"""ストレージ(画像・動画のライブラリ)の場所の管理。

ライブラリ(`inbox/` `ready/<作品ID>/` など)は、任意のドライブ・フォルダに置ける。
リムーバブルメディアなど、起動時に見つからないことがある場所も扱えるよう、次の方針とする。

- 場所は設定画面で変更でき、`settings`テーブル(`storage_root`)に保存する。データベース(state)は
  常にローカルに置くので、ストレージが見つからなくてもアプリは起動でき、設定で場所を直せる
- 見つからないとき(`available=False`)は、画像なしで起動する。投稿・AI処理・取り込みは止め、
  待機中の処理はそのまま残す(失敗にはしない)
- 別のドライブが同じドライブ文字で挿さっても取り違えないよう、ライブラリに目印のファイル
  (`.reelmilly-library`、中身はライブラリID)を置いて確認する
- DBの`file_path`/`wm_path`は絶対パスのまま持つ。場所を変えるときは、`<新しい場所>/ready/<作品ID>/<ファイル名>`
  へ付け替える(ライブラリ内は、この配置が決まっているため)
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core import db
from core.config import Config
from core.events import log_event

MARKER = ".reelmilly-library"
SETTING_ROOT = "storage_root"
SETTING_ID = "storage_id"
SETTING_JOB = "storage_job"
LIBRARY_SUBDIRS = ("inbox", "ready", "posted", "x_only")
JOB_STALE_SECONDS = 1800  # 移行ジョブの更新がこれより止まっていたら、異常終了とみなす(巨大な動画1本のコピーも見込む)


class StorageError(Exception):
    """ユーザーに理由を伝えられる、ストレージ操作の失敗。"""


@dataclass
class StorageStatus:
    root: Path
    available: bool
    reason: str = ""  # 見つからない理由(画面に出す)
    code: str = "ok"  # ok / missing / wrong / empty / migrating
    free_bytes: int | None = None
    total_bytes: int | None = None
    is_default: bool = True
    asset_files: int = 0
    asset_files_found: int = 0

    def as_dict(self) -> dict:
        return {
            "root": str(self.root), "available": self.available, "reason": self.reason, "code": self.code,
            "free_bytes": self.free_bytes, "total_bytes": self.total_bytes, "is_default": self.is_default,
            "asset_files": self.asset_files, "asset_files_found": self.asset_files_found,
        }


# ---------------------------------------------------------------- ライブラリID・場所の設定

def library_id(conn: sqlite3.Connection) -> str:
    value = db.get_setting(conn, SETTING_ID)
    if not value:
        value = uuid.uuid4().hex
        db.set_setting(conn, SETTING_ID, value)
    return value


def apply_override(config: Config, conn: sqlite3.Connection) -> None:
    """設定画面で保存した場所があれば、configのライブラリの場所へ反映する(プロセスごと・リクエストごとに呼ぶ)。"""
    saved = db.get_setting(conn, SETTING_ROOT)
    target = Path(saved) if saved else (config.default_root or config.paths.root)
    if target != config.paths.root:
        config.paths = config.paths.with_library_root(target)


def is_default_root(config: Config) -> bool:
    return config.default_root is None or config.paths.root == config.default_root


def _read_marker(root: Path) -> str | None:
    try:
        return (root / MARKER).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_marker(root: Path, lib_id: str) -> None:
    (root / MARKER).write_text(lib_id, encoding="utf-8")


def _asset_library_files(conn: sqlite3.Connection, root: Path) -> list[Path]:
    """DBにある作品のファイルが、いまのライブラリの場所にあるはずのパス(`<root>/ready/<ID>/<名前>`)。"""
    paths = []
    for row in conn.execute("SELECT id, file_path FROM assets"):
        p = Path(row["file_path"])
        if p.parent.name == row["id"]:
            paths.append(root / "ready" / row["id"] / p.name)
    return paths


def check(config: Config, conn: sqlite3.Connection) -> StorageStatus:
    """ストレージが使える状態かを調べる。見つからなくても例外にはしない。"""
    root = config.paths.root
    status = StorageStatus(root=root, available=False, is_default=is_default_root(config))
    job = current_job(conn)
    if job and job.get("state") == "running":
        status.code, status.reason = "migrating", "ストレージの移行中です。完了するまでお待ちください"
        status.available = False
        return status
    try:
        exists = root.is_dir()
    except OSError:
        exists = False
    if not exists:
        status.code = "missing"
        status.reason = f"ストレージの場所が見つかりません: {root}（リムーバブルメディアなら、接続を確認してください）"
        return status

    try:
        usage = shutil.disk_usage(root)
        status.free_bytes, status.total_bytes = usage.free, usage.total
    except OSError:
        pass

    files = _asset_library_files(conn, root)
    found = sum(1 for p in files if p.exists())
    status.asset_files, status.asset_files_found = len(files), found

    lib_id = library_id(conn)
    marker = _read_marker(root)
    if marker == lib_id:
        status.available = True
        return status
    if marker is None:
        # 目印がまだ無い(旧バージョンで作った、またはコピーしてきた)場所。作品がまだ無いか、作品のファイルが
        # この場所にあるなら、このライブラリの場所とみなして目印を付ける
        if not files or found > 0 or (status.is_default and root.exists()):
            try:
                write_marker(root, lib_id)
            except OSError:
                pass  # 読み取り専用のメディア等。目印なしでも使えるようにする
            status.available = True
            return status
        status.code = "empty"
        status.reason = f"この場所に、作品のファイルが見つかりません: {root}（別のドライブや空のフォルダではありませんか？）"
        return status
    status.code = "wrong"
    status.reason = f"この場所は、別のライブラリのものです: {root}（ドライブ文字が変わった可能性があります）"
    return status


# ---------------------------------------------------------------- ドライブ・フォルダの一覧

def list_drives() -> list[dict]:
    """ドライブの一覧(Windows)。リムーバブルメディアか、空き容量を添える。それ以外の環境は主なマウント先。"""
    drives: list[dict] = []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        mask = kernel32.GetLogicalDrives()
        names = {2: "リムーバブル", 3: "固定ディスク", 4: "ネットワーク", 5: "光学ドライブ", 6: "RAMディスク"}
        for i in range(26):
            if not mask & (1 << i):
                continue
            letter = f"{chr(65 + i)}:\\"
            kind = kernel32.GetDriveTypeW(letter)
            label_buf = ctypes.create_unicode_buffer(261)
            ok = kernel32.GetVolumeInformationW(letter, label_buf, 261, None, None, None, None, 0)
            entry = {"path": letter, "type": names.get(kind, "不明"), "removable": kind == 2,
                     "label": label_buf.value if ok else "", "ready": bool(ok)}
            if ok:
                try:
                    usage = shutil.disk_usage(letter)
                    entry["free_bytes"], entry["total_bytes"] = usage.free, usage.total
                except OSError:
                    entry["ready"] = False
            drives.append(entry)
    else:
        for base in ("/", "/mnt", "/media", "/Volumes"):
            p = Path(base)
            if p.is_dir():
                drives.append({"path": str(p), "type": "マウント", "removable": base != "/", "label": "", "ready": True})
    return drives


def browse(path: str) -> dict:
    """フォルダの中のフォルダ一覧(ファイルは出さない)。フォルダ選択画面用。"""
    p = Path(path)
    if not path or not p.is_absolute():
        raise StorageError("絶対パスを指定してください（例: E:\\Reelmilly）")
    if not p.is_dir():
        raise StorageError(f"フォルダが見つかりません: {path}")
    folders = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir() and not child.name.startswith((".", "$")) and child.name != "System Volume Information":
                    folders.append(child.name)
            except OSError:
                continue
    except OSError as exc:
        raise StorageError(f"フォルダを読めません: {exc}") from exc
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "folders": folders}


# ---------------------------------------------------------------- 場所の変更(移行)

def current_job(conn: sqlite3.Connection) -> dict | None:
    raw = db.get_setting(conn, SETTING_JOB)
    if not raw:
        return None
    try:
        job = json.loads(raw)
    except ValueError:
        return None
    if job.get("state") == "running" and time.time() - job.get("heartbeat", 0) > JOB_STALE_SECONDS:
        job["state"] = "failed"
        job["message"] = "移行が途中で止まりました（再度実行してください）"
    return job


def _save_job(conn: sqlite3.Connection, **fields) -> None:
    job = json.loads(db.get_setting(conn, SETTING_JOB) or "{}")
    job.update(fields)
    job["heartbeat"] = time.time()
    db.set_setting(conn, SETTING_JOB, json.dumps(job, ensure_ascii=False))


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_new_root(raw: str, mode: str, old_root: Path) -> Path:
    """入力された場所を検証する。問題があれば、理由つきでStorageErrorを送出する。"""
    raw = (raw or "").strip().strip('"')
    if not raw:
        raise StorageError("場所を入力してください")
    new_root = Path(raw)
    if not new_root.is_absolute():
        raise StorageError("絶対パスで指定してください（例: E:\\Reelmilly、D:\\media\\library）")
    if mode == "move":
        if new_root.resolve() == old_root.resolve():
            raise StorageError("いまと同じ場所です")
        if _within(new_root, old_root) or _within(old_root, new_root):
            raise StorageError("いまの場所の中、またはその外側のフォルダは、移動先にできません")
        anchor = Path(new_root.anchor)
        if new_root.anchor and not anchor.exists():
            raise StorageError(f"ドライブが見つかりません: {new_root.anchor}")
    else:
        if not new_root.is_dir():
            raise StorageError(f"フォルダが見つかりません: {new_root}")
    return new_root


def rewrite_paths(conn: sqlite3.Connection, new_root: Path) -> int:
    """作品のファイルのパス(file_path・wm_path)を、新しい場所へ付け替える。変えた作品の数を返す。

    ライブラリ内の配置(`ready/<作品ID>/<ファイル名>`)にある作品だけが対象で、それ以外の場所にある
    ファイルのパスは変えない。
    """
    changed = 0
    for row in conn.execute("SELECT id, file_path, wm_path FROM assets").fetchall():
        updates = {}
        for column in ("file_path", "wm_path"):
            value = row[column]
            if value and Path(value).parent.name == row["id"]:
                target = str(new_root / "ready" / row["id"] / Path(value).name)
                if target != value:
                    updates[column] = target
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE assets SET {sets} WHERE id = ?", (*updates.values(), row["id"]))
            changed += 1
    conn.commit()
    return changed


def _tree_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.name != MARKER]


def change_root(
    config: Config,
    conn: sqlite3.Connection,
    raw_path: str,
    mode: str,
    delete_source: bool = False,
) -> dict:
    """ストレージの場所を変更する。

    mode="move": いまのライブラリをコピーして新しい場所へ移し、切り替える(完了後に、希望すれば元を削除)。
    mode="use" : すでにファイルがある場所(ドライブ文字が変わった、バックアップを戻した、等)を指す。コピーはしない。
    """
    if mode not in ("move", "use"):
        raise StorageError("方法が正しくありません")
    old_root = config.paths.root
    new_root = validate_new_root(raw_path, mode, old_root)
    lib_id = library_id(conn)
    _save_job(conn, state="running", mode=mode, source=str(old_root), target=str(new_root),
              done_bytes=0, total_bytes=0, message="準備中")

    try:
        if mode == "move":
            if not old_root.is_dir():
                raise StorageError(
                    "いまの場所が見つからないため、コピーできません。ファイルのある場所を指す「この場所を使う」を使ってください"
                )
            files = _tree_files(old_root)
            total = sum(f.stat().st_size for f in files)
            usage = shutil.disk_usage(new_root if new_root.exists() else Path(new_root.anchor or "."))
            if usage.free < total + 64 * 1024 * 1024:
                raise StorageError(f"移動先の空き容量が足りません（必要: {total // 1024 // 1024}MB、空き: {usage.free // 1024 // 1024}MB）")
            for sub in LIBRARY_SUBDIRS:
                (new_root / sub).mkdir(parents=True, exist_ok=True)
            _save_job(conn, total_bytes=total, message=f"コピー中（{len(files)}ファイル）")

            done = 0
            last = time.time()
            for src in files:
                dst = new_root / src.relative_to(old_root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not (dst.exists() and dst.stat().st_size == src.stat().st_size):
                    shutil.copy2(src, dst)
                if dst.stat().st_size != src.stat().st_size:
                    raise StorageError(f"コピーの確認に失敗しました: {src.name}")
                done += src.stat().st_size
                if time.time() - last > 1:
                    _save_job(conn, done_bytes=done)
                    last = time.time()
            _save_job(conn, done_bytes=done, message="切り替え中")
        else:
            for sub in LIBRARY_SUBDIRS:
                (new_root / sub).mkdir(exist_ok=True)

        write_marker(new_root, lib_id)
        changed = rewrite_paths(conn, new_root)
        db.set_setting(conn, SETTING_ROOT, str(new_root))
        config.paths = config.paths.with_library_root(new_root)

        removed = 0
        if mode == "move" and delete_source:
            for src in files:
                try:
                    src.unlink()
                    removed += 1
                except OSError:
                    pass
            for sub in LIBRARY_SUBDIRS:  # 空になったフォルダだけを片付ける(ファイルが残っていれば消さない)
                folder = old_root / sub
                if folder.is_dir() and not any(p.is_file() for p in folder.rglob("*")):
                    shutil.rmtree(folder, ignore_errors=True)
            (old_root / MARKER).unlink(missing_ok=True)
        log_event(config.paths.events_path, "storage_changed", mode=mode, source=str(old_root),
                  target=str(new_root), assets=changed, removed_source_files=removed)
        result = {"mode": mode, "root": str(new_root), "assets_updated": changed, "removed_source_files": removed}
        _save_job(conn, state="done", message="完了しました", result=result)
        return result
    except Exception as exc:
        _save_job(conn, state="failed", message=str(exc))
        if isinstance(exc, StorageError):
            raise
        raise StorageError(f"ストレージの変更に失敗しました: {exc}") from exc


_thread_lock = threading.Lock()


def start_change_in_background(config: Config, db_path: Path, raw_path: str, mode: str, delete_source: bool) -> None:
    """時間のかかる移行を、別スレッドで始める(進捗は`current_job`で読む)。すぐに検証だけして、問題があれば例外にする。"""
    conn = db.get_connection(db_path)
    try:
        job = current_job(conn)
        if job and job.get("state") == "running":
            raise StorageError("すでに移行中です")
        validate_new_root(raw_path, mode, config.paths.root)
        _save_job(conn, state="running", mode=mode, target=raw_path, message="開始します", done_bytes=0, total_bytes=0)
    finally:
        conn.close()

    def run() -> None:
        thread_conn = db.get_connection(db_path)
        try:
            with _thread_lock:
                change_root(config, thread_conn, raw_path, mode, delete_source)
        except StorageError:
            pass  # 失敗の理由は、ジョブの状態として保存済み
        finally:
            thread_conn.close()

    threading.Thread(target=run, name="storage-change", daemon=True).start()


def usage_summary(conn: sqlite3.Connection, root: Path) -> dict:
    """ライブラリの使用量(作品数・ファイルサイズの合計)。"""
    files = _asset_library_files(conn, root)
    total = 0
    found = 0
    for p in files:
        try:
            total += p.stat().st_size
            found += 1
        except OSError:
            continue
    return {"assets": len(files), "found": found, "bytes": total}
