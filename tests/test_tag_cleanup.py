"""タグの整形(規則)・整理案・適用(退避・取り消し)・別名の対応表・定期実行・保存時の自動整形。"""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from core import analysis, db, notifications, tag_cleanup
from core.tag_normalizer import normalize
from core.web.app import create_app

from tests.test_worker import _insert, _setup  # noqa: F401

JST = ZoneInfo("Asia/Tokyo")


# ------------------------------------------------------------------ 規則
@pytest.mark.parametrize("name,expected", [
    ("スカートとハイヒール", ["スカート", "ハイヒール"]),                       # 複合タグを分ける
    ("ブラとパンツ", ["ブラ", "パンツ"]),
    ("プールや庭", ["プール", "庭"]),
    ("レッドビキニ", ["赤", "ビキニ"]),                                       # 色を分ける(標準は、漢字の赤)
    ("赤いビキニ", ["赤", "ビキニ"]),
    ("ピンクのスカート", ["ピンク", "スカート"]),
    ("白シャツ", ["白", "シャツ"]),
    ("ホワイトシャツ", ["白", "シャツ"]),
    ("黑色スカート", ["黒", "スカート"]),                                      # 中国語の字
    ("ピンクのスカートと黒いスカート", ["ピンク", "スカート", "黒"]),
    ("比基尼", ["ビキニ"]), ("泳池", ["プール"]), ("喷水の前", ["噴水の前"]),
    ("胸衣", ["ブラ"]), ("坐り", ["座る"]), ("女", ["女性"]), ("巨大乳", ["巨乳"]),  # 別名
    ("ハイヒールで歩き", ["歩く", "ハイヒール"]),                              # 文のタグ: 姿勢と、先頭の名詞を残す
    ("膝を曲げて座っている", ["座る"]),
    ("手を上げて立っている", ["立つ"]),
    ("手にファイルを持つ", []),                                               # 体の部位だけが残る文は、捨てる
    ("黒いス", ["黒"]),                                                        # 1文字のかなは、捨てる
])
def test_normalize_rules(name, expected):
    assert normalize(name).tags == expected


@pytest.mark.parametrize("name", ["ビキニ", "プール", "女性", "ブラウス", "金髪", "黒髪", "青空", "赤ちゃん", "ヴィクトリー・ポーズ", "ブラジリアン・スカート", "ビジネスイベントの会場", "巨乳"])
def test_good_tags_are_left_alone(name):
    result = normalize(name)
    assert result.tags == [name] and not result.changed  # 色で始まる別の言葉・名前の一部の「・」・正しい標準名は、変えない


def test_custom_aliases_override_and_are_applied():
    assert normalize("赤ビキニ").tags == ["赤", "ビキニ"]
    assert normalize("スポーツブラ", {"スポーツブラ": "ブラ"}).tags == ["ブラ"]
    assert normalize("座り", {"座り": "すわる"}).tags == ["すわる"]  # 画面の対応表が、組み込みより優先


# ------------------------------------------------------------------ 整理案と適用
@pytest.fixture
def env(tmp_path):
    config, conn = _setup(tmp_path)
    for i in (1, 2, 3):
        _insert(conn, tmp_path, f"a{i}")
    return config, conn


def tags_of(conn, asset_id):
    return sorted(db.list_tags_for_asset(conn, asset_id))


def test_plan_lists_only_what_changes_and_does_not_touch_data(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    db.add_tag_to_asset(conn, "a2", "スカートとハイヒール")
    db.add_tag_to_asset(conn, "a2", "ビキニ")  # 変わらない
    items = tag_cleanup.plan(conn)
    assert {i["name"]: i["becomes"] for i in items} == {"レッドビキニ": ["赤", "ビキニ"], "スカートとハイヒール": ["スカート", "ハイヒール"]}
    assert tag_cleanup.summary(conn, items)["tags_before"] == 3
    assert tags_of(conn, "a1") == ["レッドビキニ"]  # プレビューでは、変更しない


def test_apply_rewrites_assets_merges_and_removes_old_tags_with_backup(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    db.add_tag_to_asset(conn, "a2", "赤いビキニ")
    db.add_tag_to_asset(conn, "a2", "ビキニ")
    db.add_tag_to_asset(conn, "a3", "ビキニ")

    result = tag_cleanup.apply(conn, config)

    assert result["applied"] == 2 and result["assets"] == 2
    assert tags_of(conn, "a1") == ["ビキニ", "赤"] and tags_of(conn, "a2") == ["ビキニ", "赤"] and tags_of(conn, "a3") == ["ビキニ"]
    assert sorted(db.list_all_tags(conn)) == ["ビキニ", "赤"]  # 古いタグは、消える(統合される)
    from pathlib import Path

    assert Path(result["backup"]).exists() and "reelmilly-tags-" in result["backup"]  # 適用の前の退避
    assert tag_cleanup.plan(conn) == []  # 整理済み


def test_apply_only_selected_names(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    db.add_tag_to_asset(conn, "a1", "スカートとパンツ")
    tag_cleanup.apply(conn, config, ["レッドビキニ"])
    assert tags_of(conn, "a1") == ["スカートとパンツ", "ビキニ", "赤"]


def test_apply_rolls_back_on_failure(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    db.add_tag_to_asset(conn, "a1", "スカートとパンツ")
    real = tag_cleanup.normalize
    calls = {"n": 0}
    original_execute = conn.execute

    class Boom(Exception):
        pass

    def failing_execute(sql, *args):
        if sql.startswith("DELETE FROM tags") and calls["n"] == 0:
            calls["n"] += 1
            raise Boom("disk")
        return original_execute(sql, *args)

    class Wrapper:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args):
            return failing_execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    with pytest.raises(Boom):
        tag_cleanup.apply(Wrapper(conn), config)
    assert tags_of(conn, "a1") == ["スカートとパンツ", "レッドビキニ"]  # 途中で失敗したら、すべて元に戻る


# ------------------------------------------------------------------ 保存時の自動整形
def test_auto_tags_are_normalized_when_saved_but_manual_tags_are_not(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "スカートとパンツ")  # 人が付けたタグは、そのまま
    result = analysis.AnalysisResult(success=True, suggested_tags=["レッドビキニ", "スカートとハイヒール", "泳池"], nsfw_auto_rating="sfw")
    analysis.apply_auto_tags(conn, "a1", result)
    assert tags_of(conn, "a1") == ["sfw", "スカート", "スカートとパンツ", "ハイヒール", "ビキニ", "プール", "赤"]
    conn.execute("INSERT INTO tag_aliases (alias, canonical) VALUES ('ネイルハイヒール', 'ハイヒール')")
    conn.commit()
    analysis.apply_auto_tags(conn, "a2", analysis.AnalysisResult(success=True, suggested_tags=["ネイルハイヒール"]))
    assert tags_of(conn, "a2") == ["ハイヒール"]  # 画面の対応表も、保存時に使う


# ------------------------------------------------------------------ 画面のAPI
def test_api_plan_apply_and_aliases(env):
    config, conn = env
    client = create_app(config).test_client()
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    plan = client.get("/api/tag-cleanup/plan").get_json()
    assert plan["items"][0]["name"] == "レッドビキニ" and plan["summary"]["changed"] == 1 and "color" in plan["kinds"]

    assert client.post("/api/tag-aliases", json={"alias": "同じ", "canonical": "同じ"}).status_code == 400
    added = client.post("/api/tag-aliases", json={"alias": "赤ビキニ水着", "canonical": "ビキニ"}).get_json()
    assert added["custom"] == {"赤ビキニ水着": "ビキニ"} and "胸衣" in added["builtin"]
    assert client.delete("/api/tag-aliases", json={"alias": "赤ビキニ水着"}).get_json()["custom"] == {}

    result = client.post("/api/tag-cleanup/apply", json={"names": ["レッドビキニ"]}).get_json()
    assert result["applied"] == 1 and tags_of(conn, "a1") == ["ビキニ", "赤"]
    assert client.post("/api/tag-cleanup/apply", json={}).get_json()["applied"] == 0

    page = client.get("/settings").get_data(as_text=True)
    assert 'id="sec-tags"' in page and 'id="tc-plan"' in page and 'name="tag_cleanup_weekday"' in page


# ------------------------------------------------------------------ 定期実行(週1回など)
def test_weekly_schedule_notifies_or_applies_once_per_day(env):
    config, conn = env
    db.add_tag_to_asset(conn, "a1", "レッドビキニ")
    assert tag_cleanup.get_schedule(conn)["enabled"] is False  # 既定: オフ
    sunday_5 = datetime(2026, 10, 11, 5, 0, tzinfo=JST)  # 日曜
    assert tag_cleanup.run_if_due(conn, config, sunday_5, log=lambda m: None) is None  # オフの間は、何もしない

    tag_cleanup.set_schedule(conn, True, 6, "04:00", False)  # 日曜 4:00・自動適用なし
    assert tag_cleanup.run_if_due(conn, config, datetime(2026, 10, 10, 5, 0, tzinfo=JST), log=lambda m: None) is None  # 土曜
    assert tag_cleanup.run_if_due(conn, config, datetime(2026, 10, 11, 3, 59, tzinfo=JST), log=lambda m: None) is None  # 時刻前
    message = tag_cleanup.run_if_due(conn, config, sunday_5, log=lambda m: None)
    assert "整理案が1種類" in message and tags_of(conn, "a1") == ["レッドビキニ"]  # 通知だけで、適用しない
    assert notifications.list_notifications(conn, 1)[0]["title"] == "タグの整理案があります"
    assert tag_cleanup.run_if_due(conn, config, sunday_5, log=lambda m: None) is None  # 同じ日は、2回目をしない

    tag_cleanup.set_schedule(conn, True, 6, "04:00", True)  # 自動で適用する
    db.set_last_run_date(conn, "tag_cleanup", "2026-10-04")
    message = tag_cleanup.run_if_due(conn, config, sunday_5, log=lambda m: None)
    assert "自動で整理しました" in message and tags_of(conn, "a1") == ["ビキニ", "赤"]


def test_schedule_validation_and_form(env):
    config, conn = env
    tag_cleanup.set_schedule(conn, True, "9", "25:99", True)  # 不正な値は、範囲内・いまの値に
    assert tag_cleanup.get_schedule(conn) == {"enabled": True, "weekday": 6, "time": "04:00", "auto_apply": True}
    client = create_app(config).test_client()
    client.post("/settings", data={"tag_cleanup_enabled": "on", "tag_cleanup_weekday": "2", "tag_cleanup_time": "23:30"})
    assert tag_cleanup.get_schedule(conn) == {"enabled": True, "weekday": 2, "time": "23:30", "auto_apply": False}
