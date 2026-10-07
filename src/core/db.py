"""SQLiteアクセス層。ADR-0011に基づき、メタデータ・タグ・フォルダの正はSQLiteとする。"""
from __future__ import annotations

import json
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
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """既存DBへ後から追加した列を補う(マイグレーションツールは未導入のため最小限の対応)。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
    if "analysis_error" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN analysis_error TEXT")
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN deleted_at TEXT")
    if "content_hash" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN content_hash TEXT")
    # 列を足したあとに作る(既存DBでは、schema.sqlの時点では列が無いため、そこでは作れない)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_content_hash ON assets(content_hash)")
    for column in ("width", "height"):
        if column not in columns:
            conn.execute(f"ALTER TABLE assets ADD COLUMN {column} INTEGER")
    if "is_broken" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN is_broken INTEGER NOT NULL DEFAULT 0")
    if "original_name" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN original_name TEXT")
    # 既存の作品は、いまのファイル名を元の名前として記録する(ファイルは動かさない)
    for row in conn.execute("SELECT id, file_path FROM assets WHERE original_name IS NULL").fetchall():
        conn.execute("UPDATE assets SET original_name = ? WHERE id = ?", (Path(row["file_path"]).name, row["id"]))
    for column in ("wm_path", "wm_text", "wm_position"):
        if column not in columns:
            conn.execute(f"ALTER TABLE assets ADD COLUMN {column} TEXT")
    post_columns = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
    if "source" not in post_columns:
        conn.execute("ALTER TABLE posts ADD COLUMN source TEXT NOT NULL DEFAULT 'auto'")
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_tasks)")}
    if "params" not in task_columns:
        conn.execute("ALTER TABLE ai_tasks ADD COLUMN params TEXT")

    # 投稿状態は作品の準備状態(assets.status)から分離した(postsテーブル)。
    # 旧ステータスposted/failed_fanvueの作品は、準備状態をreadyへ戻し投稿状態へ移す
    conn.execute(
        """
        INSERT OR IGNORE INTO posts (asset_id, channel, status, url, external_id, posted_at)
        SELECT id, 'fanvue', 'posted', fanvue_url, fanvue_uuid, updated_at FROM assets WHERE status = 'posted'
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO posts (asset_id, channel, status, error, posted_at)
        SELECT id, 'fanvue', 'failed', '(移行前の失敗記録)', updated_at FROM assets WHERE status = 'failed_fanvue'
        """
    )
    conn.execute("UPDATE assets SET status = 'ready' WHERE status IN ('posted', 'failed_fanvue')")


# --- assets -----------------------------------------------------------------

def insert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    conn.execute(
        """
        INSERT INTO assets (
            id, status, kind, file_path, caption, x_caption, fanvue_text,
            audience, price_cents, fanvue_url, fanvue_uuid, x_ok, original_name, content_hash, width, height,
            content_rating, content_rating_confirmed, nsfw_auto_rating,
            nsfw_auto_confidence, content_description, fanvue_caption_draft,
            created_at, updated_at
        ) VALUES (
            :id, :status, :kind, :file_path, :caption, :x_caption, :fanvue_text,
            :audience, :price_cents, :fanvue_url, :fanvue_uuid, :x_ok, :original_name, :content_hash, :width, :height,
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
            "original_name": asset.get("original_name"),
            "content_hash": asset.get("content_hash"),
            "width": asset.get("width"),
            "height": asset.get("height"),
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


NONE_VALUE = "__none__"  # 絞り込みで「未設定(NULL)」を表す値


def _as_list(value) -> list:
    """単一値・リスト・Noneを、空要素を除いたリストにそろえる。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v is not None and v != ""]
    return [value] if value != "" else []


def _like_escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_assets(
    conn: sqlite3.Connection,
    status=None,
    content_rating=None,
    folder_id=None,
    tag=None,
    channel: str | None = None,
    confirmed_only: bool = False,
    kind=None,
    ext=None,
    confirmed=None,
    post_channel: str | None = None,
    post_status: str | None = None,
    post_filters: list[tuple[str, str]] | None = None,
    q: str | None = None,
    nsfw_auto=None,
    plan=None,
    tag_mode: str = "all",
    folder_mode: str = "any",
    broken: str = "hide",
    trashed: bool = False,
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """アセット一覧。絞り込みは項目間でAND。

    `status`/`content_rating`/`confirmed`などはリストで複数指定でき、その項目内はOR
    (例: status=["analyzing", "ready"])。1つの作品が複数持てる`tag`/`folder_id`だけは、
    `tag_mode`/`folder_mode`で"all"(選んだものをすべて含む=AND)か"any"(いずれかを含む=OR)を選べる。
    `post_filters`は[(投稿先, "posted"/"failed"/"none")]のいずれかに該当(OR)。
    `q`は空白区切りの語を、AI生成の内容説明・タグ名・元のファイル名に含むもの(語ごとにAND)。
    """
    query = "SELECT DISTINCT assets.* FROM assets"
    joins = []
    conditions = []
    params: dict = {}

    # ごみ箱に入れた作品は、通常の一覧・投稿・AI処理の対象から外す(trashed=Trueでごみ箱の中身だけ)
    conditions.append("assets.deleted_at IS NOT NULL" if trashed else "assets.deleted_at IS NULL")
    # 破綻画像(キメラ): 既定は一覧・投稿の対象から外す。"show"=含める、"only"=破綻画像だけ。ごみ箱の中では区別しない
    if not trashed:
        if broken == "only":
            conditions.append("assets.is_broken = 1")
        elif broken != "show":
            conditions.append("assets.is_broken = 0")

    def in_clause(column: str, values: list, prefix: str) -> None:
        """IN条件。NONE_VALUE(未設定)が含まれていれば、NULLも対象にする。"""
        names = []
        include_null = False
        for i, v in enumerate(values):
            if v == NONE_VALUE:
                include_null = True
                continue
            params[f"{prefix}{i}"] = v
            names.append(f":{prefix}{i}")
        parts = []
        if names:
            parts.append(f"{column} IN ({', '.join(names)})")
        if include_null:
            parts.append(f"{column} IS NULL")
        conditions.append("(" + " OR ".join(parts) + ")")

    folder_ids = _as_list(folder_id)
    if folder_ids:
        names = []
        for i, v in enumerate(folder_ids):
            params[f"fold{i}"] = v
            names.append(f":fold{i}")
        if folder_mode == "all":
            for n in names:  # 選んだフォルダのすべてに入っている
                conditions.append(
                    f"EXISTS (SELECT 1 FROM asset_folders af WHERE af.asset_id = assets.id AND af.folder_id = {n})"
                )
        else:  # いずれかのフォルダに入っている
            conditions.append(
                "EXISTS (SELECT 1 FROM asset_folders af WHERE af.asset_id = assets.id "
                f"AND af.folder_id IN ({', '.join(names)}))"
            )

    tag_names = _as_list(tag)
    if tag_names:
        names = []
        for i, name in enumerate(tag_names):
            params[f"tag{i}"] = name
            names.append(f":tag{i}")
        if tag_mode == "any":  # いずれかのタグを持つ
            conditions.append(
                "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                f"WHERE at.asset_id = assets.id AND t.name IN ({', '.join(names)}))"
            )
        else:  # 選んだタグをすべて持つ
            for n in names:
                conditions.append(
                    "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                    f"WHERE at.asset_id = assets.id AND t.name = {n})"
                )

    if channel is not None:
        joins.append("JOIN channels ON channels.asset_id = assets.id")
        conditions.append("channels.channel = :channel")
        params["channel"] = channel

    statuses = _as_list(status)
    if statuses:
        in_clause("assets.status", statuses, "st")

    ratings = _as_list(content_rating)
    if ratings:
        in_clause("assets.content_rating", ratings, "rt")

    auto = _as_list(nsfw_auto)
    if auto:
        in_clause("assets.nsfw_auto_rating", auto, "na")

    # 投稿予定: 投稿先(channels)が1つでもあれば"planned"、1つも無ければ"none"(=投稿予定なし)
    plans = set(_as_list(plan))
    if plans == {"planned"}:
        conditions.append("EXISTS (SELECT 1 FROM channels c WHERE c.asset_id = assets.id)")
    elif plans == {"none"}:
        conditions.append("NOT EXISTS (SELECT 1 FROM channels c WHERE c.asset_id = assets.id)")

    if confirmed_only:
        conditions.append("assets.content_rating_confirmed = 1")

    confirmed_values = {bool(v) for v in _as_list(confirmed)}
    if len(confirmed_values) == 1:  # 承認待ち・承認済みの両方を選んだ場合は絞り込まない
        conditions.append(f"assets.content_rating_confirmed = {1 if True in confirmed_values else 0}")

    # ファイルタイプ(2階層): 種別(image/video)と拡張子。両方指定されたときはどちらかに合えばよい(OR)。
    # 種別を選ぶ=その種別の全拡張子を選ぶことと同じ。拡張子はfile_pathの末尾で判定する(小文字・ドットなし)
    kinds = _as_list(kind)
    exts = [str(e).lower().lstrip(".") for e in _as_list(ext)]
    type_parts = []
    if kinds:
        names = []
        for i, k in enumerate(kinds):
            params[f"kind{i}"] = k
            names.append(f":kind{i}")
        type_parts.append(f"assets.kind IN ({', '.join(names)})")
    for i, e in enumerate(exts):
        params[f"ext{i}"] = f"%.{_like_escape(e)}"
        type_parts.append(f"lower(assets.file_path) LIKE :ext{i} ESCAPE '\\'")
    if type_parts:
        conditions.append("(" + " OR ".join(type_parts) + ")")

    # 投稿状態での絞り込み。状態は"posted"/"failed"/"none"(その投稿先へ未投稿)
    pairs = list(post_filters or [])
    if post_channel is not None and post_status is not None:
        pairs.append((post_channel, post_status))
    if pairs:
        ors = []
        for i, (ch, st) in enumerate(pairs):
            params[f"pc{i}"] = ch
            if st == "none":
                ors.append(f"NOT EXISTS (SELECT 1 FROM posts p WHERE p.asset_id = assets.id AND p.channel = :pc{i})")
            else:
                params[f"ps{i}"] = st
                ors.append(
                    "EXISTS (SELECT 1 FROM posts p WHERE p.asset_id = assets.id "
                    f"AND p.channel = :pc{i} AND p.status = :ps{i})"
                )
        conditions.append("(" + " OR ".join(ors) + ")")

    # フリーテキスト検索: AI生成の内容説明、またはタグ名に含まれる語(語ごとにAND)
    for i, word in enumerate((q or "").split()):
        params[f"q{i}"] = f"%{_like_escape(word)}%"
        conditions.append(
            f"(assets.content_description LIKE :q{i} ESCAPE '\\' OR assets.original_name LIKE :q{i} ESCAPE '\\' OR EXISTS ("
            "SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id = assets.id AND t.name LIKE :q{i} ESCAPE '\\'))"
        )

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


# --- posts（投稿先ごとの投稿状態) ---------------------------------------------

def set_post(
    conn: sqlite3.Connection,
    asset_id: str,
    channel: str,
    status: str,
    url: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
    posted_at: str | None = None,
    source: str = "auto",
) -> None:
    """投稿結果を記録する(作品x投稿先で1行。再投稿時は上書き)。

    `source`は"auto"(アプリが投稿)か"manual"(手動で投稿した記録)。`posted_at`で投稿日時(ISO8601)を指定できる。
    """
    conn.execute(
        """
        INSERT INTO posts (asset_id, channel, status, url, external_id, error, posted_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset_id, channel) DO UPDATE SET
            status = excluded.status, url = excluded.url, external_id = excluded.external_id,
            error = excluded.error, posted_at = excluded.posted_at, source = excluded.source
        """,
        (asset_id, channel, status, url, external_id, error, posted_at or _now_iso(), source),
    )
    conn.commit()


def delete_post(conn: sqlite3.Connection, asset_id: str, channel: str) -> None:
    conn.execute("DELETE FROM posts WHERE asset_id = ? AND channel = ?", (asset_id, channel))
    conn.commit()


def get_posts(conn: sqlite3.Connection, asset_ids: list[str]) -> dict[str, dict[str, dict]]:
    """{asset_id: {channel: {"status","url","error","posted_at",...}}}。行が無い投稿先は含まれない。"""
    result: dict[str, dict[str, dict]] = {}
    if not asset_ids:
        return result
    marks = ",".join("?" for _ in asset_ids)
    rows = conn.execute(f"SELECT * FROM posts WHERE asset_id IN ({marks})", asset_ids).fetchall()
    for row in rows:
        result.setdefault(row["asset_id"], {})[row["channel"]] = dict(row)
    return result


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


def set_channels(conn: sqlite3.Connection, asset_id: str, channels: list[str]) -> None:
    """投稿先を置き換える。空なら「投稿予定なし」(どのSNS投稿の対象にもならない)。"""
    conn.execute("DELETE FROM channels WHERE asset_id = ?", (asset_id,))
    for channel in dict.fromkeys(channels):
        conn.execute("INSERT INTO channels (asset_id, channel) VALUES (?, ?)", (asset_id, channel))
    conn.commit()


def get_channels_map(conn: sqlite3.Connection, asset_ids: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {asset_id: [] for asset_id in asset_ids}
    if not asset_ids:
        return result
    marks = ",".join("?" for _ in asset_ids)
    for row in conn.execute(f"SELECT asset_id, channel FROM channels WHERE asset_id IN ({marks})", asset_ids):
        result[row["asset_id"]].append(row["channel"])
    return result


# --- ごみ箱 -----------------------------------------------------------------

def trash_assets(conn: sqlite3.Connection, asset_ids: list[str]) -> int:
    """作品をごみ箱へ移す(論理削除。ファイル・タグ・投稿記録はそのまま残る)。"""
    moved = 0
    for asset_id in asset_ids:
        cur = conn.execute(
            "UPDATE assets SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (_now_iso(), asset_id)
        )
        if cur.rowcount:
            moved += 1
            # 待機中のAI処理は不要になるので取り消す(実行中のものは終わるまで待つ)
            conn.execute("DELETE FROM ai_tasks WHERE asset_id = ? AND status = 'queued'", (asset_id,))
    conn.commit()
    return moved


def restore_assets(conn: sqlite3.Connection, asset_ids: list[str]) -> int:
    restored = 0
    for asset_id in asset_ids:
        cur = conn.execute(
            "UPDATE assets SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL", (asset_id,)
        )
        restored += cur.rowcount
    conn.commit()
    return restored


def count_trashed(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM assets WHERE deleted_at IS NOT NULL").fetchone()[0]


# --- 絞り込み候補(実在する値の一覧と件数) -------------------------------------

def set_broken(conn: sqlite3.Connection, asset_ids: list[str], broken: bool) -> int:
    """破綻画像(キメラ)のフラグを付け外しする。待機中のAI処理は不要になるので取り消す。"""
    changed = 0
    for asset_id in asset_ids:
        cur = conn.execute(
            "UPDATE assets SET is_broken = ? WHERE id = ? AND is_broken != ?",
            (1 if broken else 0, asset_id, 1 if broken else 0),
        )
        if cur.rowcount:
            changed += 1
            if broken:
                conn.execute("DELETE FROM ai_tasks WHERE asset_id = ? AND status = 'queued'", (asset_id,))
    conn.commit()
    return changed


def facets(conn: sqlite3.Connection, channels: list[str], broken: str = "hide") -> dict[str, list[tuple]]:
    """一覧の絞り込み候補。ごみ箱以外の作品に実在する値だけを、件数つきで返す。

    値が未設定(NULL)のものは`NONE_VALUE`として含める。`channels`は投稿状態の候補にする投稿先。
    """
    live = "FROM assets WHERE deleted_at IS NULL"
    if broken == "only":  # 件数は、いま一覧に出す範囲(破綻画像の扱い)に合わせる
        live += " AND is_broken = 1"
    elif broken != "show":
        live += " AND is_broken = 0"
    live_ids = "SELECT id " + live

    def grouped(column: str) -> list[tuple]:
        rows = conn.execute(f"SELECT {column} AS v, COUNT(*) AS n {live} GROUP BY {column} ORDER BY v IS NULL, v").fetchall()
        return [(NONE_VALUE if r["v"] is None else r["v"], r["n"]) for r in rows]

    result = {
        "status": grouped("status"),
        "content_rating": grouped("content_rating"),
        "nsfw_auto": grouped("nsfw_auto_rating"),
        "confirmed": [(str(r["v"]), r["n"]) for r in conn.execute(
            f"SELECT content_rating_confirmed AS v, COUNT(*) AS n {live} GROUP BY 1 ORDER BY 1")],
    }
    planned = conn.execute(
        f"SELECT COUNT(*) {live} AND EXISTS (SELECT 1 FROM channels c WHERE c.asset_id = assets.id)"
    ).fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) {live}").fetchone()[0]
    # ファイルタイプ: 種別ごとに、実在する拡張子と件数を並べる
    tree: dict[str, dict] = {}
    for r in conn.execute(f"SELECT kind, file_path {live}"):
        node = tree.setdefault(r["kind"], {"count": 0, "exts": {}})
        node["count"] += 1
        suffix = r["file_path"].rsplit(".", 1)[-1].lower() if "." in r["file_path"] else ""
        node["exts"][suffix] = node["exts"].get(suffix, 0) + 1
    result["type"] = [
        (kind, node["count"], sorted(node["exts"].items(), key=lambda x: (-x[1], x[0])))
        for kind, node in sorted(tree.items())
    ]
    result["plan"] = [(k, n) for k, n in (("planned", planned), ("none", total - planned)) if n > 0]
    result["tag"] = [(r["name"], r["n"]) for r in conn.execute(
        "SELECT t.name AS name, COUNT(DISTINCT a.id) AS n FROM tags t "
        "JOIN asset_tags at ON at.tag_id = t.id JOIN assets a ON a.id = at.asset_id "
        f"WHERE a.id IN ({live_ids}) GROUP BY t.name ORDER BY t.name")]
    result["folder"] = [(r["id"], r["name"], r["n"]) for r in conn.execute(
        "SELECT f.id AS id, f.name AS name, COUNT(DISTINCT a.id) AS n FROM folders f "
        "JOIN asset_folders af ON af.folder_id = f.id JOIN assets a ON a.id = af.asset_id "
        f"WHERE a.id IN ({live_ids}) GROUP BY f.id ORDER BY f.name")]
    post = []
    for ch in channels:
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT p.status AS status, COUNT(*) AS n FROM posts p JOIN assets a ON a.id = p.asset_id "
            f"WHERE a.id IN ({live_ids}) AND p.channel = ? GROUP BY p.status", (ch,))}
        counts["none"] = total - sum(counts.values())
        post += [(ch, st, n) for st, n in counts.items() if n > 0]
    result["post"] = post
    return result


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


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


def list_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


# --- ai_tasks（AI処理の非同期キュー） ------------------------------------------

AI_TASK_KINDS = ("nsfw", "describe")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def enqueue_ai_task(conn: sqlite3.Connection, asset_id: str, kind: str, params: dict | None = None) -> bool:
    """処理(AI・透かし)をキューに積む。同じアセット・種別が待機中/実行中ならFalse(二重登録しない)。

    `params`はタスクごとの設定(透かしの文字・位置など)。JSONで保存する。
    """
    active = conn.execute(
        "SELECT 1 FROM ai_tasks WHERE asset_id = ? AND kind = ? AND status IN ('queued', 'running')",
        (asset_id, kind),
    ).fetchone()
    if active:
        return False
    # 過去の完了/失敗の履歴は最新1件だけ残せば十分なので、積み直すときに消す
    conn.execute(
        "DELETE FROM ai_tasks WHERE asset_id = ? AND kind = ? AND status IN ('done', 'failed')",
        (asset_id, kind),
    )
    conn.execute(
        "INSERT INTO ai_tasks (asset_id, kind, status, params, created_at) VALUES (?, ?, 'queued', ?, ?)",
        (asset_id, kind, json.dumps(params, ensure_ascii=False) if params else None, _now_iso()),
    )
    conn.commit()
    return True


def claim_next_ai_task(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM ai_tasks WHERE status = 'queued' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE ai_tasks SET status = 'running' WHERE id = ?", (row["id"],))
    conn.commit()
    return dict(row)


def finish_ai_task(conn: sqlite3.Connection, task_id: int, error: str | None = None) -> None:
    conn.execute(
        "UPDATE ai_tasks SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        ("failed" if error else "done", error, _now_iso(), task_id),
    )
    conn.commit()


def requeue_running_ai_tasks(conn: sqlite3.Connection) -> None:
    """ワーカーが異常終了して実行中のまま残ったタスクを待機中へ戻す。"""
    conn.execute("UPDATE ai_tasks SET status = 'queued' WHERE status = 'running'")
    conn.commit()


def list_ai_tasks(conn: sqlite3.Connection, status: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM ai_tasks WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def ai_task_counts(conn: sqlite3.Connection) -> dict:
    counts = {"queued": 0, "running": 0, "failed": 0}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM ai_tasks GROUP BY status"):
        if row["status"] in counts:
            counts[row["status"]] = row["n"]
    return counts


def running_ai_task(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM ai_tasks WHERE status = 'running' ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


def ai_task_states(conn: sqlite3.Connection, asset_ids: list[str]) -> dict[str, dict]:
    """アセットごとの{kind: {"status", "error"}}(最新のタスク)を返す。"""
    states: dict[str, dict] = {}
    if not asset_ids:
        return states
    marks = ",".join("?" for _ in asset_ids)
    rows = conn.execute(
        f"SELECT asset_id, kind, status, error FROM ai_tasks WHERE asset_id IN ({marks}) ORDER BY id",
        asset_ids,
    ).fetchall()
    for row in rows:  # idの昇順なので後勝ち=最新
        states.setdefault(row["asset_id"], {})[row["kind"]] = {
            "status": row["status"],
            "error": row["error"],
        }
    return states


def unprocessed_asset_ids(conn: sqlite3.Connection, kind: str, since_iso: str | None = None) -> list[str]:
    """kindの処理結果がまだ無いアセットID(古い順)。`since_iso`以降に登録されたものに絞れる。"""
    column = "nsfw_auto_rating" if kind == "nsfw" else "content_description"
    sql = f"SELECT id FROM assets WHERE {column} IS NULL AND deleted_at IS NULL AND is_broken = 0"
    params: list = []
    if since_iso:
        sql += " AND created_at >= ?"
        params.append(since_iso)
    sql += " ORDER BY created_at"
    return [row["id"] for row in conn.execute(sql, params)]
