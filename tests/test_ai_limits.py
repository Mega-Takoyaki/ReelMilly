"""AI処理の負荷: 一時停止・再開、CPUスレッド数、優先度。"""
from unittest.mock import MagicMock, patch

from core import db, settings, worker
from core.nsfw import NsfwResult
from core.web.app import create_app

from tests.test_worker import _insert, _run, _setup


def _classifier():
    classifier = MagicMock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.9)
    return classifier


def test_defaults_and_roundtrip(tmp_path):
    config, conn = _setup(tmp_path)
    assert settings.get_ai_limits(conn) == {"paused": False, "cpu_threads": 0, "low_priority": True}
    settings.set_ai_limits(conn, paused=True, cpu_threads=3, low_priority=False)
    assert settings.get_ai_limits(conn) == {"paused": True, "cpu_threads": 3, "low_priority": False}


def test_paused_worker_leaves_queue_untouched_and_resumes(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path)
    classifier = _classifier()
    db.enqueue_ai_task(conn, "a1", "nsfw")

    settings.set_ai_limits(conn, paused=True)
    assert _run(config, conn, classifier=classifier) == (0, 0)  # 何も処理しない
    classifier.classify.assert_not_called()
    assert db.ai_task_counts(conn)["queued"] == 1               # 待機中のまま残る
    assert worker.get_status(conn)["paused"] is True

    settings.set_ai_limits(conn, paused=False)
    assert _run(config, conn, classifier=classifier) == (1, 0)  # 再開すると、続きから
    assert db.ai_task_counts(conn)["queued"] == 0


def test_pause_releases_loaded_models(tmp_path):
    config, conn = _setup(tmp_path)
    w = worker.AnalysisWorker(config, persistent=True)
    w._generator, w._generator_key, w._classifier, w._classifier_loaded = object(), ("p", "m"), object(), True
    w.release_models()
    assert w._generator is None and w._classifier is None and w._classifier_loaded is False


def test_apply_limits_sets_priority_and_threads(tmp_path):
    config, conn = _setup(tmp_path)
    w = worker.AnalysisWorker(config, persistent=True)
    fake_torch = MagicMock()
    settings.set_ai_limits(conn, cpu_threads=2, low_priority=True)
    with patch("core.worker._set_process_priority") as prio, patch.dict("sys.modules", {"torch": fake_torch}):
        w.apply_limits(conn)
        w.apply_limits(conn)  # 同じ値では、やり直さない
    prio.assert_called_once_with(True)
    fake_torch.set_num_threads.assert_called_once_with(2)
    settings.set_ai_limits(conn, cpu_threads=4, low_priority=False)
    with patch("core.worker._set_process_priority") as prio, patch.dict("sys.modules", {"torch": fake_torch}):
        w.apply_limits(conn)
    prio.assert_called_once_with(False)
    fake_torch.set_num_threads.assert_called_with(4)


def test_api_roundtrip_and_validation(tmp_path):
    config, conn = _setup(tmp_path)
    client = create_app(config).test_client()
    data = client.get("/api/ai/limits").get_json()
    assert data["limits"]["paused"] is False and data["cpu_count"] >= 1 and "queued" in data["status"]
    res = client.post("/api/ai/limits", json={"paused": True, "cpu_threads": 2, "low_priority": False}).get_json()
    assert res["limits"] == {"paused": True, "cpu_threads": 2, "low_priority": False} and res["status"]["paused"] is True
    assert client.post("/api/ai/limits", json={"cpu_threads": "abc"}).status_code == 400
    assert settings.get_ai_limits(conn)["paused"] is True
    assert 'id="ai-limits"' in client.get("/settings").get_data(as_text=True)


def test_set_process_priority_really_changes_the_priority_class_on_windows():
    import os
    import subprocess
    import sys

    import pytest

    if os.name != "nt":
        pytest.skip("Windowsのみ")
    code = (
        "import sys, subprocess, os; sys.path.insert(0, 'src'); from core import worker\n"
        "get = lambda: subprocess.run(['powershell','-NoProfile','-Command','(Get-Process -Id %d).PriorityClass' % os.getpid()], capture_output=True, text=True).stdout.strip()\n"
        "worker._set_process_priority(True); low = get(); worker._set_process_priority(False); print(low, get())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=os.getcwd()).stdout.split()
    assert out == ["BelowNormal", "Normal"]
