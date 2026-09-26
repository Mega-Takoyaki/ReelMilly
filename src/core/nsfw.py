"""NSFW自動仕分け: ADR-0009に基づきMarqo/nsfw-image-detection-384を使う。

重い依存関係(torch/timm/opencv-python)はこのモジュールの import 時には
読み込まず、実際の推論を行うタイミングまで遅延させる。閾値判定やフレーム
サンプリング間隔の計算といった純粋なロジックは分離してあり、依存関係なし
にテストできる。

注意: Marqo/nsfw-image-detection-384 は SFW/NSFW の二値分類器であるため、
本モジュールが出す `rating` も "sfw" / "nsfw" の二値にとどまる。ADR-0006の
`content_rating`（sfw/suggestive/explicit の3値）への変換は行わず、人間が
最終確認時に確定させる（ADR-0008）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import NsfwConfig

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


@dataclass
class NsfwResult:
    rating: str  # "sfw" | "nsfw"（参考値。確定値はcontent_rating、ADR-0008参照）
    confidence: float


def rating_from_score(score: float, threshold: float) -> str:
    return "nsfw" if score >= threshold else "sfw"


def frame_sample_interval(fps: float, interval_seconds: float) -> int:
    """フレームサンプリング間隔(フレーム数)を計算する。fpsが取得できない場合は30を仮定する。"""
    effective_fps = fps if fps and fps > 0 else 30.0
    return max(int(effective_fps * interval_seconds), 1)


class NsfwClassifier:
    """Marqo/nsfw-image-detection-384をロードし、画像・動画を判定する。

    torch/timm/opencv-pythonのインポートとモデルロードは初回呼び出し時まで
    遅延する。単体テストでは `_classify_pil_image` をモックすることで、重い
    依存関係なしにロジックを検証できる。
    """

    def __init__(self, config: NsfwConfig):
        self._config = config
        self._model = None
        self._transform = None
        self._class_names = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import timm
        import torch

        model = timm.create_model(f"hf_hub:{self._config.model}", pretrained=True).eval()
        cfg = timm.data.resolve_model_data_config(model)
        transform = timm.data.create_transform(**cfg, is_training=False)
        class_names = model.pretrained_cfg["label_names"]

        self._model = model
        self._transform = transform
        self._class_names = class_names
        self._torch = torch

    def _classify_pil_image(self, img) -> NsfwResult:
        self._ensure_loaded()
        with self._torch.no_grad():
            probs = self._model(self._transform(img).unsqueeze(0)).softmax(dim=-1)[0]
        nsfw_idx = self._class_names.index("NSFW")
        score = probs[nsfw_idx].item()
        return NsfwResult(rating=rating_from_score(score, self._config.threshold), confidence=score)

    def classify_image_path(self, image_path: Path) -> NsfwResult:
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        return self._classify_pil_image(img)

    def classify_video_path(self, video_path: Path) -> NsfwResult:
        import cv2
        from PIL import Image

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        interval = frame_sample_interval(fps, self._config.video_frame_interval_seconds)

        max_score = 0.0
        idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if idx % interval == 0:
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    result = self._classify_pil_image(img)
                    max_score = max(max_score, result.confidence)
                idx += 1
        finally:
            cap.release()

        return NsfwResult(rating=rating_from_score(max_score, self._config.threshold), confidence=max_score)

    def classify(self, media_path: Path) -> NsfwResult:
        if media_path.suffix.lower() in VIDEO_EXTENSIONS:
            return self.classify_video_path(media_path)
        return self.classify_image_path(media_path)
