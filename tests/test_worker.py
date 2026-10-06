import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from core import db, worker
from core import settings as settings_module
from core.cli import cmd_init
from core.config import load_config
from core.generation import DescriptionResult
from core.ingest import ingest_inbox
from core.nsfw import NsfwResult

CONFIG_YAML = """
timezone: Asia/Tokyo
paths:
  library_root: data/library
  state_dir: data/state
  screenshots_dir: data/screenshots
nsfw:
  model: marqo/nsfw-image-detection-384
  threshold: 0.5
  video_frame_interval_seconds: 2
platform_content_rules:
  fanvue: [sfw]
platform_auto_post_ratings:
  fanvue: [sfw]
web:
  host: 127.0.0.1
  port: 8420
"""


def _setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    return config, conn


def _insert(conn, tmp_path, asset_id="a1", created_at="2026-01-01T00:00:00+00:00", **extra):
    path = tmp_path / f"{asset_id}.jpg"
    path.write_bytes(b"x")
    db.insert_asset(
        conn,
        {
            "id": asset_id,
            "status": "analyzing",
            "kind": "image",
            "file_path": str(path),
            "created_at": created_at,
            "updated_at": created_at,
            **extra,
        },
    )


def _run(config, conn, classifier=None, generator=None, w=None):
    with patch("core.worker.try_create_classifier", return_value=classifier), patch(
        "core.generation.try_create_generator", return_value=generator
    ):
        return (w or worker.AnalysisWorker(config)).run_once(conn, log=lambda m: None)


def test_ingest_defer_analysis_registers_as_analyzing_without_models(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "a.jpg").write_bytes(b"x")
    classifier = MagicMock()

    results = ingest_inbox(config, conn, nsfw_classifier=classifier, defer_analysis=True)

    assert len(results) == 1
    classifier.classify.assert_not_called()
    assert db.get_asset(conn, results[0].asset_id)["status"] == "analyzing"


def test_enqueue_does_not_duplicate_active_tasks(tmp_path):
    _config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)

    assert db.enqueue_ai_task(conn, "a1", "nsfw") is True
    assert db.enqueue_ai_task(conn, "a1", "nsfw") is False
    assert db.enqueue_ai_task(conn, "a1", "describe") is True
    assert db.ai_task_counts(conn)["queued"] == 2


def test_nsfw_and_describe_run_independently(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    classifier = MagicMock()
    classifier.classify.return_value = NsfwResult(rating="nsfw", confidence=0.9)
    generator = MagicMock()

    db.enqueue_ai_task(conn, "a1", "nsfw")
    assert _run(config, conn, classifier=classifier, generator=generator) == (1, 0)

    asset = db.get_asset(conn, "a1")
    assert asset["nsfw_auto_rating"] == "nsfw"
    assert asset["content_description"] is None
    assert asset["status"] == "analyzing"  # 説明がまだなので承認待ちにはならない
    generator.describe_image.assert_not_called()
    assert "nsfw" in db.list_tags_for_asset(conn, "a1")


def test_describe_after_nsfw_promotes_to_pending_approval(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path, nsfw_auto_rating="sfw", nsfw_auto_confidence=0.2)
    generator = MagicMock()
    generator.describe_image.return_value = DescriptionResult(description="説明", suggested_tags=["屋外"])

    db.enqueue_ai_task(conn, "a1", "describe")
    assert _run(config, conn, generator=generator) == (1, 0)

    asset = db.get_asset(conn, "a1")
    assert asset["content_description"] == "説明"
    assert asset["status"] == "pending_approval"
    assert "屋外" in db.list_tags_for_asset(conn, "a1")


def test_failed_task_records_error_and_keeps_other_results(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)

    db.enqueue_ai_task(conn, "a1", "nsfw")
    assert _run(config, conn, classifier=None) == (0, 1)  # classifierが無い

    assert "NSFW判定モデル" in db.get_asset(conn, "a1")["analysis_error"]
    state = db.ai_task_states(conn, ["a1"])["a1"]["nsfw"]
    assert state["status"] == "failed" and state["error"]


def test_describe_generates_tags_in_separate_call_with_categories(tmp_path):
    """タグは説明文とは別の呼び出しで、設定のカテゴリを渡して生成する(小型モデル対策)。"""
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    cats = [{"name": "服装", "options": "水着, 制服"}, {"name": "性別", "options": ""}]
    settings_module.set_tag_categories(conn, cats)
    generator = MagicMock()
    generator.describe_image.return_value = DescriptionResult(description="d")
    generator.suggest_tags.return_value = ["水着", "女性"]

    db.enqueue_ai_task(conn, "a1", "describe")
    assert _run(config, conn, generator=generator) == (1, 0)

    assert generator.suggest_tags.call_args[0][1] == cats
    assert {"水着", "女性"} <= set(db.list_tags_for_asset(conn, "a1"))


def test_describe_saves_description_but_reports_failure_when_tags_missing(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    generator = MagicMock()
    generator.describe_image.return_value = DescriptionResult(description="説明")
    generator.suggest_tags.return_value = []  # モデルがタグを出さなかった

    db.enqueue_ai_task(conn, "a1", "describe")
    assert _run(config, conn, generator=generator) == (0, 1)

    assert db.get_asset(conn, "a1")["content_description"] == "説明"  # 説明文は残る
    assert "タグ" in db.ai_task_states(conn, ["a1"])["a1"]["describe"]["error"]


def test_describe_tag_exception_is_reported_not_silent(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    generator = MagicMock()
    generator.describe_image.return_value = DescriptionResult(description="説明")
    generator.suggest_tags.side_effect = RuntimeError("boom")

    db.enqueue_ai_task(conn, "a1", "describe")
    assert _run(config, conn, generator=generator) == (0, 1)
    assert "boom" in db.get_asset(conn, "a1")["analysis_error"]


def test_status_shows_worker_and_queue(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    db.enqueue_ai_task(conn, "a1", "nsfw")
    assert worker.get_status(conn)["queued"] == 1
    assert worker.get_status(conn)["worker_alive"] is False


def test_run_once_skips_when_another_worker_holds_lock(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    db.enqueue_ai_task(conn, "a1", "nsfw")
    other = {"pid": os.getppid(), "heartbeat": datetime.now(timezone.utc).isoformat(), "current": "a1", "kind": "nsfw"}
    db.set_setting(conn, worker.LOCK_KEY, json.dumps(other))

    assert _run(config, conn, classifier=MagicMock()) is None
    assert worker.get_status(conn)["worker_alive"] is True
    assert db.ai_task_counts(conn)["queued"] == 1  # 触られていない


def test_run_once_takes_over_stale_lock_and_requeues_orphans(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    db.enqueue_ai_task(conn, "a1", "nsfw")
    db.claim_next_ai_task(conn)  # 前のワーカーが実行中のまま死んだ状態
    old = datetime.now(timezone.utc) - worker.STALE_AFTER - timedelta(minutes=1)
    db.set_setting(conn, worker.LOCK_KEY, json.dumps({"pid": os.getppid(), "heartbeat": old.isoformat(), "current": None}))
    classifier = MagicMock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.1)

    assert _run(config, conn, classifier=classifier) == (1, 0)
    assert db.get_asset(conn, "a1")["nsfw_auto_rating"] == "sfw"


def test_models_are_loaded_once_and_only_when_needed(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    w = worker.AnalysisWorker(config)
    with patch("core.worker.try_create_classifier", return_value=MagicMock()) as mc, patch(
        "core.generation.try_create_generator", return_value=MagicMock()
    ) as mg:
        assert mc.call_count == 0 and mg.call_count == 0  # 遅延ロード
        w._get_classifier()
        w._get_classifier()
        w._get_generator(conn)
        w._get_generator(conn)
    assert mc.call_count == 1 and mg.call_count == 1


def _jst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=9)))


def test_scheduled_enqueue_waits_for_time_and_runs_once_per_day(tmp_path):
    _config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    settings_module.set_ai_schedule(conn, "nsfw", True, "03:00", "all", 7)

    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", _jst(2026, 10, 1, 2, 59)) == 0
    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", _jst(2026, 10, 1, 3, 0)) == 1
    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", _jst(2026, 10, 1, 9, 0)) == 0  # 同日は1回だけ
    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", _jst(2026, 10, 2, 3, 0)) == 0  # 積み済み(待機中)


def test_scheduled_enqueue_only_unprocessed_and_respects_days(tmp_path):
    _config, conn = _setup(tmp_path)
    now = _jst(2026, 10, 10, 4, 0)
    _insert(conn, tmp_path, "old", created_at=(now - timedelta(days=30)).astimezone(timezone.utc).isoformat())
    _insert(conn, tmp_path, "new", created_at=(now - timedelta(days=2)).astimezone(timezone.utc).isoformat())
    _insert(
        conn, tmp_path, "done", created_at=(now - timedelta(days=1)).astimezone(timezone.utc).isoformat(),
        nsfw_auto_rating="sfw", nsfw_auto_confidence=0.1,
    )
    settings_module.set_ai_schedule(conn, "nsfw", True, "03:00", "days", 7)

    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", now) == 1  # 直近7日かつ未処理は"new"のみ
    queued = [r["asset_id"] for r in conn.execute("SELECT asset_id FROM ai_tasks")]
    assert queued == ["new"]


def test_disabled_schedule_does_nothing(tmp_path):
    _config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    assert worker.enqueue_scheduled(conn, "Asia/Tokyo", _jst(2026, 10, 1, 12, 0)) == 0


def test_schedule_and_categories_settings_roundtrip(tmp_path):
    _config, conn = _setup(tmp_path)
    settings_module.set_ai_schedule(conn, "describe", True, "25:99", "bogus", "x")  # 不正値は既定へ
    sch = settings_module.get_ai_schedule(conn, "describe")
    assert sch == {"enabled": True, "time": "03:00", "scope": "all", "days": 7}

    settings_module.set_tag_categories(conn, [{"name": " 服装 ", "options": "a, b"}, {"name": "", "options": "x"}])
    assert settings_module.get_tag_categories(conn) == [{"name": "服装", "options": "a, b"}]
    assert settings_module.tag_category_instructions(conn).count("- ") == 1


def test_lock_of_dead_process_is_taken_over_immediately(tmp_path):
    """強制終了されたワーカーのロックは、有効期限を待たずに引き継ぐ。"""
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    db.enqueue_ai_task(conn, "a1", "nsfw")
    fresh = datetime.now(timezone.utc).isoformat()
    db.set_setting(conn, worker.LOCK_KEY, json.dumps({"pid": 2**30, "heartbeat": fresh, "current": "a1"}))
    classifier = MagicMock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.1)

    assert worker.get_status(conn)["worker_alive"] is False  # 死んだPIDは「稼働中」と表示しない
    assert _run(config, conn, classifier=classifier) == (1, 0)
