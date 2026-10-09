"""矩形範囲のぼかし・モザイク(静止画と動画)。範囲は動画の最初から最後まで、同じ位置に固定(被写体への追従はしない)。

- 範囲は、画像(動画)の幅・高さに対する割合(0〜1)の矩形`{x, y, w, h}`で持つ(解像度に依らず、同じ範囲を指す)
- 強さ(1〜10)は、画像の長い辺に対する割合で、効き具合を決める(大きい画像でも、小さい画像でも、見た目の強さがそろう)
- 静止画はPillow、動画はffmpeg(`core.ffmpeg.mask_video`)で処理する
"""
from __future__ import annotations

from pathlib import Path

STYLES = {"blur": "ぼかし", "mosaic": "モザイク"}
MAX_REGIONS = 8
MIN_SIDE = 0.01  # 範囲の最小の大きさ(幅・高さに対する割合)。これより小さいものは、無視する
STRENGTH_RANGE = (1, 10)
DEFAULT_STRENGTH = 5


class MaskError(ValueError):
    """範囲・強さが正しくない、または画像を処理できないとき。利用者に伝える理由つき。"""


def clean_params(regions, style, strength) -> dict:
    """APIの入力を検証して、保存する形(`regions`/`style`/`strength`)にそろえる。"""
    if style not in STYLES:
        raise MaskError("種類は、ぼかし(blur)かモザイク(mosaic)です")
    try:
        strength = int(strength)
    except (TypeError, ValueError) as exc:
        raise MaskError("強さは、1〜10の数にしてください") from exc
    if not STRENGTH_RANGE[0] <= strength <= STRENGTH_RANGE[1]:
        raise MaskError("強さは、1〜10の数にしてください")
    if not isinstance(regions, list) or not regions:
        raise MaskError("ぼかす範囲を、1つ以上、指定してください")
    if len(regions) > MAX_REGIONS:
        raise MaskError(f"範囲は、{MAX_REGIONS}つまでです")
    cleaned = []
    for r in regions:
        try:
            x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
        except (TypeError, ValueError, KeyError) as exc:
            raise MaskError("範囲の指定が正しくありません") from exc
        x0, y0 = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        x1, y1 = min(max(x + w, 0.0), 1.0), min(max(y + h, 0.0), 1.0)
        if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
            continue
        cleaned.append({"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)})
    if not cleaned:
        raise MaskError("範囲が小さすぎます。もう少し大きく囲んでください")
    return {"regions": cleaned, "style": style, "strength": strength}


def summarize(params: dict) -> str:
    return f"{STYLES.get(params['style'], params['style'])} {len(params['regions'])}か所・強さ{params['strength']}"


def blur_sigma(strength: int, long_side: int) -> float:
    return max(2.0, strength * long_side / 300)


def mosaic_block(strength: int, long_side: int) -> int:
    return max(4, round(strength * long_side / 160))


def pixel_regions(regions: list[dict], width: int, height: int, even: bool = False) -> list[tuple[int, int, int, int]]:
    """割合の矩形を、ピクセルの(x, y, w, h)にする。動画(yuv420)では、偶数にそろえる(`even=True`)。"""
    out = []
    for r in regions:
        x0, y0 = round(r["x"] * width), round(r["y"] * height)
        x1, y1 = round((r["x"] + r["w"]) * width), round((r["y"] + r["h"]) * height)
        if even:
            x0, y0 = x0 // 2 * 2, y0 // 2 * 2
            x1, y1 = min(width, (x1 + 1) // 2 * 2), min(height, (y1 + 1) // 2 * 2)
        x1, y1 = min(x1, width), min(y1, height)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def apply_image(src: Path, dest: Path, params: dict) -> None:
    """静止画の指定範囲に、ぼかし・モザイクをかけて`dest`に書く(元のファイルは変えない)。"""
    from PIL import Image, ImageFilter

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        if getattr(img, "is_animated", False):
            raise MaskError("アニメーション画像は、ぼかし・モザイクに未対応です")
        img.load()
        work = img.convert("RGB") if img.mode not in ("RGB", "RGBA", "L") else img.copy()
        width, height = work.size
        long_side = max(width, height)
        boxes = pixel_regions(params["regions"], width, height)
        if not boxes:
            raise MaskError("範囲が小さすぎます")
        for x, y, w, h in boxes:
            part = work.crop((x, y, x + w, y + h))
            if params["style"] == "blur":
                part = part.filter(ImageFilter.GaussianBlur(blur_sigma(params["strength"], long_side)))
            else:
                block = mosaic_block(params["strength"], long_side)
                small = part.resize((max(1, round(w / block)), max(1, round(h / block))), Image.BOX)
                part = small.resize((w, h), Image.NEAREST)
            work.paste(part, (x, y))
        fmt = (img.format or dest.suffix.lstrip(".")).upper()
        options = {"quality": 95, "subsampling": 0} if fmt in ("JPEG", "JPG") else {}
        if fmt in ("JPEG", "JPG") and work.mode == "RGBA":
            work = work.convert("RGB")
        work.save(dest, format="JPEG" if fmt == "JPG" else fmt, **options)
