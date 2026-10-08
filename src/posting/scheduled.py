"""予約投稿(Fanvue・X)。ReelMilly自身が時刻を管理し、時間になったら`reelmilly watch`が投稿する。

先方(Fanvue・X)のスケジューラーには登録しない。予約した内容(作品・バージョン・投稿文・公開範囲など)を保存しておき、
予定の時刻が来たら、その時点の状態で、もう一度検査して(`post_now.prepare`)投稿する(`post_now.execute`)。

- 予約は、取り消せる。「いますぐ実行」もできる
- 時刻に`watch`が止まっていたら、再開後に(遅れて)実行する
- 実行に失敗したら、再試行せずに「失敗」として残し、通知する(二重投稿を避けるため。やり直しは、予約し直す)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from core import db, notifications
from posting import post_now

CHANNEL_LABELS = {"fanvue": "Fanvue", "x": "X"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_run_at(value) -> datetime:
    """画面から届いた日時(ISO 8601。タイムゾーンつき)を、UTCにする。読めなければ`ValueError`。"""
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("タイムゾーンがありません")
    return parsed.astimezone(timezone.utc)


def _summary(payload: dict) -> str:
    channel = payload.get("channel", "fanvue")
    return f"{CHANNEL_LABELS.get(channel, channel)} {len(payload.get('items') or [])}件"


def create(conn: sqlite3.Connection, payload: dict, run_at: datetime) -> int:
    asset_ids = [str(i.get("asset_id")) for i in payload.get("items") or []]
    cur = conn.execute(
        "INSERT INTO scheduled_posts (channel, payload, summary, asset_ids, run_at, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'scheduled', ?)",
        (payload.get("channel", "fanvue"), json.dumps(payload, ensure_ascii=False), _summary(payload),
         json.dumps(asset_ids), run_at.astimezone(timezone.utc).isoformat(), _now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def _row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["payload"] = json.loads(data["payload"])
    data["asset_ids"] = json.loads(data["asset_ids"])
    return data


def list_posts(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """予約中のものを、実行の早い順に、そのあとに、終わったものを、新しい順に。"""
    rows = conn.execute(
        "SELECT * FROM scheduled_posts ORDER BY CASE WHEN status IN ('scheduled','running') THEN 0 ELSE 1 END, "
        "CASE WHEN status IN ('scheduled','running') THEN run_at END ASC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row(r) for r in rows]


def pending_for_asset(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM scheduled_posts WHERE status = 'scheduled' ORDER BY run_at").fetchall()
    return [r for r in (_row(x) for x in rows) if asset_id in r["asset_ids"]]


def cancel(conn: sqlite3.Connection, scheduled_id: int) -> bool:
    cur = conn.execute("UPDATE scheduled_posts SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'scheduled'", (_now().isoformat(), scheduled_id))
    conn.commit()
    return cur.rowcount == 1


def run_now(conn: sqlite3.Connection, scheduled_id: int) -> bool:
    """予定の時刻を、いまにする(次の巡回で、実行される)。"""
    cur = conn.execute("UPDATE scheduled_posts SET run_at = ? WHERE id = ? AND status = 'scheduled'", (_now().isoformat(), scheduled_id))
    conn.commit()
    return cur.rowcount == 1


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """前回、実行の途中で止まったもの(投稿されたかが不明)は、再実行せずに、失敗として残す(二重投稿を避ける)。"""
    cur = conn.execute(
        "UPDATE scheduled_posts SET status = 'failed', error = ?, finished_at = ? WHERE status = 'running'",
        ("実行の途中で止まりました(投稿されたかを、投稿先で確認してください)", _now().isoformat()),
    )
    conn.commit()
    return cur.rowcount


def claim_due(conn: sqlite3.Connection) -> dict | None:
    """時刻の来た予約を1件、実行中にして返す(複数のプロセスが取り合っても、1件は1回だけ)。"""
    while True:
        row = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'scheduled' AND run_at <= ? ORDER BY run_at LIMIT 1", (_now().isoformat(),)
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute("UPDATE scheduled_posts SET status = 'running' WHERE id = ? AND status = 'scheduled'", (row["id"],))
        conn.commit()
        if cur.rowcount == 1:
            return _row(row)


def _finish(conn: sqlite3.Connection, scheduled_id: int, error: str | None) -> None:
    conn.execute(
        "UPDATE scheduled_posts SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        ("failed" if error else "done", error, _now().isoformat(), scheduled_id),
    )
    conn.commit()


def run_due(conn: sqlite3.Connection, config, log=print) -> int:
    """時刻の来た予約を、すべて実行する。実行した件数を返す。ストレージが見つからない間は、何もせず、予約のまま残す。"""
    from core import storage

    storage.apply_override(config, conn)
    if not storage.check(config, conn).available:
        return 0
    count = 0
    while True:
        row = claim_due(conn)
        if row is None:
            return count
        count += 1
        label = CHANNEL_LABELS.get(row["channel"], row["channel"])
        late = (_now() - datetime.fromisoformat(row["run_at"])).total_seconds()
        try:
            prepared = post_now.prepare(config, conn, row["payload"])
        except post_now.PostRequestError as exc:
            _finish(conn, row["id"], str(exc))
            notifications.add(conn, "post", f"予約した{label}への投稿を、実行できませんでした", f"{row['summary']}: {exc}", "error",
                              asset_id=row["asset_ids"][0] if len(row["asset_ids"]) == 1 else None)
            log(f"[scheduled] #{row['id']} 実行できません: {exc}")
            continue
        result = post_now.execute(config, conn, prepared)
        _finish(conn, row["id"], result.error if not result.ok else None)
        log(f"[scheduled] #{row['id']} {label} {'投稿しました' if result.ok else '失敗: ' + str(result.error)}" + (f"（予定より{int(late // 60)}分遅れ）" if late > 300 else ""))
    return count
