"""ごみ箱の作品の完全削除(元に戻せない)。

DBの記録(タグ・フォルダ・投稿先・投稿状態・AI処理は外部キーの連鎖で一緒に消える)と、
ライブラリ内のファイルを削除する。誤削除を避けるため、次の場合だけ実行する:
- ごみ箱に入っている作品に限る(通常の作品は対象外)
- ファイルはライブラリ(`data/library`)の中にあるものに限る
- 作品のフォルダ(`ready/<作品ID>/`)ごと消すのは、フォルダ名が作品IDと一致するときだけ
- ファイルを消せなかった作品は、DBの記録も残して理由を返す(あとで再試行できる)
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core import db
from core.config import Config
from core.events import log_event


@dataclass
class PurgeResult:
    deleted: int = 0
    errors: list[str] = field(default_factory=list)


def _remove_files(config: Config, asset: dict) -> None:
    """作品のファイルを消す。ライブラリの外にあるファイルは触らない(例外にもしない)。"""
    library_root = config.paths.ready.parent.resolve()
    path = Path(asset["file_path"])
    asset_dir = path.parent
    try:
        inside = library_root in asset_dir.resolve().parents or asset_dir.resolve() == library_root
    except OSError:
        inside = False
    if not inside:
        return
    if asset_dir.name == asset["id"] and asset_dir.resolve() != library_root:
        shutil.rmtree(asset_dir)  # 作品専用のフォルダごと(メディア・派生ファイルをまとめて)
    else:
        path.unlink(missing_ok=True)


def purge_assets(config: Config, conn: sqlite3.Connection, asset_ids: list[str] | None = None) -> PurgeResult:
    """ごみ箱の作品を完全に削除する。`asset_ids`がNoneならごみ箱の全件(ごみ箱を空にする)。"""
    trashed = {a["id"]: a for a in db.list_assets(conn, trashed=True, limit=100000)}
    targets = list(trashed) if asset_ids is None else [i for i in asset_ids if i in trashed]
    result = PurgeResult()
    for asset_id in targets:
        running = conn.execute(
            "SELECT 1 FROM ai_tasks WHERE asset_id = ? AND status = 'running'", (asset_id,)
        ).fetchone()
        if running:
            result.errors.append(f"{asset_id}: AI処理の実行中のため削除できません（終わってからやり直してください）")
            continue
        try:
            _remove_files(config, trashed[asset_id])
        except OSError as exc:
            result.errors.append(f"{asset_id}: ファイルを削除できませんでした（{exc}）")
            continue
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        conn.commit()
        log_event(config.paths.events_path, "asset_purged", asset_id=asset_id)
        result.deleted += 1
    return result
