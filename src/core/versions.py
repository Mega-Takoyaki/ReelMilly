"""作品の「バージョン」(投稿に使えるファイルの種類)。

1つの作品には、元のファイルのほかに、透かし入りの画像や、編集した動画(サブ動画)がある。
「今すぐ投稿」では、作品ごとに、このどれを投稿するかを選ぶ。

バージョンのキー: `original`(元のファイル)/ `wm`(透かし入りの画像)/ `edit:<編集ID>`(編集した動画)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core import edits


class VersionError(ValueError):
    """指定したバージョンが無い・ファイルが見つからないとき。"""


def list_versions(conn: sqlite3.Connection, asset: dict) -> list[dict]:
    """投稿に使えるバージョンの一覧(使えるものだけ)。各要素: key / label / media_type(image|video)。"""
    versions = [{
        "key": "original",
        "label": "元の動画" if asset["kind"] == "video" else "元の画像",
        "media_type": asset["kind"],
    }]
    if asset.get("wm_path") and Path(asset["wm_path"]).exists():
        versions.append({"key": "wm", "label": f"透かし入り（「{asset.get('wm_text') or ''}」）", "media_type": "image"})
    for edit in edits.list_edits(conn, asset["id"]):
        path = edits.edit_path(asset, edit)
        if edit["status"] == "done" and path is not None and path.exists():
            versions.append({"key": f"edit:{edit['id']}", "label": f"編集した動画: {edit['summary']}", "media_type": "video"})
    return versions


def default_version(versions: list[dict]) -> str:
    """既定のバージョン。透かし入りがあれば、それ(これまでの投稿と同じ扱い)。無ければ、元のファイル。"""
    keys = [v["key"] for v in versions]
    return "wm" if "wm" in keys else "original"


def resolve(conn: sqlite3.Connection, asset: dict, key: str | None) -> tuple[Path, str]:
    """バージョンのキーから、ファイルのパスと、メディアの種類(image|video)を返す。無ければ`VersionError`。"""
    key = key or default_version(list_versions(conn, asset))
    if key == "original":
        path, media_type = Path(asset["file_path"]), asset["kind"]
    elif key == "wm":
        if not asset.get("wm_path"):
            raise VersionError("この作品には、透かし入りがありません")
        path, media_type = Path(asset["wm_path"]), "image"
    elif key.startswith("edit:") and key[5:].isdigit():
        edit = edits.get_edit(conn, asset["id"], int(key[5:]))
        if edit is None or edit["status"] != "done":
            raise VersionError("その編集した動画は、使えません(処理中・失敗・削除済み)")
        path, media_type = edits.edit_path(asset, edit), "video"
    else:
        raise VersionError(f"不明なバージョンです: {key}")
    if path is None or not path.exists():
        raise VersionError(f"ファイルが見つかりません(ストレージを確認してください): {path.name if path else key}")
    return path, media_type
