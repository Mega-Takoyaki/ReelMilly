"""作品のファイル名の整理。

ディスク上のファイル名は作品ID(`<ID>.<拡張子>`)にそろえ、元のファイル名は
`assets.original_name`に作品の情報として持つ。理由:
- 投稿(Fanvue)でファイル名が外へ出るとき、元の名前(内部メモや生成ツール由来のID)を漏らさない
- 日本語や記号を含む名前による、保存・他ツールとの相性の問題を避ける
元の名前は、詳細画面の表示・検索・ダウンロード時のファイル名に使う。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core import db
from core.config import Config
from core.events import log_event


@dataclass
class NormalizeResult:
    renamed: int = 0
    skipped: list[str] = field(default_factory=list)


def id_filename(asset_id: str, original: str | Path) -> str:
    """`<作品ID>.<拡張子(小文字)>`。"""
    return f"{asset_id}{Path(original).suffix.lower()}"


def normalize_asset_files(config: Config, conn: sqlite3.Connection) -> NormalizeResult:
    """ファイル名が作品IDになっていない既存の作品を、`<ID>.<拡張子>`へ改名する(何度実行しても安全)。

    - 元のファイル名は`original_name`へ記録する(未記録のものだけ)
    - AI処理の実行中の作品、ファイルが見つからない作品、同名のファイルが既にある作品は触らず、理由を返す
    - 改名は同じフォルダ内で行い、DBの`file_path`を更新する(透かし入りの別ファイルは対象外)
    """
    result = NormalizeResult()
    running = {r["asset_id"] for r in conn.execute("SELECT asset_id FROM ai_tasks WHERE status = 'running'")}
    for asset in db.list_assets(conn, limit=1000000) + db.list_assets(conn, trashed=True, limit=1000000):
        path = Path(asset["file_path"])
        target = path.with_name(id_filename(asset["id"], path))
        if path.name == target.name:
            continue
        if asset["id"] in running:
            result.skipped.append(f"{asset['id']}: AI処理の実行中のため後で改名します")
            continue
        if not path.exists():
            result.skipped.append(f"{asset['id']}: ファイルが見つかりません（{path.name}）")
            continue
        if target.exists():
            result.skipped.append(f"{asset['id']}: 改名先が既にあります（{target.name}）")
            continue
        path.rename(target)
        updates = {"file_path": str(target)}
        if not asset.get("original_name"):
            updates["original_name"] = path.name
        db.update_asset(conn, asset["id"], **updates)
        log_event(config.paths.events_path, "file_renamed", asset_id=asset["id"], old=path.name, new=target.name)
        result.renamed += 1
    return result
