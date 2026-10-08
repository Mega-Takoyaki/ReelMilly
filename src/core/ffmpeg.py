"""ffmpegの場所の特定と、動画の長さの取得・切り出し。

ffmpegは、次の順に探す(最初に見つかったもの):
1. 環境変数`REELMILLY_FFMPEG`(ffmpeg本体のパス。ffprobeは同じ場所から探す)
2. PATH上の`ffmpeg`
3. wingetで入れた場所(`%LOCALAPPDATA%/Microsoft/WinGet/...`)。インストール前から動いているプロセスは、
   PATHの変更を知らないため、ここも直接探す
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

MIN_CLIP_SECONDS = 0.1  # これより短い切り出しは、作らない
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windowsで、コンソール窓を出さない


class FfmpegError(RuntimeError):
    pass


def _winget_candidates(name: str) -> list[Path]:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    root = Path(base) / "Microsoft" / "WinGet"
    found = sorted((root / "Packages").glob(f"Gyan.FFmpeg*/ffmpeg-*/bin/{name}.exe"), reverse=True)
    links = root / "Links" / f"{name}.exe"
    return found + ([links] if links.exists() else [])


def find(name: str = "ffmpeg") -> str | None:
    """`ffmpeg`または`ffprobe`のパス。見つからなければNone。"""
    override = os.environ.get("REELMILLY_FFMPEG")
    if override:
        p = Path(override)
        if name != "ffmpeg":
            p = p.with_name(p.name.replace("ffmpeg", name))
        if p.exists():
            return str(p)
    which = shutil.which(name)
    if which:
        return which
    for p in _winget_candidates(name):
        if p.exists():
            return str(p)
    return None


def available() -> bool:
    return find("ffmpeg") is not None


def _require(name: str = "ffmpeg") -> str:
    path = find(name)
    if path is None:
        raise FfmpegError(
            "ffmpegが見つかりません。`winget install Gyan.FFmpeg`で入れてから、アプリを再起動してください"
        )
    return path


def probe_duration(path: Path) -> float | None:
    """動画の長さ(秒)。ffprobeが無い・読めない場合はNone。"""
    probe = find("ffprobe")
    if probe is None:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
        )
        value = json.loads(out.stdout or "{}").get("format", {}).get("duration")
        return float(value) if value else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def trim(src: Path, dest: Path, start: float, end: float, mode: str = "accurate", timeout: int = 3600) -> None:
    """`start`〜`end`秒を切り出して`dest`に書く。

    - accurate: 再エンコードして、指定どおりの位置で切る(画質は、ほぼ劣化しない設定)
    - fast: 再エンコードせずにコピーする(速いが、開始位置が直前のキーフレームにずれることがある)
    """
    ffmpeg = _require("ffmpeg")
    length = end - start
    if length < MIN_CLIP_SECONDS:
        raise FfmpegError("切り出す範囲が短すぎます")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}"]
    if mode == "fast":
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        ]
    cmd.append(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        dest.unlink(missing_ok=True)
        raise FfmpegError("ffmpegの処理が時間切れになりました") from exc
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()
        raise FfmpegError("ffmpegが失敗しました: " + (detail[-1] if detail else f"終了コード{result.returncode}"))
