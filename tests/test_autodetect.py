"""センシティブ領域の自動検出 → ぼかし・モザイクの加工版(検出器は差し替えて、流れを確かめる)。"""
from unittest.mock import patch

import pytest
from PIL import Image

from core import autodetect, db, edits, mask

from tests.test_library import env  # noqa: F401


def test_clean_options_validates():
    o = autodetect.clean_options(None)
    assert o["parts"] == ["genitals", "anus", "breasts", "buttocks"] and o["sensitivity"] == 7
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


def _jpeg_asset(conn, size=(200, 100)):
    asset = db.get_asset(conn, "a1")
    img = Image.new("RGB", size)
    for x in range(size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, ((x + y) * 5) % 256))
    img.save(asset["file_path"], format="JPEG")
    return asset


def test_preview_masks_detected_region_and_falls_back_to_center_sample(env):
    config, conn, client, tmp_path = env
    asset = _jpeg_asset(conn)
    opts = autodetect.clean_options(None)
    with _fake_boxes([(0.1, 0.1, 0.3, 0.3)]):
        out = autodetect.preview(asset["file_path"] and __import__("pathlib").Path(asset["file_path"]), "image", opts, "mosaic", 5)
    assert out["detected"] is True and len(out["regions"]) == 1 and out["image"].startswith("data:image/jpeg;base64,")
    with _fake_boxes([]):
        out = autodetect.preview(__import__("pathlib").Path(asset["file_path"]), "image", opts, "blur", 5)
    assert out["detected"] is False and out["regions"] == []  # 検出なし: 真ん中の矩形に、かけた見た目だけを返す
    with _fake_boxes([(0.1, 0.1, 0.3, 0.3)]):  # 範囲を渡すと、検出しない
        out = autodetect.preview(__import__("pathlib").Path(asset["file_path"]), "image", opts, "mosaic", 5, regions=[{"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}])
    assert out["regions"] == [{"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}]


def test_preview_api(env):
    config, conn, client, tmp_path = env
    _jpeg_asset(conn)
    body = {"asset_id": "a1", "style": "mosaic", "strength": 6, "options": {"parts": ["genitals"]}}
    with patch.object(autodetect, "available", lambda: False):
        assert client.post("/api/automask/preview", json=body).status_code == 503
    with patch.object(autodetect, "available", lambda: True), _fake_boxes([(0.2, 0.2, 0.5, 0.5)]):
        data = client.post("/api/automask/preview", json=body).get_json()
        assert data["detected"] and data["image"].startswith("data:image/jpeg")
        assert client.post("/api/automask/preview", json={**body, "options": {"parts": []}}).status_code == 400
        assert client.post("/api/automask/preview", json={**body, "asset_id": "nope"}).status_code == 404


def test_large_frames_are_detected_at_full_and_reduced_size_and_combined():
    import numpy as np

    seen = []

    class Fake:
        def detect(self, frame):
            seen.append(frame.shape[:2])
            h, w = frame.shape[:2]
            return [{"class": "ANUS_EXPOSED", "score": 0.9, "box": [w * 0.1, h * 0.1, w * 0.2, h * 0.2]}] if len(seen) == 2 else []

    with patch.object(autodetect, "_get_detector", lambda: Fake()):
        boxes = autodetect._boxes_in(np.zeros((1008, 1792, 3), np.uint8), {"ANUS_EXPOSED"}, 0.3)
    assert len(seen) == 2 and seen[0] == (1008, 1792) and max(seen[1]) == autodetect.DETECT_MAX_SIDE  # 縮小のほうだけで見つかっても、拾う
    assert len(boxes) == 1


def test_automask_defaults_can_be_saved_and_feed_the_dialog_options(env):
    from core import settings

    config, conn, client, tmp_path = env
    d = settings.get_automask_defaults(conn)
    assert d["parts"] == ["genitals", "anus", "breasts", "buttocks"] and d["style"] == "mosaic" and d["strength"] == 6
    settings.set_automask_defaults(conn, ["genitals"], 9, 40, 2, "blur", 3)
    d = settings.get_automask_defaults(conn)
    assert d["parts"] == ["genitals"] and d["sensitivity"] == 9 and d["padding"] == 40 and d["interval"] == 2.0 and d["style"] == "blur" and d["strength"] == 3
    with pytest.raises(mask.MaskError):  # 不正な値は、保存しない
        settings.set_automask_defaults(conn, [], 9, 40, 2, "blur", 3)
    assert settings.get_automask_defaults(conn)["parts"] == ["genitals"]
    opts = client.get("/api/automask/options").get_json()
    assert opts["style"] == "blur" and opts["strength"]["default"] == 3 and opts["sensitivity"]["default"] == 9
    assert [p["id"] for p in opts["parts"] if p["default"]] == ["genitals"]


def test_settings_form_saves_automask_and_translate_defaults(env):
    from core import settings

    config, conn, client, tmp_path = env
    res = client.post("/settings", data={
        "am_part": ["breasts", "buttocks"], "am_sensitivity": "4", "am_padding": "10", "am_interval": "3", "am_style": "blur", "am_strength": "8",
        "translate_mode": "replace", "translate_order": "original_first",
    })
    assert res.status_code in (200, 302)
    conn2 = db.get_connection(config.paths.db_path)
    d = settings.get_automask_defaults(conn2)
    assert d["parts"] == ["breasts", "buttocks"] and d["style"] == "blur" and d["strength"] == 8 and d["sensitivity"] == 4
    assert settings.get_translate_settings(conn2) == {"mode": "replace", "order": "original_first"}
