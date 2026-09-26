"""SQLiteアクセス層。ADR-0011に基づき、メタデータ・タグ・フォルダの正はSQLiteとする。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


# --- assets -----------------------------------------------------------------

def insert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    conn.execute(
        """
        INSERT INTO assets (
            id, status, kind, file_path, caption, x_caption, fanvue_text,
            audience, price_cents, fanvue_url, fanvue_uuid, x_ok,
            content_rating, content_rating_confirmed, nsfw_auto_rating,
            nsfw_auto_confidence, content_description, fanvue_caption_draft,
            created_at, updated_at
        ) VALUES (
            :id, :status, :kind, :file_path, :caption, :x_caption, :fanvue_text,
            :audience, :price_cents, :fanvue_url, :fanvue_uuid, :x_ok,
            :content_rating, :content_rating_confirmed, :nsfw_auto_rating,
            :nsfw_auto_confidence, :content_description, :fanvue_caption_draft,
            :created_at, :updated_at
        )
        """,
        {
            "id": asset["id"],
            "status": asset["status"],
            "kind": asset["kind"],
            "file_path": asset["file_path"],
            "caption": asset.get("caption"),
            "x_caption": asset.get("x_caption"),
            "fanvue_text": asset.get("fanvue_text"),
            "audience": asset.get("audience"),
            "price_cents": asset.get("price_cents"),
            "fanvue_url": asset.get("fanvue_url"),
            "fanvue_uuid": asset.get("fanvue_uuid"),
            "x_ok": int(asset.get("x_ok", False)),
            "content_rating": asset.get("content_rating"),
            "content_rating_confirmed": int(asset.get("content_rating_confirmed", False)),
            "nsfw_auto_rating": asset.get("nsfw_auto_rating"),
            "nsfw_auto_confidence": asset.get("nsfw_auto_confidence"),
            "content_description": asset.get("content_description"),
            "fanvue_caption_draft": asset.get("fanvue_caption_draft"),
            "created_at": asset["created_at"],
            "updated_at": asset["updated_at"],
        },
    )
    conn.commit()


def get_asset(conn: sqlite3.Connection, asset_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return dict(row) if row else None


def list_assets(
    conn: sqlite3.Connection,
    status: str | None = None,
    content_rating: str | None = None,
    folder_id: int | None = None,
    tag: str | None = None,
    channel: str | None = None,
    confirmed_only: bool = False,
    kind: str | None = None,
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = "SELECT DISTINCT assets.* FROM assets"
    joins = []
    conditions = []
    params: dict = {}

    if folder_id is not None:
        joins.append("JOIN asset_folders ON asset_folders.asset_id = assets.id")
        conditions.append("asset_folders.folder_id = :folder_id")
        params["folder_id"] = folder_id

    if tag is not None:
        joins.append("JOIN asset_tags ON asset_tags.asset_id = assets.id")
        joins.append("JOIN tags ON tags.id = asset_tags.tag_id")
        conditions.append("tags.name = :tag")
        params["tag"] = tag

    if channel is not None:
        joins.append("JOIN channels ON channels.asset_id = assets.id")
        conditions.append("channels.channel = :channel")
        params["channel"] = channel

    if status is not None:
        conditions.append("assets.status = :status")
        params["status"] = status

    if content_rating is not None:
        conditions.append("assets.content_rating = :content_rating")
        params["content_rating"] = content_rating

    if confirmed_only:
        conditions.append("assets.content_rating_confirmed = 1")

    if kind is not None:
        conditions.append("assets.kind = :kind")
        params["kind"] = kind

    if joins:
        query += " " + " ".join(joins)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    query += f" ORDER BY assets.created_at {order_sql} LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def update_asset(conn: sqlite3.Connection, asset_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    params = dict(fields)
    params["id"] = asset_id
    conn.execute(f"UPDATE assets SET {set_clause} WHERE id = :id", params)
    conn.commit()


# --- channels -----------------------------------------------------------------

def add_channel(conn: sqlite3.Connection, asset_id: str, channel: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO channels (asset_id, channel) VALUES (?, ?)",
        (asset_id, channel),
    )
    conn.commit()


def get_channels(conn: sqlite3.Connection, asset_id: str) -> list[str]:
    rows = conn.execute("SELECT channel FROM channels WHERE asset_id = ?", (asset_id,)).fetchall()
    return [row["channel"] for row in rows]


# --- tags -----------------------------------------------------------------

def get_or_create_tag(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    conn.commit()
    return cursor.lastrowid


def add_tag_to_asset(conn: sqlite3.Connection, asset_id: str, tag_name: str) -> None:
    tag_id = get_or_create_tag(conn, tag_name)
    conn.execute(
        "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
        (asset_id, tag_id),
    )
    conn.commit()


def remove_tag_from_asset(conn: sqlite3.Connection, asset_id: str, tag_name: str) -> None:
    conn.execute(
        """
        DELETE FROM asset_tags
        WHERE asset_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)
        """,
        (asset_id, tag_name),
    )
    conn.commit()


def list_tags_for_asset(conn: sqlite3.Connection, asset_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT tags.name FROM tags
        JOIN asset_tags ON asset_tags.tag_id = tags.id
        WHERE asset_tags.asset_id = ?
        ORDER BY tags.name
        """,
        (asset_id,),
    ).fetchall()
    return [row["name"] for row in rows]


def list_all_tags(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
    return [row["name"] for row in rows]


# --- folders -----------------------------------------------------------------

def create_folder(conn: sqlite3.Connection, name: str, parent_id: int | None = None) -> int:
    cursor = conn.execute(
        "INSERT INTO folders (name, parent_id) VALUES (?, ?)", (name, parent_id)
    )
    conn.commit()
    return cursor.lastrowid


def list_folders(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def add_asset_to_folder(conn: sqlite3.Connection, asset_id: str, folder_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO asset_folders (asset_id, folder_id) VALUES (?, ?)",
        (asset_id, folder_id),
    )
    conn.commit()


def remove_asset_from_folder(conn: sqlite3.Connection, asset_id: str, folder_id: int) -> None:
    conn.execute(
        "DELETE FROM asset_folders WHERE asset_id = ? AND folder_id = ?",
        (asset_id, folder_id),
    )
    conn.commit()


def list_folders_for_asset(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT folders.* FROM folders
        JOIN asset_folders ON asset_folders.folder_id = folders.id
        WHERE asset_folders.asset_id = ?
        ORDER BY folders.name
        """,
        (asset_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# --- job_runs（同一カレンダー日の二重実行防止、CLAUDE_HANDOFF.md 6章） ------------

def get_last_run_date(conn: sqlite3.Connection, job_name: str) -> str | None:
    row = conn.execute(
        "SELECT last_run_date FROM job_runs WHERE job_name = ?", (job_name,)
    ).fetchone()
    return row["last_run_date"] if row else None


def set_last_run_date(conn: sqlite3.Connection, job_name: str, date_str: str) -> None:
    conn.execute(
        """
        INSERT INTO job_runs (job_name, last_run_date) VALUES (?, ?)
        ON CONFLICT(job_name) DO UPDATE SET last_run_date = excluded.last_run_date
        """,
        (job_name, date_str),
    )
    conn.commit()


# --- settings（ADR-0015: 本体UIの設定画面から調整可能な値） ------------------

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def list_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}
