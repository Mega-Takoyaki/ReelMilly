"""既存のタグの整理(整理案の作成・適用・定期実行)。

`tag_normalizer`の規則で、いまあるタグを整え直す。適用前に、データベースを退避し、1つの取引(失敗したら、すべて元に戻る)で行う。
定期実行(週1回など)では、整理案の通知、または、自動の適用を行う。LLMは使わない(タグの内容を、外部に送らない)。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import db, notifications
from core.tag_normalizer import normalize

SCHEDULE_KEY = "tag_cleanup_schedule"
DEFAULT_SCHEDULE = {"enabled": False, "weekday": 6, "time": "04:00", "auto_apply": False}  # 既定: オフ。日曜 4:00。自動適用はしない
KIND_LABELS = {"split": "分割", "color": "色を分離", "alias": "別名を統一", "phrase": "文のタグ", "drop": "捨てる", "fix": "表記を直す"}


def aliases(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["alias"]: r["canonical"] for r in conn.execute("SELECT alias, canonical FROM tag_aliases")}


def plan(conn: sqlite3.Connection) -> list[dict]:
    """いまあるタグのうち、整理で変わるものの一覧。各要素: name / count(付いている作品数) / becomes(整理後のタグ) / kinds。"""
    custom = aliases(conn)
    rows = conn.execute(
        "SELECT t.name, COUNT(at.asset_id) AS n FROM tags t LEFT JOIN asset_tags at ON at.tag_id = t.id GROUP BY t.id ORDER BY n DESC, t.name"
    ).fetchall()
    items = []
    for row in rows:
        result = normalize(row["name"], custom)
        if result.changed:
            items.append({"name": row["name"], "count": row["n"], "becomes": result.tags, "kinds": sorted(result.kinds)})
    return items


def summary(conn: sqlite3.Connection, items: list[dict]) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    after = {t for r in conn.execute("SELECT name FROM tags") for t in normalize(r["name"], aliases(conn)).tags}
    return {"tags_before": total, "tags_after": len(after), "changed": len(items)}


def backup(conn: sqlite3.Connection, config) -> Path:
    """適用の前に、データベースを退避する(`data/backup/`)。"""
    folder = config.paths.state_dir.parent / "backup"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"reelmilly-tags-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def apply(conn: sqlite3.Connection, config, names: list[str] | None = None) -> dict:
    """整理案を適用する(`names`を省略すると、すべて)。退避してから、1つの取引で行う。戻り値: 件数など。"""
    custom = aliases(conn)
    wanted = set(names) if names is not None else None
    targets = [i for i in plan(conn) if wanted is None or i["name"] in wanted]
    if not targets:
        return {"applied": 0, "assets": 0, "backup": None}
    saved = backup(conn, config)
    touched: set[str] = set()
    try:
        conn.execute("BEGIN")
        for item in targets:
            old = conn.execute("SELECT id FROM tags WHERE name = ?", (item["name"],)).fetchone()
            if old is None:
                continue
            asset_ids = [r["asset_id"] for r in conn.execute("SELECT asset_id FROM asset_tags WHERE tag_id = ?", (old["id"],))]
            for new_name in item["becomes"]:
                row = conn.execute("SELECT id FROM tags WHERE name = ?", (new_name,)).fetchone()
                new_id = row["id"] if row else conn.execute("INSERT INTO tags (name) VALUES (?)", (new_name,)).lastrowid
                for asset_id in asset_ids:
                    conn.execute("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (asset_id, new_id))
            conn.execute("DELETE FROM asset_tags WHERE tag_id = ?", (old["id"],))
            if item["name"] not in item["becomes"]:
                conn.execute("DELETE FROM tags WHERE id = ?", (old["id"],))
            touched.update(asset_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"applied": len(targets), "assets": len(touched), "backup": str(saved)}


# ------------------------------------------------------------------ 定期実行
def get_schedule(conn: sqlite3.Connection) -> dict:
    stored = db.get_setting(conn, SCHEDULE_KEY)
    data = dict(DEFAULT_SCHEDULE)
    if stored:
        try:
            data.update(json.loads(stored))
        except ValueError:
            pass
    return data


def set_schedule(conn: sqlite3.Connection, enabled: bool, weekday, time: str | None, auto_apply: bool) -> None:
    from core.settings import _valid_time

    current = get_schedule(conn)
    try:
        day = min(max(int(weekday), 0), 6)
    except (TypeError, ValueError):
        day = current["weekday"]
    db.set_setting(conn, SCHEDULE_KEY, json.dumps({
        "enabled": bool(enabled), "weekday": day, "time": _valid_time(time) or current["time"], "auto_apply": bool(auto_apply),
    }))


def run_if_due(conn: sqlite3.Connection, config, now: datetime | None = None, log=print) -> str | None:
    """週1回などの設定で、時刻が来ていて、今日まだなら、整理を行う。行った内容(文)を返す。"""
    schedule = get_schedule(conn)
    if not schedule["enabled"]:
        return None
    tz = ZoneInfo(config.timezone)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    today = now.strftime("%Y-%m-%d")
    if now.weekday() != schedule["weekday"] or (now.hour, now.minute) < (hour, minute) or db.get_last_run_date(conn, "tag_cleanup") == today:
        return None
    db.set_last_run_date(conn, "tag_cleanup", today)  # 先に記録する(途中で失敗しても、1日に何度も繰り返さない)
    items = plan(conn)
    if not items:
        return None
    if schedule["auto_apply"]:
        result = apply(conn, config)
        message = f"タグを自動で整理しました（{result['applied']}種類、{result['assets']}件の作品）。退避: {result['backup']}"
        notifications.add(conn, "system", "タグを整理しました", message, "success")
    else:
        message = f"タグの整理案が{len(items)}種類あります。設定の「タグ整理」で、確認して適用できます"
        notifications.add(conn, "system", "タグの整理案があります", message, "info")
    log(f"[tag-cleanup] {message}")
    return message
