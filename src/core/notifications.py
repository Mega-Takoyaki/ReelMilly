"""処理の完了・失敗などの通知と、その履歴。

画面右上のベルのアイコン(未読の印・最新10件のポップアップ)と、設定画面の「通知・ログ」タブ(全件の履歴)で見る。
ワーカー・投稿ジョブ・ストレージ・重複の検出など、画面を開いていないときに起きたことも残す。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

LEVELS = ("info", "success", "warning", "error")
KINDS = {
    "ai": "AI処理",
    "watermark": "透かし",
    "post": "投稿",
    "duplicates": "重複",
    "storage": "ストレージ",
    "system": "その他",
}
ACTIONS = ("duplicates",)  # 通知をクリックすると、ポップアップで開く操作


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add(
    conn: sqlite3.Connection,
    kind: str,
    title: str,
    body: str = "",
    level: str = "info",
    action: str | None = None,
    asset_id: str | None = None,
) -> int:
    """通知を1件追加する。追加した通知のIDを返す。"""
    if level not in LEVELS:
        level = "info"
    if kind not in KINDS:
        kind = "system"
    cur = conn.execute(
        "INSERT INTO notifications (created_at, kind, level, title, body, action, asset_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), kind, level, title, body or None, action if action in ACTIONS else None, asset_id),
    )
    conn.commit()
    return cur.lastrowid


def _row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["read"] = data.pop("read_at") is not None
    data["kind_label"] = KINDS.get(data["kind"], data["kind"])
    return data


def list_notifications(
    conn: sqlite3.Connection, limit: int = 10, offset: int = 0, kind: str | None = None
) -> list[dict]:
    sql = "SELECT * FROM notifications"
    params: list = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row(r) for r in conn.execute(sql, params)]


def count(conn: sqlite3.Connection, kind: str | None = None) -> int:
    if kind:
        return conn.execute("SELECT COUNT(*) FROM notifications WHERE kind = ?", (kind,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]


def unread_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]


def mark_read(conn: sqlite3.Connection, ids: list[int] | None = None) -> int:
    """既読にする。`ids`がNoneなら、すべて。"""
    if ids is None:
        cur = conn.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (_now(),))
    else:
        marks = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND id IN ({marks})", (_now(), *ids)
        )
    conn.commit()
    return cur.rowcount


def summarize_tasks(label: str, done: int, failed: int, first_error: str | None) -> tuple[str, str, str]:
    """AI処理などのまとまりの結果を、通知の(タイトル, 本文, レベル)にする。"""
    parts = []
    if done:
        parts.append(f"{done}件完了")
    if failed:
        parts.append(f"{failed}件失敗")
    title = f"{label}: " + "、".join(parts)
    level = "success" if not failed else ("error" if not done else "warning")
    return title, (first_error or ""), level
