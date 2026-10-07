"""同じ画像・動画(ファイルの内容が完全に同じもの)の検出と、その扱い。

- 中身のSHA-256を`assets.content_hash`に持ち、同じ値の作品を「重複」とする(再エンコードや縮小で中身が変わったものは、
  別のファイルとして扱う。見た目が似ているだけのものまでは、検出しない)
- アップロード時: 既にある作品と同じ中身のファイルは、取り込まずに保留(`inbox/.dup-pending/`)にして、取り込むかどうかを聞く
- 登録済みの重複: インボックスからの取り込み等で後から増えた重複は、定期処理で検出して通知し、まとめて扱いを決められる
- ユーザーが「重複のまま残す」と決めたものは、`dup_ignores`に記録して、二度と通知しない
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from core import db, notifications
from core.config import Config

STAGING_DIRNAME = ".dup-pending"
STALE_SECONDS = 48 * 3600  # 保留のまま、これより長く放置されたファイルは、片付ける


def file_hash(path: Path) -> str | None:
    """ファイルの中身のSHA-256。読めなければNone。"""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def backfill_hashes(conn: sqlite3.Connection) -> int:
    """中身のハッシュが未記録の作品について、ファイルから計算して記録する(ファイルが見えない作品は、あとで再試行)。"""
    updated = 0
    for row in conn.execute("SELECT id, file_path FROM assets WHERE content_hash IS NULL").fetchall():
        value = file_hash(Path(row["file_path"]))
        if value:
            conn.execute("UPDATE assets SET content_hash = ? WHERE id = ?", (value, row["id"]))
            updated += 1
    conn.commit()
    return updated


# ---------------------------------------------------------------- 登録済みの重複

def _ignored(conn: sqlite3.Connection) -> set[str]:
    return {r["content_hash"] for r in conn.execute("SELECT content_hash FROM dup_ignores")}


def ignore_hash(conn: sqlite3.Connection, content_hash: str) -> None:
    conn.execute("INSERT OR IGNORE INTO dup_ignores (content_hash) VALUES (?)", (content_hash,))
    conn.commit()


def assets_with_hash(conn: sqlite3.Connection, content_hash: str) -> list[dict]:
    """同じ中身の作品(ごみ箱の中も含む)。"""
    rows = conn.execute(
        "SELECT id, original_name, created_at, status, deleted_at, is_broken, content_rating_confirmed "
        "FROM assets WHERE content_hash = ? ORDER BY created_at",
        (content_hash,),
    ).fetchall()
    return [dict(r) for r in rows]


def _keep_score(conn: sqlite3.Connection, asset: dict) -> tuple:
    """残す候補の優先度(大きいほど残す): 投稿の記録がある・区分を承認済み・タグが多い・古い。"""
    posts = conn.execute("SELECT COUNT(*) FROM posts WHERE asset_id = ?", (asset["id"],)).fetchone()[0]
    tags = conn.execute("SELECT COUNT(*) FROM asset_tags WHERE asset_id = ?", (asset["id"],)).fetchone()[0]
    return (posts > 0, bool(asset["content_rating_confirmed"]), tags)  # 同点なら、取り込みが古いほう(作品は古い順に並べて渡す)


def duplicate_groups(conn: sqlite3.Connection) -> list[dict]:
    """登録済み(ごみ箱以外)の重複グループ。ユーザーが「残す」と決めたものは除く。"""
    ignored = _ignored(conn)
    hashes = [
        r["content_hash"]
        for r in conn.execute(
            "SELECT content_hash FROM assets WHERE content_hash IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY content_hash HAVING COUNT(*) > 1 ORDER BY MIN(created_at)"
        )
        if r["content_hash"] not in ignored
    ]
    groups = []
    for h in hashes:
        members = [a for a in assets_with_hash(conn, h) if not a["deleted_at"]]
        if len(members) < 2:
            continue
        keep = max(members, key=lambda a: _keep_score(conn, a))["id"]
        for a in members:
            a["posted_to"] = [r["channel"] for r in conn.execute("SELECT channel FROM posts WHERE asset_id = ?", (a["id"],))]
            a["keep"] = a["id"] == keep  # 既定で残す作品
        groups.append({"hash": h, "assets": members})
    return groups


def notify_new_duplicates(conn: sqlite3.Connection) -> int:
    """未通知の重複グループがあれば、通知を1件出す(同じ内容を繰り返し通知しない)。出した場合は、グループ数を返す。"""
    hashes = sorted(g["hash"] for g in duplicate_groups(conn))
    notified = set(json.loads(db.get_setting(conn, "dup_notified") or "[]"))
    fresh = [h for h in hashes if h not in notified]
    db.set_setting(conn, "dup_notified", json.dumps(hashes))  # 解決済みのものは、次回から数えない
    if not fresh:
        return 0
    notifications.add(
        conn, "duplicates", f"重複している画像が{len(hashes)}組あります",
        "同じ内容のファイルが複数登録されています。クリックして、扱いをまとめて決められます", "warning", action="duplicates",
    )
    return len(hashes)


def resolve_groups(conn: sqlite3.Connection, decisions: list[dict]) -> dict:
    """登録済みの重複グループの扱いを実行する。

    decision: {"hash", "action": "trash_others"(残す1つ以外をごみ箱へ)/"keep_all"(重複のまま残す), "keep_id"}
    """
    trashed = ignored = 0
    for d in decisions:
        members = [a for a in assets_with_hash(conn, d.get("hash", "")) if not a["deleted_at"]]
        if len(members) < 2:
            continue
        if d.get("action") == "keep_all":
            ignore_hash(conn, d["hash"])
            ignored += 1
        elif d.get("action") == "trash_others":
            keep_id = d.get("keep_id")
            if keep_id not in {a["id"] for a in members}:
                continue
            trashed += db.trash_assets(conn, [a["id"] for a in members if a["id"] != keep_id])
    return {"trashed": trashed, "kept_all": ignored}


# ---------------------------------------------------------------- アップロード時の保留

def staging_root(config: Config) -> Path:
    return config.paths.inbox / STAGING_DIRNAME


def _manifest(token_dir: Path) -> list[dict]:
    try:
        return json.loads((token_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def stage_duplicate(config: Config, token: str, src: Path, content_hash: str, existing_ids: list[str], same_as: str | None) -> dict:
    """重複したアップロードを、取り込まずに保留の場所へ移す。"""
    token_dir = staging_root(config) / token
    token_dir.mkdir(parents=True, exist_ok=True)
    name = src.name
    shutil.move(str(src), str(token_dir / name))
    entries = _manifest(token_dir)
    entry = {"name": name, "hash": content_hash, "existing": existing_ids, "same_as": same_as}
    entries.append(entry)
    (token_dir / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return entry


def new_token() -> str:
    return uuid.uuid4().hex[:16]


def list_pending(config: Config, conn: sqlite3.Connection) -> list[dict]:
    """保留中の(取り込むかどうか未決定の)アップロード。既にある作品の情報つき。"""
    root = staging_root(config)
    items: list[dict] = []
    if not root.is_dir():
        return items
    for token_dir in sorted(root.iterdir()):
        if not token_dir.is_dir():
            continue
        for e in _manifest(token_dir):
            if not (token_dir / e["name"]).exists():
                continue
            existing = [a for a in assets_with_hash(conn, e["hash"])]
            items.append({"token": token_dir.name, **e, "existing_assets": existing})
    return items


def pending_file(config: Config, token: str, name: str) -> Path | None:
    """保留中のファイルのパス(プレビュー用)。トークン・名前に、パスの区切りなどが入っていれば、None。"""
    if not token.isalnum() or "/" in name or "\\" in name or name in ("", ".", "..") or name == "manifest.json":
        return None
    path = staging_root(config) / token / name
    return path if path.is_file() else None


def resolve_uploads(config: Config, conn: sqlite3.Connection, decisions: list[dict]) -> dict:
    """保留中のアップロードの扱いを実行する。

    decision: {"token", "name", "action": "import"(別の作品として取り込む)/"skip"(取り込まない=破棄)}
    """
    from core.ingest import ingest_inbox  # 循環importを避ける

    imported = skipped = 0
    inbox = config.paths.inbox
    for d in decisions:
        path = pending_file(config, d.get("token", ""), d.get("name", ""))
        if path is None:
            continue
        entry = next((e for e in _manifest(path.parent) if e["name"] == path.name), None)
        if d.get("action") == "import":
            dest = inbox / path.name
            n = 1
            while dest.exists():
                dest = inbox / f"{path.stem}-{n}{path.suffix}"
                n += 1
            shutil.move(str(path), str(dest))
            if entry:
                ignore_hash(conn, entry["hash"])  # 「取り込む」と決めた重複は、あとで通知しない
            imported += 1
        else:
            path.unlink(missing_ok=True)
            skipped += 1
        # 決めたものは、保留の記録から外す
        remaining = [e for e in _manifest(path.parent) if e["name"] != path.name]
        (path.parent / "manifest.json").write_text(json.dumps(remaining, ensure_ascii=False), encoding="utf-8")
        if not remaining:
            shutil.rmtree(path.parent, ignore_errors=True)
    results = ingest_inbox(config, conn, defer_analysis=True) if imported else []
    return {"imported": imported, "skipped": skipped, "ingested": len(results)}


def cleanup_stale(config: Config) -> int:
    """長く放置された保留のファイルを片付ける。片付けたディレクトリの数を返す。"""
    root = staging_root(config)
    removed = 0
    if root.is_dir():
        for token_dir in root.iterdir():
            try:
                if token_dir.is_dir() and time.time() - token_dir.stat().st_mtime > STALE_SECONDS:
                    shutil.rmtree(token_dir, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    return removed
