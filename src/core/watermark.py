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

# 挿入位置。3x3の格子 + 全面に繰り返す"tile"
POSITIONS = {
    "top-left": "左上", "top-center": "上", "top-right": "右上",
    "middle-left": "左", "center": "中央", "middle-right": "右",
    "bottom-left": "左下", "bottom-center": "下", "bottom-right": "右下",
    "tile": "全面に繰り返す",
}

DEFAULTS = {
    "text": "",
    "position": "bottom-right",
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


def clean_params(text, position, opacity, size) -> dict:
    """入力値を検証・補正する。文字列が空なら例外。"""
    text = (text or "").strip()
    if not text:
        raise WatermarkError("透かしの文字列を入力してください")
    if len(text) > 100:
        raise WatermarkError("透かしの文字列は100文字以内にしてください")
    if position not in POSITIONS:
        raise WatermarkError("挿入位置が正しくありません")
    try:
        opacity = min(max(float(opacity), OPACITY_RANGE[0]), OPACITY_RANGE[1])
        size = min(max(float(size), SIZE_RANGE[0]), SIZE_RANGE[1])
    except (TypeError, ValueError) as exc:
        raise WatermarkError("濃さ・大きさが数値ではありません") from exc
    return {"text": text, "position": position, "opacity": opacity, "size": size}


def render(image: Image.Image, text: str, position: str, opacity: float, size: float) -> Image.Image:
    """透かしを重ねた画像(RGB)を返す。opacityは%、sizeは短辺に対する文字高さの%。"""
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

    if position == "tile":
        step_x, step_y = tw + margin * 4, th + margin * 5
        row = 0
        for y in range(margin, height, step_y):
            offset = (step_x // 2) if row % 2 else 0
            for x in range(margin - offset, width, step_x):
                stamp(x, y)
            row += 1
    else:
        vertical, _, horizontal = position.partition("-")
        if position == "center":
            vertical, horizontal = "middle", "center"
        elif horizontal == "":
            horizontal = "center"
        x = {"left": margin, "center": (width - tw) // 2, "right": width - tw - margin}[horizontal]
        y = {"top": margin, "middle": (height - th) // 2, "bottom": height - th - margin}[vertical]
        stamp(x, y)

    return Image.alpha_composite(base, layer).convert("RGB")


def output_path(src: Path) -> Path:
    return src.with_name(f"watermarked{src.suffix.lower()}")


def apply_to_file(src: Path, text: str, position: str, opacity: float, size: float) -> Path:
    """`src`に透かしを入れた`watermarked.<拡張子>`を同じフォルダへ作り、そのパスを返す。"""
    if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise WatermarkError(f"{src.suffix} は透かしの挿入に対応していません（動画は未対応です）")
    dst = output_path(src)
    with Image.open(src) as image:
        image.load()
        out = render(image, text, position, opacity, size)
        suffix = dst.suffix
        if suffix in (".jpg", ".jpeg"):
            out.save(dst, quality=95, subsampling=0)
        elif suffix == ".webp":
            out.save(dst, quality=95)
        else:
            out.save(dst)
    return dst


def preview_jpeg(src: Path, text: str, position: str, opacity: float, size: float, max_side: int = 900) -> bytes:
    """設定の確認用に、縮小した透かし入りプレビュー(JPEG)を返す。保存はしない。"""
    with Image.open(src) as image:
        image.load()
        scale = max_side / max(image.size)
        if scale < 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
        out = render(image, text, position, opacity, size)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
