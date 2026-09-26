from core.events import log_event, read_events


def test_log_event_appends_jsonl(tmp_path):
    events_path = tmp_path / "state" / "events.jsonl"

    log_event(events_path, "ingest_ok", asset_id="a1", kind="image")
    log_event(events_path, "drop_ok", asset_id="a1", fanvue_url="https://example.com")

    events = read_events(events_path)
    assert len(events) == 2
    assert events[0]["event"] == "ingest_ok"
    assert events[0]["asset_id"] == "a1"
    assert "timestamp" in events[0]
    assert events[1]["event"] == "drop_ok"


def test_read_events_missing_file_returns_empty_list(tmp_path):
    assert read_events(tmp_path / "no-such-file.jsonl") == []
