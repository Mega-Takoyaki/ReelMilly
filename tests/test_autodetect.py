"""センシティブ領域の自動検出 → ぼかし・モザイクの加工版(検出器は差し替えて、流れを確かめる)。"""
from unittest.mock import patch

import pytest
from PIL import Image

from core import autodetect, db, edits, mask

from tests.test_library import env  # noqa: F401


def test_clean_options_validates():
    o = autodetect.clean_options(None)
    assert o["parts"] == ["genitals", "anus"] and o["sensitivity"] == 7
    assert autodetect.clean_options({"parts": ["breasts"], "padding": 0})["parts"] == ["breasts"]
    for bad in ({"parts": []}, {"parts": ["x"]}, {"sensitivity": 11}, {"padding": 101}, {"interval": 0}, {"sensitivity": "a"}):
        with pytest.raises(mask.MaskError):
            autodetect.clean_options(bad)


def test_sensitivity_maps_to_threshold():
    assert autodetect.threshold_for(10) < autodetect.threshold_for(1)
    assert 0.0 < autodetect.threshold_for(10) < autodetect.threshold_for(7) < 1.0


def test_merge_boxes_joins_overlaps_and_respects_limit():
    assert autodetect.merge_boxes([(0.1, 0.1, 0.3, 0.3), (0.2, 0.2, 0.4, 0.4)]) == [(0.1, 0.1, 0.4, 0.4)]
    far = [(i * 0.1, 0.0, i * 0.1 + 0.05, 0.05) for i in range(10)]
    assert len(autodetect.merge_boxes(far)) == mask.MAX_REGIONS


def _fake_boxes(boxes):
    return patch.object(autodetect, "_boxes_in", lambda frame, labels, threshold: boxes)


def test_auto_mask_edit_detects_applies_and_records_regions(env):
    config, conn, client, tmp_path = env
    asset = db.get_asset(conn, "a1")
    img = Image.new("RGB", (200, 100))
    for x in range(200):
        for y in range(100):
            img.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, ((x + y) * 5) % 256))
    img.save(asset["file_path"], format="JPEG")
    with patch.object(autodetect, "available", lambda: True):
        edit_id = edits.enqueue_automask(conn, asset, {"parts": ["genitals"], "padding": 0}, "mosaic", 5)
    queued = edits.get_edit(conn, "a1", edit_id)
    assert "自動検出" in queued["summary"] and queued["params"]["regions"] == []
    edit = edits.claim_next(conn)
    with _fake_boxes([(0.25, 0.25, 0.75, 0.75)]):
        assert edits.run_edit(conn, edit, asset) is None
    done = edits.get_edit(conn, "a1", edit_id)
    assert done["status"] == "done" and done["filename"]
    assert done["params"]["regions"] == [{"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}]
    assert "1か所" in done["summary"] and done["summary"].startswith("自動検出")
    out, base = Image.open(edits.edit_path(asset, done)), Image.open(asset["file_path"])
    assert max(abs(a - b) for a, b in zip(out.getpixel((2, 2)), base.getpixel((2, 2)))) <= 6  # 範囲の外は、ほぼ変わらない(JPEGの再圧縮ぶんの差だけ)
    inside = [(x, y) for x in range(55, 145, 5) for y in range(30, 70, 5)]
    assert any(max(abs(a - b) for a, b in zip(out.getpixel(p), base.getpixel(p))) > 20 for p in inside)


def test_auto_mask_with_nothing_found_makes_no_edit_and_notifies(env):
    config, conn, client, tmp_path = env
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (120, 80), (10, 10, 10)).save(asset["file_path"], format="JPEG")
    with patch.object(autodetect, "available", lambda: True):
        edits.enqueue_automask(conn, asset, None, "blur", 5)
    with _fake_boxes([]), patch("core.storage.check") as chk:
        chk.return_value.available = True
        assert edits.run_pending(conn, config, log=lambda *_: None) == 1
    assert [e for e in edits.list_edits(conn, "a1") if e["kind"] == "mask"] == []
    row = conn.execute("SELECT level, title FROM notifications ORDER BY id DESC LIMIT 1").fetchone()
    assert row["level"] == "info" and "見つかりませんでした" in row["title"]


def test_api_batch_reports_missing_model_and_queues(env):
    config, conn, client, tmp_path = env
    with patch.object(autodetect, "available", lambda: False):
        res = client.post("/api/automask/apply-batch", json={"asset_ids": ["a1"], "style": "mosaic"})
        assert res.status_code == 503
    with patch.object(autodetect, "available", lambda: True):
        res = client.post("/api/automask/apply-batch", json={"asset_ids": ["a1", "nope"], "style": "mosaic", "strength": 5, "options": {"parts": ["genitals"]}})
        data = res.get_json()
    assert data["queued"] == 1 and data["failed"][0]["id"] == "nope"
    bad = client.post("/api/automask/apply-batch", json={"asset_ids": ["a1"], "style": "mosaic", "options": {"parts": []}}) if autodetect.available() else None
