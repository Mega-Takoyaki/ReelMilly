"""テロップ・スタンプ(ロゴ・図柄)の挿入。静止画も動画も、同じ指定でできる。

- 1つの加工版に、複数の「レイヤー」を重ねられる。レイヤーは `type: text`(テロップ)か `type: stamp`(スタンプ)
- 位置は、画面の大きさに対する割合(中心の`x`,`y`: 0〜1)、大きさは、テロップが「画面の高さに対する文字の高さ」、スタンプが「画面の幅に対する幅」の割合で持つ
  (解像度が違っても、同じ見た目になる)
- どのレイヤーも、まず透明なPNG(Pillowで描画)にする。静止画はそれを貼り、動画はffmpegのoverlayで重ねる(`core.ffmpeg.overlay_video`)。
  プレビューも同じ描画を使うので、画面で見たとおりに仕上がる
- 動画では、レイヤーごとに、表示する時間帯と、動き(右→左・左→右・上→下・下→上に流れる。繰り返しも可)を指定できる
"""
from __future__ import annotations

import io
import json
import math
import re
import sqlite3
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from core import fonts, stamps

MAX_LAYERS = 12
MAX_TEXT = 200
ANIMS = {"none": "固定", "scroll_left": "右から左へ流れる", "scroll_right": "左から右へ流れる", "scroll_up": "下から上へ流れる", "scroll_down": "上から下へ流れる"}
ALIGNS = ("left", "center", "right")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class OverlayError(ValueError):
    """指定が正しくない、またはフォント・スタンプが無いとき。利用者に伝える理由つき。"""


def _num(value, name, lo, hi, default):
    if value is None or value == "":
        return default
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise OverlayError(f"{name}は、数で指定してください") from exc
    return min(max(v, lo), hi)


def _color(value, default):
    if value is None or value == "":
        return default
    if not isinstance(value, str) or not _COLOR.match(value):
        raise OverlayError("色は、#rrggbbの形で指定してください")
    return value.lower()


def clean_layer(conn: sqlite3.Connection, raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise OverlayError("レイヤーの指定が正しくありません")
    kind = raw.get("type")
    if kind not in ("text", "stamp"):
        raise OverlayError("レイヤーの種類は、テロップ(text)かスタンプ(stamp)です")
    layer = {
        "type": kind,
        "x": _num(raw.get("x"), "位置", 0, 1, 0.5),
        "y": _num(raw.get("y"), "位置", 0, 1, 0.5),
        "rotation": _num(raw.get("rotation"), "角度", -180, 180, 0),
        "opacity": _num(raw.get("opacity"), "不透明度", 0.05, 1, 1),
    }
    anim = raw.get("anim") if isinstance(raw.get("anim"), dict) else {}
    anim_type = anim.get("type", "none")
    if anim_type not in ANIMS:
        raise OverlayError("動きの種類が正しくありません")
    end = anim.get("end")
    layer["anim"] = {
        "type": anim_type,
        "cycle": _num(anim.get("cycle"), "流れる秒数", 0.5, 300, 6),
        "loop": bool(anim.get("loop", True)),
        "start": _num(anim.get("start"), "表示の開始", 0, 36000, 0),
        "end": None if end in (None, "") else _num(end, "表示の終了", 0.1, 36000, 0.1),
        "fade_in": _num(anim.get("fade_in"), "フェードイン", 0, 30, 0),
        "fade_out": _num(anim.get("fade_out"), "フェードアウト", 0, 30, 0),
    }
    if layer["anim"]["end"] is not None and layer["anim"]["end"] <= layer["anim"]["start"]:
        raise OverlayError("表示の終了は、開始より後にしてください")
    if kind == "stamp":
        stamp_id = raw.get("stamp")
        if not stamps.exists(conn, stamp_id):
            raise OverlayError("スタンプが見つかりません")
        layer.update(stamp=stamp_id, size=_num(raw.get("size"), "大きさ", 0.02, 1.5, 0.2))
        return layer
    text = str(raw.get("text") or "").replace("\r\n", "\n")
    if not text.strip():
        raise OverlayError("テロップの文字を入力してください")
    if len(text) > MAX_TEXT:
        raise OverlayError(f"テロップは、{MAX_TEXT}文字までです")
    font_id = raw.get("font") or fonts.DEFAULT_FONT
    if font_id not in {f["id"] for f in fonts.available(fonts.state_dir(conn))}:
        raise OverlayError("そのフォントは使えません")
    align = raw.get("align", "center")
    layer.update(
        text=text, font=font_id, size=_num(raw.get("size"), "文字の大きさ", 0.01, 0.5, 0.07),
        bold=bool(raw.get("bold")), italic=bool(raw.get("italic")),
        fill=_color(raw.get("fill"), "#ffffff"), stroke_color=_color(raw.get("stroke_color"), "#000000"),
        stroke_width=_num(raw.get("stroke_width"), "縁の太さ", 0, 0.4, 0.08),
        shadow=bool(raw.get("shadow")), shadow_color=_color(raw.get("shadow_color"), "#000000"),
        shadow_dx=_num(raw.get("shadow_dx"), "影の位置", -0.5, 0.5, 0.06), shadow_dy=_num(raw.get("shadow_dy"), "影の位置", -0.5, 0.5, 0.06),
        shadow_blur=_num(raw.get("shadow_blur"), "影のぼかし", 0, 0.5, 0.04),
        bg=bool(raw.get("bg")), bg_color=_color(raw.get("bg_color"), "#000000"), bg_opacity=_num(raw.get("bg_opacity"), "背景の濃さ", 0, 1, 0.6),
        align=align if align in ALIGNS else "center",
    )
    return layer


def clean_params(conn: sqlite3.Connection, layers) -> dict:
    if not isinstance(layers, list) or not layers:
        raise OverlayError("テロップかスタンプを、1つ以上、追加してください")
    if len(layers) > MAX_LAYERS:
        raise OverlayError(f"レイヤーは、{MAX_LAYERS}個までです")
    return {"layers": [clean_layer(conn, l) for l in layers]}


def summarize(params: dict) -> str:
    texts = [l for l in params["layers"] if l["type"] == "text"]
    marks = len(params["layers"]) - len(texts)
    parts = []
    if texts:
        first = texts[0]["text"].replace("\n", " ")
        parts.append(f"テロップ「{first[:12]}{'…' if len(first) > 12 else ''}」" + (f"ほか{len(texts) - 1}件" if len(texts) > 1 else ""))
    if marks:
        parts.append(f"スタンプ{marks}件")
    moving = any(l["anim"]["type"] != "none" for l in params["layers"])
    fading = any(l["anim"]["fade_in"] or l["anim"]["fade_out"] for l in params["layers"])
    return "＋".join(parts) + ("（動きあり）" if moving else "（フェードあり）" if fading else "")


def has_motion_or_time(params: dict) -> bool:
    return any(l["anim"]["type"] != "none" or l["anim"]["start"] > 0 or l["anim"]["end"] is not None for l in params["layers"])


# --------------------------------------------------------------------------- 描画
def _hex(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), alpha)


def _shear(img: Image.Image, slant: float = 0.22) -> Image.Image:
    w, h = img.size
    extra = int(h * slant)
    return img.transform((w + extra, h), Image.AFFINE, (1, slant, -extra, 0, 1, 0), Image.BICUBIC)


def _render_text(conn: sqlite3.Connection, layer: dict, width: int, height: int) -> Image.Image:
    px = max(6, round(layer["size"] * height))
    font, real_bold = fonts.load(layer["font"], px, layer["bold"], fonts.state_dir(conn))
    outline = round(layer["stroke_width"] * px)
    extra = max(1, round(px * 0.035)) if layer["bold"] and not real_bold else 0  # 太字ファイルが無いときは、輪郭を太らせて擬似的に太くする
    spacing = round(px * 0.2)
    text = layer["text"]
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    left, top, right, bottom = probe.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align=layer["align"], stroke_width=outline + extra)
    left, top, right, bottom = math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)
    tw, th = right - left, bottom - top
    sdx, sdy = round(layer["shadow_dx"] * px), round(layer["shadow_dy"] * px)
    sblur = round(layer["shadow_blur"] * px)
    bg_pad = round(px * 0.3) if layer["bg"] else 0
    pad = outline + extra + bg_pad + 2
    if layer["shadow"]:
        pad += max(abs(sdx), abs(sdy)) + sblur * 3
    size = (tw + pad * 2, th + pad * 2)
    origin = (pad - left, pad - top)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))

    if layer["bg"]:
        box = Image.new("RGBA", size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        inset = pad - bg_pad
        bd.rounded_rectangle((inset, inset, size[0] - inset - 1, size[1] - inset - 1), radius=round(px * 0.25), fill=_hex(layer["bg_color"], round(layer["bg_opacity"] * 255)))
        canvas = Image.alpha_composite(canvas, box)

    if layer["shadow"]:
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).multiline_text((origin[0] + sdx, origin[1] + sdy), text, font=font, fill=255, spacing=spacing, align=layer["align"], stroke_width=outline + extra)
        if sblur:
            mask = mask.filter(ImageFilter.GaussianBlur(sblur))
        shadow = Image.new("RGBA", size, _hex(layer["shadow_color"]))
        shadow.putalpha(mask)
        canvas = Image.alpha_composite(canvas, shadow)

    body = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    if outline > 0:
        d.multiline_text(origin, text, font=font, fill=_hex(layer["stroke_color"]), spacing=spacing, align=layer["align"],
                         stroke_width=outline + extra, stroke_fill=_hex(layer["stroke_color"]))
    d.multiline_text(origin, text, font=font, fill=_hex(layer["fill"]), spacing=spacing, align=layer["align"],
                     stroke_width=extra, stroke_fill=_hex(layer["fill"]))
    canvas = Image.alpha_composite(canvas, body)
    return _shear(canvas) if layer["italic"] else canvas


def _render_stamp(conn: sqlite3.Connection, layer: dict, width: int, height: int) -> Image.Image:
    try:
        img = stamps.load(conn, layer["stamp"])
    except stamps.StampError as exc:
        raise OverlayError(str(exc)) from exc
    w = max(2, round(layer["size"] * width))
    h = max(2, round(img.height * w / img.width))
    return img.resize((w, h), Image.LANCZOS)


def render_layer(conn: sqlite3.Connection, layer: dict, width: int, height: int) -> Image.Image:
    """レイヤー1つを、透明な背景のRGBA画像にする(回転・不透明度を反映済み)。`width`,`height`は、載せる先の画像の大きさ。"""
    img = _render_text(conn, layer, width, height) if layer["type"] == "text" else _render_stamp(conn, layer, width, height)
    if layer["rotation"]:
        img = img.rotate(-layer["rotation"], expand=True, resample=Image.BICUBIC)
    if layer["opacity"] < 1:
        alpha = img.getchannel("A").point(lambda a: round(a * layer["opacity"]))
        img.putalpha(alpha)
    return img


def render_png(conn: sqlite3.Connection, layer: dict, width: int, height: int) -> bytes:
    buf = io.BytesIO()
    render_layer(conn, layer, width, height).save(buf, format="PNG")
    return buf.getvalue()


def apply_image(conn: sqlite3.Connection, src: Path, dest: Path, params: dict) -> None:
    """静止画に、レイヤーを重ねて`dest`に書く(元のファイルは変えない)。動きの指定は、無視する(静止画のため)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        if getattr(img, "is_animated", False):
            raise OverlayError("アニメーション画像は、未対応です")
        img.load()
        base = img.convert("RGBA")
        fmt = (img.format or dest.suffix.lstrip(".")).upper()
    width, height = base.size
    for layer in params["layers"]:
        piece = render_layer(conn, layer, width, height)
        pos = (round(layer["x"] * width - piece.width / 2), round(layer["y"] * height - piece.height / 2))
        base.paste(piece, pos, piece)
    if fmt in ("JPEG", "JPG"):
        base.convert("RGB").save(dest, format="JPEG", quality=95, subsampling=0)
    elif fmt in ("PNG", "WEBP"):
        base.save(dest, format=fmt)
    else:
        base.convert("RGB").save(dest, format="PNG" if dest.suffix.lower() == ".png" else "JPEG")


# --------------------------------------------------------------------------- 保存したスタイル・テンプレート
# スタイル: テロップ1つの見た目(文字・縁・影・帯など。文字の内容と位置は含まない)。
# テンプレート: レイヤーの組み合わせ全体(位置・動きも含む)。名前をつけて保存し、あとから読み込んだり、複数の作品へ一括で適用できる。
STYLE_FIELDS = ("font", "size", "bold", "italic", "fill", "stroke_color", "stroke_width", "shadow", "shadow_color", "shadow_dx", "shadow_dy",
                "shadow_blur", "bg", "bg_color", "bg_opacity", "align", "opacity", "rotation")
MAX_SAVED = 40


def _store_path(conn: sqlite3.Connection) -> Path:
    return fonts.state_dir(conn) / "overlay_presets.json"


def load_store(conn: sqlite3.Connection) -> dict:
    try:
        data = json.loads(_store_path(conn).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"styles": list(data.get("styles", [])), "templates": list(data.get("templates", []))}


def _write_store(conn: sqlite3.Connection, store: dict) -> None:
    path = _store_path(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _name(value) -> str:
    name = str(value or "").strip()[:30]
    if not name:
        raise OverlayError("名前を入力してください")
    return name


def save_style(conn: sqlite3.Connection, name, raw_layer: dict) -> dict:
    """テロップの見た目を、名前をつけて保存する(同じ名前は、上書き)。"""
    layer = clean_layer(conn, raw_layer)
    if layer["type"] != "text":
        raise OverlayError("スタイルとして保存できるのは、テロップだけです")
    item = {"id": uuid.uuid4().hex[:10], "name": _name(name), "style": {k: layer[k] for k in STYLE_FIELDS}}
    store = load_store(conn)
    store["styles"] = [s for s in store["styles"] if s["name"] != item["name"]]
    if len(store["styles"]) >= MAX_SAVED:
        raise OverlayError(f"保存できるスタイルは、{MAX_SAVED}個までです。不要なものを削除してください")
    store["styles"].append(item)
    _write_store(conn, store)
    return item


def save_template(conn: sqlite3.Connection, name, raw_layers) -> dict:
    """レイヤーの組み合わせを、名前をつけて保存する(同じ名前は、上書き)。"""
    params = clean_params(conn, raw_layers)
    item = {"id": uuid.uuid4().hex[:10], "name": _name(name), "layers": params["layers"]}
    store = load_store(conn)
    store["templates"] = [t for t in store["templates"] if t["name"] != item["name"]]
    if len(store["templates"]) >= MAX_SAVED:
        raise OverlayError(f"保存できるテンプレートは、{MAX_SAVED}個までです。不要なものを削除してください")
    store["templates"].append(item)
    _write_store(conn, store)
    return item


def delete_saved(conn: sqlite3.Connection, kind: str, item_id: str) -> bool:
    store = load_store(conn)
    before = len(store[kind])
    store[kind] = [i for i in store[kind] if i["id"] != item_id]
    if len(store[kind]) == before:
        return False
    _write_store(conn, store)
    return True


def get_template(conn: sqlite3.Connection, template_id: str) -> dict | None:
    return next((t for t in load_store(conn)["templates"] if t["id"] == template_id), None)
