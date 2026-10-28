"""NSFW自動仕分け: ADR-0009に基づきMarqo/nsfw-image-detection-384を使い、露出の検出と合わせて、sfw/suggestive/explicit(ADR-0006)の3段階の案を出す。

重い依存関係(torch/timm/opencv-python)はこのモジュールの import 時には
読み込まず、実際の推論を行うタイミングまで遅延させる。閾値判定やフレーム
サンプリング間隔の計算といった純粋なロジックは分離してあり、依存関係なし
にテストできる。

Marqo/nsfw-image-detection-384 は SFW/NSFW の二値分類器で、「きわどいが露出はない」画像も
NSFW側に倒れやすい。そのため、モデルのスコア(`confidence`)だけで決めず、露出した部位の検出
(`core.autodetect`のNudeNet。入っていなければ使わない)と組み合わせて、3段階にする:

- 性器・肛門が写っている → explicit
- 胸・臀部が写っている → スコアが高ければ explicit、そうでなければ suggestive
- 露出が見つからない → スコアがしきい値以上なら suggestive、未満なら sfw

スコアが迷う帯(`REVIEW_BAND`)にあるものは、「要確認」として、人の目で見るよう促す。
結果は、あくまで参考値で、確定値はcontent_rating(人が承認、ADR-0008)。
"""
from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import NsfwConfig

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}

RATINGS = ("sfw", "suggestive", "explicit")
AUTO_TAG_NAMES = ("sfw", "nsfw", "suggestive", "explicit")  # 自動判定が付けるタグ(判定し直すとき、前のものを外す。"nsfw"は旧形式)
EXPLICIT_SCORE = 0.8  # 胸・臀部が見つかったとき、スコアがこれ以上なら explicit
REVIEW_BAND = (0.2, 0.8)  # スコアがこの範囲(以上・未満)なら「要確認」
VIDEO_TOP_K = 3  # 動画のスコアは、高いほうからこの数のコマの平均(1コマの誤検出で、動画全体が決まらないように)
MAX_VIDEO_FRAMES = 60  # 動画で調べるコマの上限(長い動画は、間隔を広げて、この数に収める)


class NsfwParamsError(ValueError):
    """しきい値などの設定が正しくないとき。利用者に伝える理由つき。"""


@dataclass(frozen=True)
class RatingParams:
    """3段階の案を決めるときの、しきい値など。設定画面で調整できる(`load_params`)。"""

    threshold: float = 0.5  # 露出が見つからないとき、スコアがこれ以上なら suggestive(未満なら sfw)
    explicit_score: float = EXPLICIT_SCORE  # 胸・臀部が見つかったとき、スコアがこれ以上なら explicit
    review_low: float = REVIEW_BAND[0]  # スコアが、この値以上、review_high未満なら「要確認」
    review_high: float = REVIEW_BAND[1]
    video_top_k: int = VIDEO_TOP_K  # 動画のスコアは、高いほうからこの数のコマの平均
    exposure_sensitivity: int = 7  # 露出の検出の感度(1〜10)

    @property
    def review_band(self) -> tuple[float, float]:
        return (self.review_low, self.review_high)


PARAM_SETTING_KEY = "nsfw_params"


def clean_params(raw: dict | None, default_threshold: float = 0.5) -> RatingParams:
    """入力を検証して、`RatingParams`にそろえる。足りない項目は既定値。正しくなければ`NsfwParamsError`。"""
    raw = raw or {}
    defaults = RatingParams(threshold=default_threshold)
    try:
        values = {
            "threshold": float(raw.get("threshold", defaults.threshold)),
            "explicit_score": float(raw.get("explicit_score", defaults.explicit_score)),
            "review_low": float(raw.get("review_low", defaults.review_low)),
            "review_high": float(raw.get("review_high", defaults.review_high)),
            "video_top_k": int(raw.get("video_top_k", defaults.video_top_k)),
            "exposure_sensitivity": int(raw.get("exposure_sensitivity", defaults.exposure_sensitivity)),
        }
    except (TypeError, ValueError) as exc:
        raise NsfwParamsError("しきい値は、数で入れてください") from exc
    for name, label in (("threshold", "suggestiveのしきい値"), ("explicit_score", "explicitのしきい値")):
        if not 0.05 <= values[name] <= 0.99:
            raise NsfwParamsError(f"{label}は、0.05〜0.99の数にしてください")
    if not 0.0 <= values["review_low"] < values["review_high"] <= 1.0:
        raise NsfwParamsError("「要確認」の幅は、下限を上限より小さく(0〜1の範囲で)してください")
    if not 1 <= values["video_top_k"] <= 20:
        raise NsfwParamsError("動画で平均するコマの数は、1〜20にしてください")
    if not 1 <= values["exposure_sensitivity"] <= 10:
        raise NsfwParamsError("露出の検出の感度は、1〜10の数にしてください")
    return RatingParams(**values)


def load_params(conn, default_threshold: float = 0.5) -> RatingParams:
    """設定で調整した値(無ければ既定)。壊れた保存値は、既定に戻す。"""
    from core import db

    raw = db.get_setting(conn, PARAM_SETTING_KEY)
    if raw:
        try:
            return clean_params(json.loads(raw), default_threshold)
        except (ValueError, TypeError):
            pass
    return RatingParams(threshold=default_threshold)


def save_params(conn, raw: dict, default_threshold: float = 0.5) -> RatingParams:
    """検証に通る値だけ保存する(通らなければ`NsfwParamsError`)。"""
    from core import db

    params = clean_params(raw, default_threshold)
    db.set_setting(conn, PARAM_SETTING_KEY, json.dumps(asdict(params)))
    return params


@dataclass
class NsfwResult:
    rating: str  # "sfw" | "suggestive" | "explicit"（参考値。確定値はcontent_rating、ADR-0008参照）
    confidence: float  # Marqoモデルの NSFW スコア(動画は、上位コマの平均)
    exposure: str | None = None  # 露出の検出結果: "genital"(性器・肛門) | "chest"(胸・臀部) | None(検出なし、または検出器なし)


def rating_from_score(score: float, threshold: float) -> str:
    """スコアだけでの二値判定(露出の検出が使えないときの下地)。"""
    return "nsfw" if score >= threshold else "sfw"


def decide_rating(score: float, exposure: str | None, threshold: float, explicit_score: float = EXPLICIT_SCORE) -> str:
    """スコアと露出の検出結果から、3段階の案を決める。"""
    if exposure == "genital":
        return "explicit"
    if exposure == "chest":
        return "explicit" if score >= explicit_score else "suggestive"
    return "suggestive" if score >= threshold else "sfw"


def needs_review(confidence: float | None, band: tuple[float, float] = REVIEW_BAND) -> bool:
    """スコアが迷う帯(`band`: 以上・未満)にあるか(人の目で確かめたほうがよいか)。未判定はFalse。"""
    return confidence is not None and band[0] <= confidence < band[1]


def top_k_mean(scores: list[float], k: int = VIDEO_TOP_K) -> float:
    """高いほうから k 個の平均(動画のスコア)。"""
    if not scores:
        return 0.0
    top = sorted(scores, reverse=True)[:k]
    return sum(top) / len(top)


def combine_exposure(flags: list[tuple[bool, bool]]) -> str | None:
    """コマごとの露出(性器・肛門、胸・臀部)の検出を、動画全体の結果にまとめる。

    1コマの誤検出で決まらないよう、調べたコマが少ない(3コマ以下)ときは1コマ、それより多いときは2コマ以上で見つかったら、とする。
    """
    need = 1 if len(flags) <= 3 else 2
    if sum(1 for genital, _ in flags if genital) >= need:
        return "genital"
    if sum(1 for _, chest in flags if chest) >= need:
        return "chest"
    return None


def frame_sample_interval(fps: float, interval_seconds: float) -> int:
    """フレームサンプリング間隔(フレーム数)を計算する。fpsが取得できない場合は30を仮定する。"""
    effective_fps = fps if fps and fps > 0 else 30.0
    return max(int(effective_fps * interval_seconds), 1)


class NsfwClassifier:
    """Marqo/nsfw-image-detection-384をロードし、画像・動画を判定する。

    torch/timm/opencv-pythonのインポートとモデルロードは初回呼び出し時まで
    遅延する。単体テストでは `_analyze_frame` をモックすることで、重い
    依存関係なしにロジックを検証できる。
    """

    def __init__(self, config: NsfwConfig):
        self._config = config
        self.params = RatingParams(threshold=config.threshold)  # 判定のたびに、設定の値へ更新される(`analysis.run_nsfw`)
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
        # 既定は、短辺を合わせて中央を切り取る(縦長の画像は、上下が判定に使われない)。全体を384x384に縮めて渡す
        cfg["crop_mode"] = "squash"
        transform = timm.data.create_transform(**cfg, is_training=False)
        class_names = model.pretrained_cfg["label_names"]

        self._model = model
        self._transform = transform
        self._class_names = class_names
        self._torch = torch

    def _score(self, img) -> float:
        """Marqoモデルの NSFW スコア(0〜1)。"""
        self._ensure_loaded()
        with self._torch.no_grad():
            probs = self._model(self._transform(img).unsqueeze(0)).softmax(dim=-1)[0]
        return probs[self._class_names.index("NSFW")].item()

    def _analyze_frame(self, img) -> tuple[float, tuple[bool, bool] | None]:
        """1枚の画像(PIL)の、(NSFWスコア, (性器・肛門, 胸・臀部)の検出)。検出器が使えなければ、検出はNone。"""
        score = self._score(img)
        from core import autodetect

        if not autodetect.available():
            return score, None
        try:
            import numpy as np

            return score, autodetect.exposure_flags(np.asarray(img.convert("RGB"))[:, :, ::-1].copy(), self.params.exposure_sensitivity)
        except Exception:  # noqa: BLE001 - 検出器の不調で、判定全体を止めない(スコアだけで判定する)
            return score, None

    def _result(self, score: float, flags: list[tuple[bool, bool]]) -> NsfwResult:
        exposure = combine_exposure(flags) if flags else None
        return NsfwResult(
            rating=decide_rating(score, exposure, self.params.threshold, self.params.explicit_score), confidence=score, exposure=exposure
        )

    def classify_image_path(self, image_path: Path) -> NsfwResult:
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        score, flags = self._analyze_frame(img)
        return self._result(score, [flags] if flags is not None else [])

    def classify_video_path(self, video_path: Path) -> NsfwResult:
        import cv2
        from PIL import Image

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        interval = frame_sample_interval(fps, self._config.video_frame_interval_seconds)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total > 0:
            interval = max(interval, math.ceil(total / MAX_VIDEO_FRAMES))  # 長い動画は、調べるコマの数を抑える

        scores: list[float] = []
        flags: list[tuple[bool, bool]] = []
        idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if idx % interval == 0:
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    score, frame_flags = self._analyze_frame(img)
                    scores.append(score)
                    if frame_flags is not None:
                        flags.append(frame_flags)
                idx += 1
        finally:
            cap.release()

        return self._result(top_k_mean(scores, self.params.video_top_k), flags)

    def classify(self, media_path: Path) -> NsfwResult:
        if media_path.suffix.lower() in VIDEO_EXTENSIONS:
            return self.classify_video_path(media_path)
        return self.classify_image_path(media_path)


def is_available() -> bool:
    """torch/timmがインストールされているか(NSFW自動仕分けが実行可能か)を返す。"""
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("timm") is not None


def try_create_classifier(config: NsfwConfig) -> "NsfwClassifier | None":
    """torch/timmが未インストールならNoneを返す。CLI/Web両方から共通で使う。"""
    if not is_available():
        return None
    return NsfwClassifier(config)
