import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from unittest.mock import MagicMock, patch

from core import db
from core.cli import (
    _is_job_due,
    _parse_cadence_entry,
    _today_str,
    cmd_analyze,
    cmd_doctor,
    cmd_init,
    cmd_run_drop,
    cmd_run_due,
    cmd_watch,
)
from core.config import load_config
from core.generation import DescriptionResult
from core.nsfw import NsfwResult
from posting.fanvue_oauth import FanvueTokenStore, TokenSet


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
  fanvue: [sfw, suggestive, explicit]
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]
web:
  host: 127.0.0.1
  port: 8420
"""

CONFIG_YAML_WITH_CADENCE = CONFIG_YAML + """
cadence:
  drop: "21:00"
"""


def _load(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    return load_config(base_dir=tmp_path)


def _load_with_cadence(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML_WITH_CADENCE, encoding="utf-8")
    return load_config(base_dir=tmp_path)


def _connect_fanvue(config, monkeypatch):
    """Fanvue OAuth連携済み(ADR-0021)の状態を疑似的に用意する。"""
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_SECRET", "client-secret")
    store = FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json")
    store.save(TokenSet(access_token="access-token", refresh_token="refresh-token", expires_at=time.time() + 3600))


def test_doctor_reports_missing_before_init(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_SECRET", raising=False)
    config = _load(tmp_path)

    exit_code = cmd_doctor(config)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "MISSING" in out


def test_init_then_doctor_is_ok(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_SECRET", raising=False)
    config = _load(tmp_path)

    init_exit_code = cmd_init(config)
    assert init_exit_code == 0

    doctor_exit_code = cmd_doctor(config)
    assert doctor_exit_code == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out
    assert "database" in out
    assert "未接続のためスキップ" in out

    assert config.paths.events_path.exists()


def test_doctor_checks_fanvue_when_connected(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)
    _connect_fanvue(config, monkeypatch)
    capsys.readouterr()

    with patch("posting.fanvue.FanvueClient.get_me", return_value={"id": "u1"}):
        exit_code = cmd_doctor(config)

    assert exit_code == 0
    assert "Fanvue API: OK" in capsys.readouterr().out


def test_doctor_reports_fanvue_failure(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)
    _connect_fanvue(config, monkeypatch)
    capsys.readouterr()

    with patch("posting.fanvue.FanvueClient.get_me", side_effect=RuntimeError("401 unauthorized")):
        exit_code = cmd_doctor(config)

    assert exit_code == 1
    assert "Fanvue API: NG" in capsys.readouterr().out


def test_run_drop_requires_fanvue_connection(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("FANVUE_OAUTH_CLIENT_SECRET", raising=False)
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    exit_code = cmd_run_drop(config)

    assert exit_code == 1
    assert "FANVUE_OAUTH_CLIENT_ID" in capsys.readouterr().out


def test_run_drop_skips_if_already_run_today(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    conn = db.get_connection(config.paths.db_path)
    db.set_last_run_date(conn, "drop", _today_str(config.timezone))
    conn.close()

    exit_code = cmd_run_drop(config)

    assert exit_code == 0
    assert "既に実行済み" in capsys.readouterr().out


def test_run_drop_executes_job_and_records_last_run(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)
    _connect_fanvue(config, monkeypatch)
    monkeypatch.setenv("FANVUE_HANDLE", "creator")
    capsys.readouterr()

    from posting.jobs import DropResult

    fake_result = DropResult(executed=True, asset_id="a1", fanvue_url="https://f.com/creator")
    with patch("posting.jobs.run_fanvue_drop", return_value=fake_result) as mocked:
        exit_code = cmd_run_drop(config)

    assert exit_code == 0
    assert "投稿成功" in capsys.readouterr().out
    mocked.assert_called_once()

    conn = db.get_connection(config.paths.db_path)
    assert db.get_last_run_date(conn, "drop") == _today_str(config.timezone)
    conn.close()


def test_run_drop_failure_returns_nonzero(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)
    _connect_fanvue(config, monkeypatch)
    capsys.readouterr()

    from posting.jobs import DropResult

    fake_result = DropResult(executed=False, asset_id="a1", error="upload failed")
    with patch("posting.jobs.run_fanvue_drop", return_value=fake_result):
        exit_code = cmd_run_drop(config)

    assert exit_code == 1
    assert "投稿失敗" in capsys.readouterr().out


def test_is_job_due_true_when_now_past_cadence_time():
    now = datetime(2026, 1, 1, 21, 30)
    assert _is_job_due(now, "21:00") is True
    assert _is_job_due(now, "21:30") is True


def test_is_job_due_false_when_now_before_cadence_time():
    now = datetime(2026, 1, 1, 20, 59)
    assert _is_job_due(now, "21:00") is False


def test_run_due_without_cadence_config_does_nothing(tmp_path, capsys, monkeypatch):
    config = _load(tmp_path)  # cadence未設定
    cmd_init(config)
    capsys.readouterr()

    with patch("core.cli.cmd_run_drop") as mocked:
        exit_code = cmd_run_due(config)

    assert exit_code == 0
    assert "cadence設定がありません" in capsys.readouterr().out
    mocked.assert_not_called()


def test_run_due_skips_when_not_yet_due(tmp_path, capsys, monkeypatch):
    config = _load_with_cadence(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    with patch("core.cli._is_job_due", return_value=False), patch("core.cli.cmd_run_drop") as mocked:
        exit_code = cmd_run_due(config)

    assert exit_code == 0
    assert "まだ実行時刻前です" in capsys.readouterr().out
    mocked.assert_not_called()


def test_run_due_executes_job_when_due(tmp_path, capsys, monkeypatch):
    config = _load_with_cadence(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    with patch("core.cli._is_job_due", return_value=True), patch("core.cli.cmd_run_drop") as mocked:
        exit_code = cmd_run_due(config)

    assert exit_code == 0
    mocked.assert_called_once_with(config, count=1, kind=None, rating=None)


def test_run_due_skips_unsupported_job_name(tmp_path, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        CONFIG_YAML + "\ncadence:\n  x_teaser: \"21:00\"\n", encoding="utf-8"
    )
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    capsys.readouterr()

    with patch("core.cli.cmd_run_drop") as mocked:
        exit_code = cmd_run_due(config)

    assert exit_code == 0
    assert "未対応のジョブ名" in capsys.readouterr().out
    mocked.assert_not_called()


@pytest.fixture(autouse=True)
def _no_edit_thread(monkeypatch):
    """`watch`の動画編集用スレッドは、ここでは動かさない(巡回のテストに、余計なsleepが混ざらないように)。"""
    monkeypatch.setattr("core.cli._start_edit_thread", lambda config: None)


def test_watch_loops_run_due_until_interrupted(tmp_path, monkeypatch):
    config = _load(tmp_path)
    cmd_init(config)

    call_count = 0

    def fake_run_due(_config):
        nonlocal call_count
        call_count += 1
        return 0

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    with patch("core.cli.cmd_run_due", side_effect=fake_run_due), patch(
        "core.cli.time.sleep", side_effect=fake_sleep
    ) as mocked_sleep:
        exit_code = cmd_watch(config, interval_seconds=5)

    assert exit_code == 0
    assert call_count == 1
    mocked_sleep.assert_called_once_with(5)


def test_watch_calls_analyze_each_tick(tmp_path):
    config = _load(tmp_path)
    cmd_init(config)

    with patch("core.cli.cmd_analyze") as mocked_analyze, patch("core.cli.cmd_run_due"), patch(
        "core.cli.time.sleep", side_effect=KeyboardInterrupt
    ):
        cmd_watch(config)

    mocked_analyze.assert_called_once()
    assert mocked_analyze.call_args[0][0] == config


def test_watch_skips_ingest_when_auto_ingest_disabled(tmp_path):
    """auto_ingest設定の既定値はFalseのため、ingestは呼ばれない(ADR-0020)。"""
    config = _load(tmp_path)
    cmd_init(config)

    with patch("core.cli.cmd_ingest") as mocked_ingest, patch("core.cli.cmd_analyze"), patch(
        "core.cli.cmd_run_due"
    ), patch("core.cli.time.sleep", side_effect=KeyboardInterrupt):
        cmd_watch(config)

    mocked_ingest.assert_not_called()


def test_watch_calls_ingest_each_tick_when_auto_ingest_enabled(tmp_path):
    """設定でauto_ingestをオンにすると、watchの各ループでingestも実行される(ADR-0020)。"""
    from core import settings

    config = _load(tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    settings.set_auto_ingest(conn, True)
    conn.close()

    with patch("core.cli.cmd_ingest") as mocked_ingest, patch("core.cli.cmd_analyze"), patch(
        "core.cli.cmd_run_due"
    ), patch("core.cli.time.sleep", side_effect=KeyboardInterrupt):
        cmd_watch(config)

    mocked_ingest.assert_called_once_with(config, defer_analysis=True)


def test_parse_cadence_entry_accepts_plain_string():
    time_str, options = _parse_cadence_entry("21:00")
    assert time_str == "21:00"
    assert options == {}


def test_parse_cadence_entry_accepts_dict_with_options():
    time_str, options = _parse_cadence_entry({"time": "21:00", "count": 3, "kind": "image", "rating": "sfw"})
    assert time_str == "21:00"
    assert options == {"count": 3, "kind": "image", "rating": "sfw"}


def test_run_due_passes_cadence_dict_options_to_run_drop(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        CONFIG_YAML + '\ncadence:\n  drop:\n    time: "00:00"\n    count: 3\n    kind: image\n    rating: sfw\n',
        encoding="utf-8",
    )
    config = load_config(base_dir=tmp_path)
    cmd_init(config)

    with patch("core.cli.cmd_run_drop") as mocked:
        cmd_run_due(config)

    mocked.assert_called_once_with(config, count=3, kind="image", rating="sfw")


def test_analyze_promotes_asset_to_pending_approval_on_success(tmp_path, capsys):
    config = _load(tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    media_path = config.paths.ready / "a1" / "look.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"fake-bytes")
    db.insert_asset(
        conn,
        {
            "id": "a1",
            "status": "analyzing",
            "kind": "image",
            "file_path": str(media_path),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    conn.close()

    fake_classifier = MagicMock()
    fake_classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.1)
    fake_generator = MagicMock()
    fake_generator.describe_image.return_value = DescriptionResult(
        description="説明文", suggested_tags=["屋外"]
    )

    with patch("core.worker.try_create_classifier", return_value=fake_classifier), patch(
        "core.generation.try_create_generator", return_value=fake_generator
    ):
        exit_code = cmd_analyze(config)

    assert exit_code == 0
    conn = db.get_connection(config.paths.db_path)
    asset = db.get_asset(conn, "a1")
    tags = db.list_tags_for_asset(conn, "a1")
    conn.close()
    assert asset["status"] == "pending_approval"  # ADR-0019: 人間の承認待ち(旧readyから改名)
    assert asset["content_description"] == "説明文"
    assert set(tags) == {"屋外", "sfw"}
    assert "pending_approvalに更新" in capsys.readouterr().out


def test_analyze_promotes_asset_directly_to_ready_when_already_confirmed(tmp_path, capsys):
    """分析待ちの間に人間が先に承認していた場合、pending_approvalを経由せずreadyになる(ADR-0019)。"""
    config = _load(tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    media_path = config.paths.ready / "a1" / "look.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"fake-bytes")
    db.insert_asset(
        conn,
        {
            "id": "a1",
            "status": "analyzing",
            "kind": "image",
            "file_path": str(media_path),
            "content_rating": "sfw",
            "content_rating_confirmed": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    conn.close()

    fake_classifier = MagicMock()
    fake_classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.1)
    fake_generator = MagicMock()
    fake_generator.describe_image.return_value = DescriptionResult(description="説明文")

    with patch("core.worker.try_create_classifier", return_value=fake_classifier), patch(
        "core.generation.try_create_generator", return_value=fake_generator
    ):
        exit_code = cmd_analyze(config)

    assert exit_code == 0
    conn = db.get_connection(config.paths.db_path)
    asset = db.get_asset(conn, "a1")
    conn.close()
    assert asset["status"] == "ready"
    assert "readyに更新" in capsys.readouterr().out


def test_analyze_reports_no_pending_assets(tmp_path, capsys):
    config = _load(tmp_path)
    cmd_init(config)

    exit_code = cmd_analyze(config)

    assert exit_code == 0
    assert "処理待ちのAI処理はありません" in capsys.readouterr().out


def test_run_due_follows_the_saved_post_schedule_over_config_yaml(tmp_path, capsys):
    """画面で保存された投稿スケジュールが優先される。オフなら、config.yamlのcadenceがあっても投稿しない。"""
    from core import db, settings

    config = _load_with_cadence(tmp_path)  # config.yamlには、cadenceがある
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    every_day = [{"id": "s1", "days": list(range(7)), "time": "09:30", "jitter": 0, "count": 3}]
    settings.set_post_schedule(conn, enabled=False, entries=every_day)
    capsys.readouterr()

    with patch("core.cli.cmd_run_drop") as mocked:
        assert cmd_run_due(config) == 0
    assert "自動投稿はオフです" in capsys.readouterr().out
    mocked.assert_not_called()

    settings.set_post_schedule(conn, enabled=True, entries=every_day)
    with patch("core.cli.datetime") as fake_dt, patch("core.cli.cmd_run_drop") as mocked:
        fake_dt.now.return_value = datetime(2026, 10, 12, 9, 29, tzinfo=ZoneInfo("Asia/Tokyo"))  # 予定の1分前
        cmd_run_due(config)
        mocked.assert_not_called()
        fake_dt.now.return_value = datetime(2026, 10, 12, 9, 31, tzinfo=ZoneInfo("Asia/Tokyo"))  # 予定を過ぎた
        cmd_run_due(config)
    mocked.assert_called_once_with(config, count=3, kind=None, rating=None, run_key="drop:s1")  # 予定ごとに、実行済みの記録を持つ


def test_run_due_runs_each_entry_on_its_own_weekdays_and_once_per_day(tmp_path, capsys):
    from core import db, settings

    config = _load(tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    settings.set_post_schedule(conn, True, [
        {"id": "mwf", "days": [0, 2, 4], "time": "21:00", "jitter": 0, "count": 1},   # 月・水・金
        {"id": "tsu", "days": [1, 6], "time": "08:00", "jitter": 0, "count": 2},      # 火・日
    ])
    jst = ZoneInfo("Asia/Tokyo")

    def run(when):
        with patch("core.cli.datetime") as fake_dt, patch("core.cli.cmd_run_drop") as mocked:
            fake_dt.now.return_value = when
            cmd_run_due(config)
        return [c.kwargs["run_key"] for c in mocked.call_args_list]

    assert run(datetime(2026, 10, 12, 22, 0, tzinfo=jst)) == ["drop:mwf"]  # 月曜: mwfだけ
    assert run(datetime(2026, 10, 13, 9, 0, tzinfo=jst)) == ["drop:tsu"]   # 火曜: tsuだけ
    assert run(datetime(2026, 10, 14, 7, 0, tzinfo=jst)) == []             # 水曜の朝: mwfの時刻前、tsuは曜日が違う
    db.set_last_run_date(conn, "drop:mwf", "2026-10-12")
    assert run(datetime(2026, 10, 12, 23, 0, tzinfo=jst)) == []            # 今日は、実行済み


def test_planned_time_jitter_is_random_but_stable_within_a_day(tmp_path):
    from datetime import date

    from core import settings

    jst = ZoneInfo("Asia/Tokyo")
    entry = {"id": "s1", "days": [0], "time": "21:00", "jitter": 30, "count": 1}
    day = date(2026, 10, 12)
    first = settings.planned_time(entry, day, jst)
    assert all(settings.planned_time(entry, day, jst) == first for _ in range(5))  # 同じ日は、何度調べても同じ(再起動しても変わらない)
    assert 20 * 60 + 30 <= first.hour * 60 + first.minute <= 21 * 60 + 30         # 上下30分の範囲

    times = {settings.planned_time(entry, date(2026, 10, 1) + __import__("datetime").timedelta(days=i), jst).strftime("%H:%M") for i in range(20)}
    assert len(times) > 5  # 日ごとに、ばらける

    exact = dict(entry, jitter=0)
    assert settings.planned_time(exact, day, jst).strftime("%H:%M") == "21:00"
    # 日をまたがない: 0:20に上下90分でも、前日・翌日にはならず、その日の中に収まる
    early = {"id": "e", "days": [0], "time": "00:20", "jitter": 90, "count": 1}
    for i in range(30):
        planned = settings.planned_time(early, date(2026, 10, 1) + __import__("datetime").timedelta(days=i), jst)
        assert planned.date() == date(2026, 10, 1) + __import__("datetime").timedelta(days=i)


def test_next_planned_picks_the_nearest_matching_weekday(tmp_path):
    from datetime import date

    from core import settings

    jst = ZoneInfo("Asia/Tokyo")
    entry = {"id": "s1", "days": [2, 5], "time": "21:00", "jitter": 0, "count": 1}  # 水・土
    now = datetime(2026, 10, 12, 10, 0, tzinfo=jst)  # 月曜
    assert settings.next_planned(entry, now, jst).date() == date(2026, 10, 14)  # 水
    now = datetime(2026, 10, 14, 22, 0, tzinfo=jst)  # 水曜の夜(時刻は過ぎた)
    assert settings.next_planned(entry, now, jst).date() == date(2026, 10, 17)  # 土
    now = datetime(2026, 10, 14, 10, 0, tzinfo=jst)
    assert settings.next_planned(entry, now, jst, skip_date="2026-10-14").date() == date(2026, 10, 17)  # 今日は実行済み


def test_post_schedule_settings_validation_and_page(tmp_path):
    from core import db, settings
    from core.web.app import create_app

    config = _load(tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    assert settings.get_post_schedule(conn) is None  # 一度も保存していなければ、config.yamlに従う
    settings.set_post_schedule(conn, True, [
        {"id": "ok", "days": ["0", "2", "9"], "time": "7:05", "jitter": "999", "count": "99"},  # 範囲外の値は、範囲内に
        {"id": "no-days", "days": [], "time": "21:00"},       # 曜日が無い行は、捨てる
        {"id": "bad-time", "days": [1], "time": "25:99"},     # 時刻が不正な行は、捨てる
    ])
    assert settings.get_post_schedule(conn) == {
        "enabled": True,
        "entries": [{"id": "ok", "days": [0, 2], "time": "07:05", "jitter": 360, "count": 10}],
    }
    # 旧形式(毎日1つの時刻)の保存値は、全曜日の1件として読む
    db.set_setting(conn, "post_schedule", '{"enabled": true, "time": "20:00", "count": 2}')
    legacy = settings.get_post_schedule(conn)
    assert legacy["entries"][0]["days"] == list(range(7)) and legacy["entries"][0]["time"] == "20:00" and legacy["entries"][0]["count"] == 2

    client = create_app(config).test_client()
    client.post("/settings", data={
        "post_schedule_enabled": "on",
        "ps_id": ["a", "b"], "ps_days": ["0,2,4,5", "1,2,5,6"], "ps_time": ["21:00", "08:00"], "ps_jitter": ["30", "90"], "ps_count": ["1", "2"],
    })
    saved = settings.get_post_schedule(conn)
    assert saved["enabled"] and [e["days"] for e in saved["entries"]] == [[0, 2, 4, 5], [1, 2, 5, 6]]
    assert [(e["time"], e["jitter"]) for e in saved["entries"]] == [("21:00", 30), ("08:00", 90)]  # 複数の予定を、登録できる
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="sec-post"' in page and page.count('class="schedule-row ps-row"') == 3  # 2件+追加用のひな型
    assert "次の予定:" in page
    client.post("/settings", data={"ps_id": ["a"], "ps_days": ["1"], "ps_time": ["10:00"], "ps_jitter": ["0"], "ps_count": ["1"]})  # チェックを外して保存
    assert settings.get_post_schedule(conn)["enabled"] is False
