"""薄い透かし(ウォーターマーク)の文字を画像へ挿入する。

元のファイルは変えず、透かし入りの別ファイル(`<作品フォルダ>/watermarked.<拡張子>`)を作る。
投稿(Fanvue等)は、透かし入りがあればそれを使う。
透かしは「簡単には見えない」薄さが既定で、文字の白(薄い影つき)を低い不透明度で重ねる。
動画は未対応(ffmpegが無い環境でも使えるよう、まず画像だけを対象にしている)。
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 挿入位置。5x5の格子(キーは"r<行>c<列>"、行・列とも0〜4)と、全面に繰り返す"tile"。複数同時に選べる
ROW_LABELS = ["上端", "上寄り", "中段", "下寄り", "下端"]
COL_LABELS = ["左端", "左寄り", "中央", "右寄り", "右端"]
GRID_KEYS = [f"r{r}c{c}" for r in range(5) for c in range(5)]
TILE = "tile"
POSITIONS = {f"r{r}c{c}": f"{ROW_LABELS[r]}・{COL_LABELS[c]}" for r in range(5) for c in range(5)}
POSITIONS[TILE] = "全面に繰り返す"
# 3x3だった頃の位置名(保存済みの設定・作品の記録)を、5x5の同じ場所へ読み替える
LEGACY_POSITIONS = {
    "top-left": "r0c0", "top-center": "r0c2", "top-right": "r0c4",
    "middle-left": "r2c0", "center": "r2c2", "middle-right": "r2c4",
    "bottom-left": "r4c0", "bottom-center": "r4c2", "bottom-right": "r4c4",
}

DEFAULTS = {
    "text": "@GirlAidol",
    "positions": ["r4c4"],  # 右下
    "opacity": 16,  # 不透明度(%)。10〜20程度が「簡単には見えない」薄さ
    "size": 3.0,  # 文字の高さ(画像の短辺に対する%)
}
OPACITY_RANGE = (3, 60)
SIZE_RANGE = (1.0, 12.0)

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class WatermarkError(Exception):
    pass


def _font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    return ImageFont.load_default(size_px)


def normalize_positions(value) -> list[str]:
    """位置の指定(文字列・カンマ区切り・リスト)を、有効なキーのリストにそろえる。旧3x3の名前も受け付ける。"""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    result: list[str] = []
    for item in items:
        key = LEGACY_POSITIONS.get(str(item).strip(), str(item).strip())
        if key in POSITIONS and key not in result:
            result.append(key)
    return result


def describe_positions(value) -> str:
    """画面に出す位置の説明(例: 「中段・中央」「3か所」「全面に繰り返す」)。"""
    keys = normalize_positions(value)
    grid = [k for k in keys if k != TILE]
    parts = []
    if len(grid) == 1:
        parts.append(POSITIONS[grid[0]])
    elif len(grid) > 1:
        parts.append(f"{len(grid)}か所")
    if TILE in keys:
        parts.append(POSITIONS[TILE])
    return "＋".join(parts) or "位置なし"


def clean_params(text, positions, opacity, size) -> dict:
    """入力値を検証・補正する。文字列や位置が空なら例外。"""
    text = (text or "").strip()
    if not text:
        raise WatermarkError("透かしの文字列を入力してください")
    if len(text) > 100:
        raise WatermarkError("透かしの文字列は100文字以内にしてください")
    keys = normalize_positions(positions)
    if not keys:
        raise WatermarkError("挿入位置を1か所以上選んでください")
    try:
        opacity = min(max(float(opacity), OPACITY_RANGE[0]), OPACITY_RANGE[1])
        size = min(max(float(size), SIZE_RANGE[0]), SIZE_RANGE[1])
    except (TypeError, ValueError) as exc:
        raise WatermarkError("濃さ・大きさが数値ではありません") from exc
    return {"text": text, "positions": keys, "opacity": opacity, "size": size}


def render(image: Image.Image, text: str, positions, opacity: float, size: float) -> Image.Image:
    """透かしを重ねた画像(RGB)を返す。positionsは複数指定でき、それぞれの場所に入れる。

    opacityは%、sizeは短辺に対する文字高さの%。
    """
    keys = normalize_positions(positions)
    base = image.convert("RGBA")
    width, height = base.size
    font_px = max(10, round(min(width, height) * size / 100))
    font = _font(font_px)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    margin = max(8, round(font_px * 0.8))

    alpha = round(255 * opacity / 100)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def stamp(x: int, y: int) -> None:
        # 明るい背景でも暗い背景でも読めるよう、白い文字にごく薄い影を添える
        shadow = max(1, font_px // 18)
        draw.text((x - left + shadow, y - top + shadow), text, font=font, fill=(0, 0, 0, round(alpha * 0.55)))
        draw.text((x - left, y - top), text, font=font, fill=(255, 255, 255, alpha))

    span_x = max(0, width - tw - 2 * margin)
    span_y = max(0, height - th - 2 * margin)
    for key in keys:
        if key == TILE:
            step_x, step_y = tw + margin * 4, th + margin * 5
            row = 0
            for y in range(margin, height, step_y):
                offset = (step_x // 2) if row % 2 else 0
                for x in range(margin - offset, width, step_x):
                    stamp(x, y)
                row += 1
        else:
            r, c = int(key[1]), int(key[3])
            stamp(margin + round(span_x * c / 4), margin + round(span_y * r / 4))

    return Image.alpha_composite(base, layer).convert("RGB")


def output_path(src: Path) -> Path:
    return src.with_name(f"watermarked{src.suffix.lower()}")


def apply_to_file(src: Path, text: str, positions, opacity: float, size: float) -> Path:
    """`src`に透かしを入れた`watermarked.<拡張子>`を同じフォルダへ作り、そのパスを返す。"""
    if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise WatermarkError(f"{src.suffix} は透かしの挿入に対応していません（動画は未対応です）")
    dst = output_path(src)
    with Image.open(src) as image:
        image.load()
        out = render(image, text, positions, opacity, size)
        suffix = dst.suffix
        if suffix in (".jpg", ".jpeg"):
            out.save(dst, quality=95, subsampling=0)
        elif suffix == ".webp":
            out.save(dst, quality=95)
        else:
            out.save(dst)
    return dst


def preview_jpeg(src: Path, text: str, positions, opacity: float, size: float, max_side: int = 900) -> bytes:
    """設定の確認用に、縮小した透かし入りプレビュー(JPEG)を返す。保存はしない。"""
    with Image.open(src) as image:
        image.load()
        scale = max_side / max(image.size)
        if scale < 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
        out = render(image, text, positions, opacity, size)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
