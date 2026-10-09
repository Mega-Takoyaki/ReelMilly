"""スタンプ(ロゴ・図柄)。作品の上に重ねる画像。3種類ある。

- `preset:<名前>`: このアプリが描く図形・バッジ(黒帯・ハート・星・NEW・18+など)。隠す用途と、宣伝用
- `emoji:<名前>`: 絵文字の画像(Google「Noto Emoji」、Apache License 2.0。`static/stamps/NOTICE.md`)
- `upload:<ID>`: 利用者がアップロードした画像。状態フォルダの`stamps/`に置く
"""
from __future__ import annotations

import io
import math
import re
import sqlite3
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from core import fonts

EMOJI_DIR = Path(__file__).resolve().parent / "web" / "static" / "stamps" / "emoji"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_SIDE = 2048  # これより大きい画像は、縮小して保存する
_SS = 2  # 描くときの倍率(縁をなめらかにするため、大きく描いて縮める)

EMOJI = [
    ("fire", "炎"), ("kiss-mark", "キスマーク"), ("blow-kiss", "投げキッス"), ("two-hearts", "ハート2つ"), ("red-heart", "赤いハート"),
    ("sparkling-heart", "キラキラハート"), ("beating-heart", "ドキドキ"), ("sparkles", "キラキラ"), ("star", "星"), ("ribbon", "リボン"),
    ("bikini", "ビキニ"), ("sweat-drops", "しずく"), ("peach", "もも"), ("cherry-blossom", "さくら"), ("no-under-18", "18禁"),
    ("eyes", "目"), ("heart-eyes", "ハート目"), ("hug", "ハグ"), ("smirk", "にやり"), ("devil", "小悪魔"),
    ("hundred", "100点"), ("party", "クラッカー"), ("gift", "プレゼント"), ("stop", "ストップ"), ("prohibited", "禁止"),
    ("gem", "宝石"), ("crown", "王冠"), ("moon", "月"), ("collision", "ドーン"), ("camera", "カメラ"), ("clapper", "カチンコ"),
]


class StampError(ValueError):
    pass


# --------------------------------------------------------------------------- プリセット(図形・バッジ)
def _canvas(w: int, h: int):
    img = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _done(img: Image.Image, w: int, h: int) -> Image.Image:
    return img.resize((w, h), Image.LANCZOS)


def _bar(color):
    def make():
        img, d = _canvas(480, 120)
        d.rounded_rectangle((0, 0, 480 * _SS - 1, 120 * _SS - 1), radius=18 * _SS, fill=color)
        return _done(img, 480, 120)
    return make


def _circle(color):
    def make():
        img, d = _canvas(300, 300)
        d.ellipse((0, 0, 300 * _SS - 1, 300 * _SS - 1), fill=color)
        return _done(img, 300, 300)
    return make


def _square(color):
    def make():
        img, d = _canvas(300, 300)
        d.rectangle((0, 0, 300 * _SS - 1, 300 * _SS - 1), fill=color)
        return _done(img, 300, 300)
    return make


def _heart(color, outline=None):
    def make():
        size = 300
        img, d = _canvas(size, size)
        pts = []
        for i in range(361):
            t = math.radians(i)
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append(((x + 17) / 34 * size * _SS, (14 - y) / 31 * size * _SS))
        d.polygon(pts, fill=color, outline=outline)
        return _done(img, size, size)
    return make


def _star(color, points=5, inner=0.5):
    def make():
        size = 300
        img, d = _canvas(size, size)
        c = size * _SS / 2
        pts = []
        for i in range(points * 2):
            r = c * (1 if i % 2 == 0 else inner)
            a = math.pi * i / points - math.pi / 2
            pts.append((c + r * math.cos(a), c + r * math.sin(a)))
        d.polygon(pts, fill=color)
        return _done(img, size, size)
    return make


def _sparkle(color):
    def make():
        size = 300
        img, d = _canvas(size, size)
        c = size * _SS / 2
        # アストロイド(四隅がへこんだ、4つの尖りの星)
        pts = [(c + c * math.cos(math.radians(i)) ** 3, c + c * math.sin(math.radians(i)) ** 3) for i in range(0, 360, 3)]
        d.polygon(pts, fill=color)
        return _done(img, size, size)
    return make


def _badge(text: str, fill, fg=(255, 255, 255, 255), shape="round", width=420, height=150):
    def make():
        img, d = _canvas(width, height)
        box = (0, 0, width * _SS - 1, height * _SS - 1)
        if shape == "circle":
            d.ellipse(box, fill=fill)
        else:
            d.rounded_rectangle(box, radius=height * _SS // 2 if shape == "pill" else 24 * _SS, fill=fill)
        font, _ = fonts.load(fonts.DEFAULT_FONT, int(height * _SS * (0.5 if len(text) <= 4 else 0.42)), bold=True)
        d.text((width * _SS / 2, height * _SS / 2), text, font=font, fill=fg, anchor="mm", stroke_width=_SS, stroke_fill=fg)
        return _done(img, width, height)
    return make


PRESETS = [
    ("bar-black", "黒帯", _bar((0, 0, 0, 255))),
    ("bar-white", "白帯", _bar((255, 255, 255, 255))),
    ("bar-pink", "ピンクの帯", _bar((255, 105, 180, 255))),
    ("circle-black", "黒丸", _circle((0, 0, 0, 255))),
    ("square-black", "黒四角", _square((0, 0, 0, 255))),
    ("heart-pink", "ピンクのハート", _heart((255, 92, 160, 255))),
    ("heart-red", "赤いハート", _heart((230, 30, 60, 255))),
    ("heart-white", "白いハート", _heart((255, 255, 255, 255))),
    ("star-yellow", "黄色い星", _star((255, 208, 0, 255))),
    ("star-pink", "ピンクの星", _star((255, 120, 190, 255))),
    ("sparkle-white", "キラッ（白）", _sparkle((255, 255, 255, 255))),
    ("sparkle-gold", "キラッ（金）", _sparkle((255, 215, 90, 255))),
    ("badge-new", "NEW", _badge("NEW", (230, 30, 60, 255), shape="pill")),
    ("badge-18", "18+", _badge("18+", (200, 20, 40, 255), shape="circle", width=240, height=240)),
    ("badge-free", "無料", _badge("無料", (255, 140, 0, 255), shape="pill")),
    ("badge-limited", "限定公開", _badge("限定公開", (120, 60, 200, 255), shape="pill", width=520)),
    ("badge-subscribe", "購読者限定", _badge("購読者限定", (30, 120, 220, 255), shape="pill", width=620)),
    ("badge-now", "公開中", _badge("公開中", (20, 160, 90, 255), shape="pill", width=460)),
]
_PRESET_MAP = {p[0]: p for p in PRESETS}


# --------------------------------------------------------------------------- アップロード
def stamps_dir(conn: sqlite3.Connection) -> Path:
    return fonts.state_dir(conn) / "stamps"


def list_uploads(conn: sqlite3.Connection) -> list[dict]:
    directory = stamps_dir(conn)
    out = []
    if directory.is_dir():
        for p in sorted(directory.glob("*.png"), key=lambda q: q.stat().st_mtime, reverse=True):
            name_file = p.with_suffix(".txt")
            name = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else p.stem
            out.append({"id": f"upload:{p.stem}", "label": name or p.stem, "group": "upload"})
    return out


def list_all(conn: sqlite3.Connection) -> list[dict]:
    items = [{"id": f"preset:{pid}", "label": label, "group": "preset"} for pid, label, _ in PRESETS]
    items += [{"id": f"emoji:{eid}", "label": label, "group": "emoji"} for eid, label in EMOJI if (EMOJI_DIR / f"{eid}.png").exists()]
    return items + list_uploads(conn)


def save_upload(conn: sqlite3.Connection, data: bytes, name: str) -> dict:
    """アップロードされた画像を、PNGにして保存する。画像でない・大きすぎるときは`StampError`。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise StampError("ファイルが大きすぎます(8MBまで)")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            img = img.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise StampError("画像として読めません(PNG・JPEG・WebP・GIFに対応しています)") from exc
    if max(img.size) > MAX_UPLOAD_SIDE:
        img.thumbnail((MAX_UPLOAD_SIDE, MAX_UPLOAD_SIDE), Image.LANCZOS)
    stamp_id = uuid.uuid4().hex[:12]
    directory = stamps_dir(conn)
    directory.mkdir(parents=True, exist_ok=True)
    img.save(directory / f"{stamp_id}.png", format="PNG")
    label = re.sub(r"\.[A-Za-z0-9]+$", "", (name or "").strip())[:40] or stamp_id
    (directory / f"{stamp_id}.txt").write_text(label, encoding="utf-8")
    return {"id": f"upload:{stamp_id}", "label": label, "group": "upload"}


def delete_upload(conn: sqlite3.Connection, stamp_id: str) -> bool:
    if not stamp_id.startswith("upload:") or not re.fullmatch(r"[0-9a-f]{12}", stamp_id[7:]):
        return False
    p = stamps_dir(conn) / f"{stamp_id[7:]}.png"
    existed = p.exists()
    p.unlink(missing_ok=True)
    p.with_suffix(".txt").unlink(missing_ok=True)
    return existed


# --------------------------------------------------------------------------- 読み込み
def exists(conn: sqlite3.Connection, stamp_id: str) -> bool:
    try:
        path_or_maker = _locate(conn, stamp_id)
    except StampError:
        return False
    return path_or_maker is not None


def _locate(conn: sqlite3.Connection, stamp_id: str):
    kind, _, name = (stamp_id or "").partition(":")
    if kind == "preset" and name in _PRESET_MAP:
        return _PRESET_MAP[name][2]
    if kind == "emoji" and re.fullmatch(r"[a-z0-9-]+", name) and (EMOJI_DIR / f"{name}.png").exists():
        return EMOJI_DIR / f"{name}.png"
    if kind == "upload" and re.fullmatch(r"[0-9a-f]{12}", name) and (stamps_dir(conn) / f"{name}.png").exists():
        return stamps_dir(conn) / f"{name}.png"
    raise StampError(f"スタンプが見つかりません: {stamp_id}")


def load(conn: sqlite3.Connection, stamp_id: str) -> Image.Image:
    """スタンプの画像(RGBA)。無ければ`StampError`。"""
    target = _locate(conn, stamp_id)
    if callable(target):
        return target()
    with Image.open(target) as img:
        return img.convert("RGBA")
