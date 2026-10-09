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


def probe_size(path: Path) -> tuple[int, int] | None:
    """動画の映像の(幅, 高さ)。読めない場合はNone。"""
    probe = find("ffprobe")
    if probe is None:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
        )
        stream = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
        return (int(stream["width"]), int(stream["height"])) if stream.get("width") and stream.get("height") else None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def mask_filter(params: dict, width: int, height: int) -> tuple[str, str]:
    """ぼかし・モザイクの`-filter_complex`。範囲ごとに切り出して、加工して、元の位置に重ねる(動画の最初から最後まで、固定)。"""
    from core import mask

    boxes = mask.pixel_regions(params["regions"], width, height, even=True)
    if not boxes:
        raise FfmpegError("範囲が小さすぎます")
    long_side = max(width, height)
    parts, last = [], "0:v"
    for i, (x, y, w, h) in enumerate(boxes):
        if params["style"] == "blur":
            fx = f"gblur=sigma={mask.blur_sigma(params['strength'], long_side):.2f}"
        else:
            block = mask.mosaic_block(params["strength"], long_side)
            fx = f"scale={max(2, round(w / block))}:{max(2, round(h / block))}:flags=area,scale={w}:{h}:flags=neighbor"
        parts.append(f"[0:v]crop={w}:{h}:{x}:{y},{fx}[r{i}]")
        parts.append(f"[{last}][r{i}]overlay={x}:{y}[o{i}]")
        last = f"o{i}"
    return ";".join(parts), last


def mask_video(src: Path, dest: Path, params: dict, timeout: int = 7200) -> None:
    """動画の指定範囲に、ぼかし・モザイクをかけて`dest`に書く(音声はそのまま、画質はほぼ劣化しない設定)。"""
    ffmpeg = _require("ffmpeg")
    size = probe_size(src)
    if size is None:
        raise FfmpegError("動画の大きさを読めませんでした(ffprobeが見つからない、またはファイルが壊れています)")
    graph, out_label = mask_filter(params, *size)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-filter_complex", graph, "-map", f"[{out_label}]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ]
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


def extract_frame(src: Path, dest: Path, at: float, scale: int = 1, timeout: int = 300) -> None:
    """動画の`at`秒の1コマを、静止画として`dest`(拡張子で、jpg/pngが決まる)に書く。

    `scale`が2以上のときは、その倍率に拡大する(Lanczos補間。細部が増えるわけではない)。
    """
    ffmpeg = _require("ffmpeg")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1"]
    if scale and scale > 1:
        cmd += ["-vf", f"scale=iw*{int(scale)}:ih*{int(scale)}:flags=lanczos"]
    if dest.suffix.lower() in (".jpg", ".jpeg"):
        cmd += ["-q:v", "1", "-pix_fmt", "yuvj420p"]
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
        raise FfmpegError("コマを取り出せませんでした: " + (detail[-1] if detail else "指定の位置に、映像がありません"))


def overlay_filter(params: dict, sizes: list[tuple[int, int]], width: int, height: int, duration: float | None = None) -> tuple[str, str]:
    """テロップ・スタンプ(レイヤーごとのPNG)を重ねる`-filter_complex`。入力0が動画、入力1以降がレイヤー(`sizes`は、その画像の大きさ)。

    位置は、中心(x,y)の割合。動きは、画面の外から外へ、`cycle`秒かけて横切る(`loop`なら繰り返す)。表示の時間帯は、`start`〜`end`秒。
    """
    parts, last = [], "0:v"
    for i, (layer, (w, h)) in enumerate(zip(params["layers"], sizes)):
        anim = layer["anim"]
        start, end, cycle = anim["start"], anim["end"], anim["cycle"]
        x = f"{round(layer['x'] * width - w / 2)}"
        y = f"{round(layer['y'] * height - h / 2)}"
        stop = end if end is not None else 999999
        if anim["type"] != "none":
            t = f"(t-{start:g})"
            p = f"mod({t},{cycle:g})/{cycle:g}" if anim["loop"] else f"clip({t}/{cycle:g},0,1)"
            if anim["type"] == "scroll_left":
                x = f"main_w-{p}*(main_w+overlay_w)"
            elif anim["type"] == "scroll_right":
                x = f"-overlay_w+{p}*(main_w+overlay_w)"
            elif anim["type"] == "scroll_up":
                y = f"main_h-{p}*(main_h+overlay_h)"
            else:
                y = f"-overlay_h+{p}*(main_h+overlay_h)"
            if not anim["loop"]:
                stop = min(stop, start + cycle)  # 1回だけ流れて、画面の外へ出たら、消える
        timed = start > 0 or stop < 999999
        enable = f":enable='between(t,{start:g},{stop:g})'" if timed else ""
        fades = ""
        fade_in, fade_out = anim.get("fade_in", 0), anim.get("fade_out", 0)
        if fade_in > 0:
            fades += f",fade=t=in:st={start:g}:d={fade_in:g}:alpha=1"
        last_second = stop if stop < 999999 else duration  # 最後まで表示するときは、動画の長さで終わる
        if fade_out > 0 and last_second:
            fades += f",fade=t=out:st={max(last_second - fade_out, start):g}:d={fade_out:g}:alpha=1"
        parts.append(f"[{i + 1}:v]format=rgba{fades}[l{i}]")
        parts.append(f"[{last}][l{i}]overlay=x='{x}':y='{y}':shortest=1:format=auto{enable}[o{i}]")
        last = f"o{i}"
    return ";".join(parts), last


def overlay_video(conn, src: Path, dest: Path, params: dict, timeout: int = 7200) -> None:
    """動画に、テロップ・スタンプを重ねて`dest`に書く(音声はそのまま、画質はほぼ劣化しない設定)。"""
    import tempfile

    from core import overlay

    ffmpeg = _require("ffmpeg")
    size = probe_size(src)
    if size is None:
        raise FfmpegError("動画の大きさを読めませんでした(ffprobeが見つからない、またはファイルが壊れています)")
    width, height = size
    with tempfile.TemporaryDirectory(prefix="reelmilly-overlay-") as tmp:
        paths, sizes = [], []
        for i, layer in enumerate(params["layers"]):
            img = overlay.render_layer(conn, layer, width, height)
            p = Path(tmp) / f"layer{i}.png"
            img.save(p, format="PNG")
            paths.append(p)
            sizes.append(img.size)
        graph, out_label = overlay_filter(params, sizes, width, height, probe_duration(src))
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
        for p in paths:
            cmd += ["-loop", "1", "-framerate", "30", "-i", str(p)]
        cmd += [
            "-filter_complex", graph, "-map", f"[{out_label}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
        ]
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
