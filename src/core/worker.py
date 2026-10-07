"""AI処理ワーカー: キューに積まれたsfw/nsfw判定・説明文生成(+タグ付与)を処理する。

Web UIとは別プロセス(`reelmilly watch`/`reelmilly analyze`)で動かす想定。
- キュー: UIの操作・定期実行・`reelmilly analyze`が`ai_tasks`に積み、ワーカーが順に処理する
- 排他: 複数のワーカーが同時に動いて二重処理しないよう、`settings`テーブルのロック
  (ハートビート付き)で1プロセスだけに制限する。`watch`は常駐中ロックを保持する
- 進捗: ロック兼ハートビートに「処理中のアセットID・種別」を書き、UIが参照できるようにする
- モデルの使い回し: 数GB級のモデルを毎回ロードしないよう、種別ごとに遅延ロードして保持する
- 定期実行: 設定(時刻・対象範囲)に従い、未処理のアセットをキューへ積む
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import analysis, db, generation, notifications, storage, watermark
from core import settings as settings_module
from core.config import Config
from core.events import log_event
from core.nsfw import try_create_classifier

LOCK_KEY = "_analyze_worker"
# 1件の処理に時間がかかる(CPU推論で数分)ため、ハートビートはタスク単位で更新し、
# それより十分長い時間更新が無ければ異常終了とみなしてロックを奪取できるようにする
STALE_AFTER = timedelta(minutes=30)
# UIの「稼働中」表示用
ALIVE_WITHIN = timedelta(minutes=15)

PROGRESS_NOTIFY_SECONDS = 30 * 60  # 長いAI処理の途中経過を通知する間隔(秒)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pid_alive(pid: int) -> bool:
    """プロセスが生きているか。ワーカーが強制終了されたとき、ロックの有効期限(30分)を待たずに
    引き継げるようにするために使う。Windowsのos.kill(pid, 0)はプロセスを終了させてしまうため使わない。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_live(lock: dict | None) -> bool:
    return bool(lock and _now() - lock["heartbeat_dt"] < STALE_AFTER and _pid_alive(lock.get("pid")))


def _read_lock(conn: sqlite3.Connection) -> dict | None:
    raw = db.get_setting(conn, LOCK_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        data["heartbeat_dt"] = datetime.fromisoformat(data["heartbeat"])
        return data
    except (ValueError, KeyError):
        return None


def next_scheduled(conn: sqlite3.Connection, timezone_name: str, now: datetime | None = None) -> dict | None:
    """次に実行される定期実行(今日これから、または明日)。画面の状況表示用。"""
    local_now = (now or _now()).astimezone(ZoneInfo(timezone_name))
    today = local_now.strftime("%Y-%m-%d")
    hhmm = local_now.strftime("%H:%M")
    upcoming = []
    for sch in settings_module.get_ai_schedules(conn):
        if not sch["enabled"]:
            continue
        done_today = db.get_last_run_date(conn, f"ai_sched_{sch['id']}") == today
        day = "今日" if (sch["time"] > hhmm and not done_today) else "明日"
        # 時刻を過ぎていても今日未実行なら、次回のワーカー巡回で実行される(=まもなく)
        if sch["time"] <= hhmm and not done_today:
            day = "まもなく"
        upcoming.append((0 if day == "まもなく" else (1 if day == "今日" else 2), sch["time"], day, sch))
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: (x[0], x[1]))
    _, time, day, _sch = upcoming[0]
    same = [x for x in upcoming if x[2] == day and x[1] == time]
    return {
        "day": day,
        "time": time,
        "labels": [settings_module.AI_KIND_LABELS[x[3]["kind"]] for x in same],
    }


def get_status(conn: sqlite3.Connection, timezone_name: str | None = None) -> dict:
    """UI向けのAI処理の状況。"""
    counts = db.ai_task_counts(conn)
    lock = _read_lock(conn)
    alive = bool(lock and _now() - lock["heartbeat_dt"] < ALIVE_WITHIN and _pid_alive(lock.get("pid")))
    running = db.running_ai_task(conn)
    upcoming = [
        {"asset_id": t["asset_id"], "kind": t["kind"]}
        for t in db.list_ai_tasks(conn, "queued", limit=5)
    ]
    return {
        "queued": counts["queued"],
        "running": counts["running"],
        "failed": counts["failed"],
        "worker_alive": alive,
        "current": {"asset_id": running["asset_id"], "kind": running["kind"]} if running and alive else None,
        "upcoming": upcoming,
        "next_schedule": next_scheduled(conn, timezone_name) if timezone_name else None,
    }


def enqueue_for_assets(conn: sqlite3.Connection, asset_ids: list[str], kinds) -> int:
    """指定アセットに指定種別のタスクを積む。実際に積んだ件数を返す。"""
    queued = 0
    for asset_id in asset_ids:
        for kind in kinds:
            if db.enqueue_ai_task(conn, asset_id, kind):
                queued += 1
    return queued


def enqueue_scheduled(conn: sqlite3.Connection, timezone_name: str, now: datetime | None = None) -> int:
    """定期実行の設定に従い、時刻が来ていれば未処理のアセットをキューへ積む(1日1回まで)。"""
    local_now = (now or _now()).astimezone(ZoneInfo(timezone_name))
    today = local_now.strftime("%Y-%m-%d")
    queued = 0
    for schedule in settings_module.get_ai_schedules(conn):
        kind = schedule["kind"]
        if not schedule["enabled"]:
            continue
        job_name = f"ai_sched_{schedule['id']}"
        if db.get_last_run_date(conn, job_name) == today:
            continue
        if local_now.strftime("%H:%M") < schedule["time"]:
            continue
        since = None
        if schedule["scope"] == "days":
            since = (local_now - timedelta(days=int(schedule["days"]))).astimezone(timezone.utc).isoformat()
        queued += enqueue_for_assets(conn, db.unprocessed_asset_ids(conn, kind, since), [kind])
        db.set_last_run_date(conn, job_name, today)
    return queued


class AnalysisWorker:
    def __init__(self, config: Config, persistent: bool = False):
        """`persistent=True`(watch)は実行間もロックを保持し続ける。終了時に`close`を呼ぶこと。"""
        self._config = config
        self._persistent = persistent
        self._pid = os.getpid()
        self._holding = False
        self._classifier = None
        self._classifier_loaded = False
        self._generator = None
        self._generator_key: tuple[str, str] | None = None

    # --- 排他 ---------------------------------------------------------------

    def _try_acquire(self, conn: sqlite3.Connection) -> bool:
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            lock = _read_lock(conn)
            if lock and lock.get("pid") != self._pid and _lock_is_live(lock):
                conn.rollback()
                return False
            first = not self._holding
            self._write_lock(conn, None, None)
            self._holding = True
            if first:
                # ロックを得た時点で「実行中」のまま残るタスクは前のワーカーの取り残し
                db.requeue_running_ai_tasks(conn)
            return True
        except Exception:
            conn.rollback()
            raise

    def _write_lock(self, conn: sqlite3.Connection, current: str | None, kind: str | None) -> None:
        value = json.dumps(
            {"pid": self._pid, "heartbeat": _now().isoformat(), "current": current, "kind": kind}
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (LOCK_KEY, value),
        )
        conn.commit()

    def close(self, conn: sqlite3.Connection) -> None:
        lock = _read_lock(conn)
        if lock and lock.get("pid") == self._pid:
            db.delete_setting(conn, LOCK_KEY)
        self._holding = False

    # --- モデル(種別ごとに遅延ロード・再利用) ---------------------------------

    def _get_classifier(self):
        if not self._classifier_loaded:
            self._classifier = try_create_classifier(self._config.nsfw)
            self._classifier_loaded = self._classifier is not None
        return self._classifier

    def _get_generator(self, conn: sqlite3.Connection):
        key = (
            settings_module.get_generation_provider(conn),
            settings_module.get_generation_model(conn),
        )
        if self._generator is None or key != self._generator_key:
            self._generator = generation.try_create_generator(conn)
            self._generator_key = key
        return self._generator

    # --- 実行 ---------------------------------------------------------------

    def _process(self, conn: sqlite3.Connection, task: dict) -> str | None:
        """1タスクを処理する。失敗時はエラー文字列、成功時はNone。"""
        asset = db.get_asset(conn, task["asset_id"])
        if asset is None:
            return "アセットが見つかりません"
        if asset.get("deleted_at"):
            return "ごみ箱に入っている作品のため処理しませんでした"
        media_path = Path(asset["file_path"])
        try:
            if task["kind"] == "watermark":
                params = json.loads(task.get("params") or "{}")
                if asset["kind"] != "image":
                    return "watermark: 動画は透かしの挿入に未対応です"
                positions = watermark.normalize_positions(params.get("positions") or params.get("position"))
                out = watermark.apply_to_file(
                    media_path, params["text"], positions, params["opacity"], params["size"]
                )
                db.update_asset(
                    conn, asset["id"], wm_path=str(out), wm_text=params["text"],
                    wm_position=",".join(positions), updated_at=_now().isoformat(),
                )
                return None
            if task["kind"] == "nsfw":
                result = analysis.run_nsfw(self._get_classifier(), media_path)
                db.update_asset(
                    conn,
                    asset["id"],
                    nsfw_auto_rating=result.rating,
                    nsfw_auto_confidence=result.confidence,
                    updated_at=_now().isoformat(),
                )
                db.add_tag_to_asset(conn, asset["id"], result.rating)
            else:
                result = analysis.run_describe(conn, self._get_generator(conn), media_path)
                db.update_asset(
                    conn,
                    asset["id"],
                    content_description=result.description,
                    updated_at=_now().isoformat(),
                )
                for tag in result.suggested_tags:
                    db.add_tag_to_asset(conn, asset["id"], tag)
                if result.tag_error:
                    # 説明文は保存済み。タグだけ失敗したことを失敗として通知する
                    return f"describe(タグ): {result.tag_error}"
        except Exception as exc:  # noqa: BLE001
            return f"{task['kind']}: {exc}"
        return None

    def _promote_if_complete(self, conn: sqlite3.Connection, asset_id: str, log) -> None:
        """判定と説明の両方が揃った分析中のアセットを承認待ち(承認済みならready)へ進める。"""
        asset = db.get_asset(conn, asset_id)
        if (
            asset
            and asset["status"] == "analyzing"
            and asset["nsfw_auto_rating"] is not None
            and asset["content_description"] is not None
        ):
            new_status = "ready" if asset["content_rating_confirmed"] else "pending_approval"
            db.update_asset(conn, asset_id, status=new_status, updated_at=_now().isoformat())
            log(f"[analyze] {asset_id}: {new_status}に更新しました")

    def run_once(self, conn: sqlite3.Connection, log=print) -> tuple[int, int] | None:
        """定期実行の判定とキューの処理を1巡行う。戻り値は(成功件数, 失敗件数)。他のワーカーが稼働中ならNone。"""
        if not self._try_acquire(conn):
            log("[analyze] 別のワーカーが稼働中のためスキップします")
            return None

        # ストレージ(画像・動画の置き場所)が見つからない間は、何も処理しない。待機中のタスクは失敗にせず、
        # そのまま残す(つながったら、次の巡回で再開する)
        storage.apply_override(self._config, conn)
        status = storage.check(self._config, conn)
        if not status.available:
            if getattr(self, "_storage_warned", None) != status.code:
                log(f"[storage] {status.reason}。接続されるまで、AI処理を一時停止します")
                notifications.add(conn, "storage", "ストレージを使えません", status.reason, "error")
                db.set_setting(conn, "storage_was_unavailable", "1")
            self._storage_warned = status.code
            self._write_lock(conn, None, None)
            if not self._persistent:
                self.close(conn)
            return 0, 0
        self._storage_warned = None
        if db.get_setting(conn, "storage_was_unavailable") == "1":
            db.set_setting(conn, "storage_was_unavailable", "0")
            notifications.add(conn, "storage", "ストレージにつながりました", "AI処理を再開します", "success")

        done = failed = 0
        summary: dict[str, dict] = {}  # 種別ごとの(成功・失敗の数、最初の失敗理由)。巡回の最後に、まとめて通知する
        # 待ちが多いと1巡が何時間も続くため、途中でも一定時間ごとに、それまでの分を通知する(途中で止まっても結果が残る)
        last_flush = time.monotonic()
        try:
            queued = enqueue_scheduled(conn, self._config.timezone)
            if queued:
                log(f"[schedule] 定期実行でAI処理を{queued}件キューに追加しました")
            while True:
                task = db.claim_next_ai_task(conn)
                if task is None:
                    break
                self._write_lock(conn, task["asset_id"], task["kind"])
                error = self._process(conn, task)
                db.finish_ai_task(conn, task["id"], error)
                entry = summary.setdefault(task["kind"], {"done": 0, "failed": 0, "error": None, "asset": None})
                if error:
                    entry["failed"] += 1
                    entry["error"] = entry["error"] or f"{task['asset_id']}: {error}"
                else:
                    entry["done"] += 1
                entry["asset"] = task["asset_id"]
                if error:
                    failed += 1
                    db.update_asset(conn, task["asset_id"], analysis_error=error)
                    log_event(
                        self._config.paths.events_path,
                        "ai_task_failed",
                        asset_id=task["asset_id"],
                        kind=task["kind"],
                        error=error,
                    )
                    log(f"[analyze] {task['asset_id']}: 失敗 ({error})")
                else:
                    done += 1
                    db.update_asset(conn, task["asset_id"], analysis_error=None)
                    self._promote_if_complete(conn, task["asset_id"], log)
                if summary and time.monotonic() - last_flush >= PROGRESS_NOTIFY_SECONDS:
                    remaining = conn.execute("SELECT COUNT(*) FROM ai_tasks WHERE status = 'queued'").fetchone()[0]
                    self._notify_summary(conn, summary, remaining)
                    summary = {}
                    last_flush = time.monotonic()
            self._write_lock(conn, None, None)
            self._notify_summary(conn, summary)
        finally:
            if not self._persistent:
                self.close(conn)
        return done, failed

    def _notify_summary(self, conn: sqlite3.Connection, summary: dict, remaining: int | None = None) -> None:
        """今回の巡回で処理した分を、種別ごとに1件の通知にまとめる(画面を見ていなくても、結果が残る)。

        `remaining`を渡したときは途中経過として、まだ待っている件数を添える。
        """
        for kind, entry in summary.items():
            label = settings_module.AI_KIND_LABELS.get(kind) or ("透かし挿入" if kind == "watermark" else kind)
            title, body, level = notifications.summarize_tasks(label, entry["done"], entry["failed"], entry["error"])
            if remaining:
                title = f"{title}（途中経過）"
                body = f"{body}　まだ{remaining}件が待っています（続けて処理中）".strip()
            single = entry["asset"] if entry["done"] + entry["failed"] == 1 else None
            notifications.add(
                conn, "watermark" if kind == "watermark" else "ai", title, body, level, asset_id=single
            )
