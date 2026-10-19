"""センシティブな領域(性器など)の自動検出。検出した範囲に、ぼかし・モザイクをかける加工版を作るための元になる。

- 検出は、このPC上のモデル(NudeNet。ONNX、CPUで動く)で行う。画像は外部に送らない。`pip install -e ".[detect]"`で入る
- 範囲は、画像(動画)の幅・高さに対する割合の矩形(`core.mask`と同じ形)で返す
- 動画は、一定間隔でコマを取り出して検出し、全部のコマの範囲を重ね合わせて、固定の範囲にする(`core.mask`は、動画で範囲が動かない)
- 画像の向きは、`core.mask.apply_image`と同じく、EXIFの回転を反映しない生の画素の向きで扱う
"""
from __future__ import annotations

from pathlib import Path

from core import mask

# 対象の部位 → NudeNetのラベル(「COVERED」=隠れているものは対象外)
PARTS = {
    "genitals": ("FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED"),
    "anus": ("ANUS_EXPOSED",),
    "breasts": ("FEMALE_BREAST_EXPOSED",),
    "buttocks": ("BUTTOCKS_EXPOSED",),
}
PART_LABELS = {"genitals": "性器", "anus": "肛門", "breasts": "胸", "buttocks": "臀部"}
DEFAULT_PARTS = ("genitals", "anus")
SENSITIVITY_RANGE = (1, 10)
DEFAULT_SENSITIVITY = 7
PADDING_RANGE = (0, 100)  # 検出した範囲を、四方に広げる割合(%。検出した範囲の大きさに対して)
DEFAULT_PADDING = 25
INTERVAL_RANGE = (0.2, 30.0)  # 動画の、コマを取り出す間隔(秒)
DEFAULT_INTERVAL = 1.0
MAX_FRAMES = 40  # 動画で調べるコマの上限(長い動画は、間隔を広げて、この数に収める)

_detector = None


class DetectError(RuntimeError):
    """検出モデルが使えない、またはファイルを読めないとき。利用者に伝える理由つき。"""


def available() -> bool:
    try:
        import nudenet  # noqa: F401
    except Exception:  # noqa: BLE001 - 入っていない・読み込めない、のどちらも「使えない」
        return False
    return True


def threshold_for(sensitivity: int) -> float:
    """感度(1〜10。大きいほど、見逃しにくく、誤検出が増える)を、検出の確信度のしきい値にする。"""
    return round(0.95 - 0.085 * sensitivity, 3)


def clean_options(options: dict | None) -> dict:
    """APIの入力を検証して、保存する形にそろえる。"""
    options = options or {}
    parts = options.get("parts", list(DEFAULT_PARTS))
    if not isinstance(parts, list) or not parts or any(p not in PARTS for p in parts):
        raise mask.MaskError("検出する部位を、1つ以上選んでください")
    try:
        sensitivity = int(options.get("sensitivity", DEFAULT_SENSITIVITY))
        padding = int(options.get("padding", DEFAULT_PADDING))
        interval = float(options.get("interval", DEFAULT_INTERVAL))
    except (TypeError, ValueError) as exc:
        raise mask.MaskError("検出の設定が正しくありません") from exc
    if not SENSITIVITY_RANGE[0] <= sensitivity <= SENSITIVITY_RANGE[1]:
        raise mask.MaskError("感度は、1〜10の数にしてください")
    if not PADDING_RANGE[0] <= padding <= PADDING_RANGE[1]:
        raise mask.MaskError("範囲の広げ幅は、0〜100%にしてください")
    if not INTERVAL_RANGE[0] <= interval <= INTERVAL_RANGE[1]:
        raise mask.MaskError(f"動画の検査間隔は、{INTERVAL_RANGE[0]}〜{INTERVAL_RANGE[1]:g}秒にしてください")
    return {"parts": [p for p in PARTS if p in parts], "sensitivity": sensitivity, "padding": padding, "interval": interval}


def describe(options: dict) -> str:
    return "・".join(PART_LABELS[p] for p in options["parts"])


def _get_detector():
    global _detector
    if _detector is None:
        try:
            from nudenet import NudeDetector

            _detector = NudeDetector()
        except Exception as exc:  # noqa: BLE001
            raise DetectError('検出モデル(NudeNet)を使えません。`pip install -e ".[detect]"`で入れてから、アプリを再起動してください') from exc
    return _detector


def _boxes_in(frame_bgr, labels: set[str], threshold: float) -> list[tuple[float, float, float, float]]:
    """1枚の画像(BGRのnumpy配列)から、対象の部位の矩形(x0, y0, x1, y1。割合)を集める。"""
    height, width = frame_bgr.shape[:2]
    boxes = []
    for d in _get_detector().detect(frame_bgr):
        if d["class"] not in labels or d["score"] < threshold:
            continue
        x, y, w, h = d["box"]
        boxes.append((max(0.0, x / width), max(0.0, y / height), min(1.0, (x + w) / width), min(1.0, (y + h) / height)))
    return boxes


def _pad(box, padding: int):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * padding / 100, (y1 - y0) * padding / 100
    return max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _union(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def merge_boxes(boxes: list[tuple], limit: int = mask.MAX_REGIONS) -> list[tuple]:
    """重なる矩形を1つにまとめる。まだ`limit`を超えるときは、近い(まとめても余計な面積が小さい)ものから、まとめる。"""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _overlaps(boxes[i], boxes[j]):
                    boxes[i] = _union(boxes[i], boxes[j])
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])  # noqa: E731
    while len(boxes) > limit:
        _, i, j = min(
            ((area(_union(boxes[i], boxes[j])) - area(boxes[i]) - area(boxes[j]), i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes)))
        )
        boxes[i] = _union(boxes[i], boxes[j])
        del boxes[j]
    return boxes


def _to_regions(boxes: list[tuple]) -> list[dict]:
    return [{"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)} for x0, y0, x1, y1 in boxes]


def _image_bgr(path: Path):
    import numpy as np
    from PIL import Image

    try:
        with Image.open(path) as img:
            if getattr(img, "is_animated", False):
                raise mask.MaskError("アニメーション画像は、ぼかし・モザイクに未対応です")
            rgb = img.convert("RGB")
            return np.asarray(rgb)[:, :, ::-1].copy()
    except mask.MaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DetectError("画像を読めません") from exc


def _video_frames(path: Path, interval: float):
    """動画から、`interval`秒おきに(上限`MAX_FRAMES`枚)コマを取り出す(BGR)。"""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if total <= 0 or fps <= 0:
            raise DetectError("動画を読めません")
        step = max(1, round(interval * fps))
        positions = list(range(0, total, step))
        if len(positions) > MAX_FRAMES:
            positions = [positions[round(i * (len(positions) - 1) / (MAX_FRAMES - 1))] for i in range(MAX_FRAMES)]
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if ok:
                yield frame
    finally:
        cap.release()


def detect_regions(path: Path, media: str, options: dict) -> list[dict]:
    """ファイルから、対象の部位の範囲(割合の矩形)を検出する。見つからなければ空のリスト。"""
    labels = {label for p in options["parts"] for label in PARTS[p]}
    threshold = threshold_for(options["sensitivity"])
    frames = _video_frames(path, options["interval"]) if media == "video" else [_image_bgr(path)]
    boxes: list[tuple] = []
    for frame in frames:
        boxes.extend(_pad(b, options["padding"]) for b in _boxes_in(frame, labels, threshold))
    return _to_regions(merge_boxes(boxes))
