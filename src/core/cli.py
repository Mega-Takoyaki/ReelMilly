"""ReelMilly CLIエントリポイント。"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import db as db_module
from core import generation
from core.analysis import analyze_asset
from core.config import Config, ensure_directories, load_config
from core.db import get_connection, init_db
from core.events import log_event
from core.ingest import ingest_inbox
from core.nsfw import try_create_classifier


def cmd_doctor(config: Config) -> int:
    ok = True
    print(f"[doctor] timezone: {config.timezone}")

    for path in config.paths.all_dirs():
        status = "OK" if path.is_dir() else "MISSING"
        if status == "MISSING":
            ok = False
        print(f"[doctor] directory {path}: {status}")

    try:
        conn = get_connection(config.paths.db_path)
        init_db(conn)
        conn.execute("SELECT COUNT(*) FROM assets").fetchone()
        conn.close()
        print(f"[doctor] database {config.paths.db_path}: OK")
    except Exception as exc:  # noqa: BLE001 - doctorは診断結果を表示するのが目的
        ok = False
        print(f"[doctor] database {config.paths.db_path}: NG ({exc})")

    events_status = "exists" if config.paths.events_path.exists() else "not yet created"
    print(f"[doctor] events log {config.paths.events_path}: {events_status}")

    fanvue_token = os.environ.get("FANVUE_API_TOKEN")
    if not fanvue_token:
        print("[doctor] Fanvue API: トークン未設定のためスキップ（.envのFANVUE_API_TOKENを設定してください）")
    else:
        # coreはposting/telegramに依存しない方針(ADR-0013)だが、doctorは
        # 本体+連携先の統合疎通確認という役割のため、ここでのみ遅延importする
        from posting.fanvue import DEFAULT_API_BASE_URL, DEFAULT_API_VERSION, FanvueClient

        base_url = os.environ.get("FANVUE_API_BASE_URL", DEFAULT_API_BASE_URL)
        api_version = os.environ.get("FANVUE_API_VERSION", DEFAULT_API_VERSION)
        try:
            FanvueClient(fanvue_token, base_url=base_url, api_version=api_version).get_me()
            print("[doctor] Fanvue API: OK")
        except Exception as exc:  # noqa: BLE001 - doctorは診断結果を表示するのが目的
            ok = False
            print(f"[doctor] Fanvue API: NG ({exc})")

    return 0 if ok else 1


def cmd_init(config: Config) -> int:
    created = ensure_directories(config)
    conn = get_connection(config.paths.db_path)
    init_db(conn)
    conn.close()
    log_event(config.paths.events_path, "init", created_dirs=[str(p) for p in created])
    for path in created:
        print(f"created: {path}")
    print(f"initialized database at {config.paths.db_path}")
    return 0


def cmd_ingest(config: Config) -> int:
    conn = get_connection(config.paths.db_path)
    init_db(conn)
    nsfw_classifier = try_create_classifier(config.nsfw)
    if nsfw_classifier is None:
        print("[ingest] torch/timm が見つからないため、NSFW自動仕分けをスキップします")
        print("[ingest] 有効化するには: pip install -e \".[nsfw]\"")
    generator = generation.try_create_generator(conn)
    if generator is None:
        print("[ingest] 生成AIの設定(APIキー/settings)が未完了のため、内容説明の取得をスキップします")
        print("[ingest] 有効化するには: pip install -e \".[ai]\" と .env への ANTHROPIC_API_KEY 設定")
    results = ingest_inbox(config, conn, nsfw_classifier=nsfw_classifier, generator=generator)
    conn.close()
    if not results:
        print("no new files in inbox")
        return 0
    for result in results:
        print(f"ingested {result.asset_id} ({result.kind}) -> {result.dest_path}")
    return 0


def cmd_analyze(config: Config) -> int:
    """`status="analyzing"`のアセットにNSFW自動仕分け・内容説明取得を再試行する(ADR-0015)。"""
    conn = get_connection(config.paths.db_path)
    init_db(conn)

    nsfw_classifier = try_create_classifier(config.nsfw)
    generator = generation.try_create_generator(conn)

    pending = db_module.list_assets(conn, status="analyzing", order="asc", limit=1000)
    if not pending:
        print("[analyze] 分析待ちのアセットはありません")
        conn.close()
        return 0

    promoted = 0
    for asset in pending:
        result = analyze_asset(conn, nsfw_classifier, generator, Path(asset["file_path"]))
        if result.success:
            db_module.update_asset(
                conn,
                asset["id"],
                status="ready",
                nsfw_auto_rating=result.nsfw_auto_rating,
                nsfw_auto_confidence=result.nsfw_auto_confidence,
                content_description=result.content_description,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            promoted += 1
            print(f"[analyze] {asset['id']}: readyに昇格しました")
        else:
            print(f"[analyze] {asset['id']}: 未完了のままです ({result.error})")

    conn.close()
    print(f"[analyze] {promoted}/{len(pending)} 件をreadyに昇格しました")
    return 0


def _today_str(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")


def cmd_run_drop(
    config: Config,
    count: int = 1,
    kind: str | None = None,
    rating: str | None = None,
) -> int:
    """Fanvueへの本編投稿を最大`count`件実行する(CLAUDE_HANDOFF.md 6章のdropジョブ、X投稿部分は未実装)。

    `kind`/`rating`で投稿対象を絞り込める(ADR-0015)。同日の実行有無は
    ジョブ全体(`drop`)単位で判定する(1回の実行でcount件まとめて投稿する)。
    """
    conn = get_connection(config.paths.db_path)
    init_db(conn)

    today = _today_str(config.timezone)
    if db_module.get_last_run_date(conn, "drop") == today:
        print(f"[run drop] 本日（{today}）は既に実行済みのためスキップします")
        conn.close()
        return 0

    fanvue_token = os.environ.get("FANVUE_API_TOKEN")
    if not fanvue_token:
        print("[run drop] FANVUE_API_TOKEN が未設定のため実行できません（.envを確認してください）")
        conn.close()
        return 1

    # coreはposting/telegramに依存しない方針(ADR-0013)だが、CLIエントリポイント
    # としてここでのみ遅延importする(doctorと同様の扱い)
    from posting.fanvue import DEFAULT_API_BASE_URL, DEFAULT_API_VERSION, FanvueClient
    from posting.jobs import run_fanvue_drop_batch

    base_url = os.environ.get("FANVUE_API_BASE_URL", DEFAULT_API_BASE_URL)
    api_version = os.environ.get("FANVUE_API_VERSION", DEFAULT_API_VERSION)
    handle = os.environ.get("FANVUE_HANDLE", "")
    url_template = os.environ.get("FANVUE_POST_URL_TEMPLATE", "https://www.fanvue.com/{handle}")

    client = FanvueClient(fanvue_token, base_url=base_url, api_version=api_version)
    generator = generation.try_create_generator(conn)
    results = run_fanvue_drop_batch(
        config,
        conn,
        client,
        fanvue_handle=handle,
        post_url_template=url_template,
        count=count,
        kind=kind,
        rating=rating,
        generator=generator,
    )

    db_module.set_last_run_date(conn, "drop", today)
    conn.close()

    exit_code = 0
    posted = 0
    for result in results:
        if result.executed:
            posted += 1
            print(f"[run drop] 投稿成功: {result.asset_id} -> {result.fanvue_url}")
        elif result.error:
            print(f"[run drop] 投稿失敗: {result.asset_id} ({result.error})")
            exit_code = 1
        else:
            print(f"[run drop] スキップ: {result.skipped_reason}")
    print(f"[run drop] {posted}/{len(results)} 件投稿しました")
    return exit_code


def _is_job_due(now: datetime, cadence_time: str) -> bool:
    """`now`が`cadence_time`(HH:MM)以降であればTrueを返す。"""
    hour_str, minute_str = cadence_time.split(":")
    return (now.hour, now.minute) >= (int(hour_str), int(minute_str))


def _parse_cadence_entry(entry) -> tuple[str, dict]:
    """cadenceの1エントリを(時刻, オプション辞書)に分解する(ADR-0015)。

    後方互換のため文字列("21:00")も許容し、その場合オプションは空とする。
    辞書の場合は"time"キーを時刻、それ以外のキーをオプションとして扱う。
    """
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        options = {key: value for key, value in entry.items() if key != "time"}
        return entry.get("time"), options
    raise ValueError(f"invalid cadence entry: {entry!r}")


def cmd_run_due(config: Config) -> int:
    """config.yamlの`cadence`設定を見て、時刻が来ていて未実行のジョブを実行する。

    現状`drop`ジョブのみ対応。X投稿ジョブ実装時にここへ追加する。
    """
    if not config.cadence:
        print("[run-due] config.yamlにcadence設定がありません（何もしません）")
        return 0

    now = datetime.now(ZoneInfo(config.timezone))
    ran_any = False
    for job_name, entry in config.cadence.items():
        if job_name != "drop":
            print(f"[run-due] 未対応のジョブ名のためスキップします: {job_name}")
            continue
        cadence_time, options = _parse_cadence_entry(entry)
        if not _is_job_due(now, cadence_time):
            print(f"[run-due] {job_name}: まだ実行時刻前です（設定 {cadence_time}、現在 {now.strftime('%H:%M')}）")
            continue
        ran_any = True
        cmd_run_drop(
            config,
            count=options.get("count", 1),
            kind=options.get("kind"),
            rating=options.get("rating"),
        )

    if not ran_any:
        print("[run-due] 実行したジョブはありません")
    return 0


def cmd_watch(config: Config, interval_seconds: int = 60) -> int:
    """`analyze`(分析待ちの再試行)と`run-due`を一定間隔で繰り返す常駐プロセス。

    Ctrl+Cで終了する。「定期バッチ処理」(ADR-0015)としてNSFW仕分け・内容説明
    取得の再試行と投稿ジョブの両方をこのループでまとめて扱う。
    """
    print(f"[watch] {interval_seconds}秒間隔でanalyze/run-dueを実行します（Ctrl+Cで終了）")
    try:
        while True:
            cmd_analyze(config)
            cmd_run_due(config)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n[watch] 終了します")
    return 0


def cmd_web(config: Config) -> int:
    from core.web.app import create_app

    app = create_app(config)
    print(f"[web] starting on http://{config.web.host}:{config.web.port} (Ctrl+Cで終了)")
    app.run(host=config.web.host, port=config.web.port, debug=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reelmilly")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="ディレクトリ・DB疎通を確認する")
    subparsers.add_parser("init", help="ディレクトリとDBを初期化する")
    subparsers.add_parser("ingest", help="inboxのメディアを取り込む")
    subparsers.add_parser(
        "analyze", help="analyzing状態のアセットにNSFW自動仕分け・内容説明取得を再試行する"
    )
    subparsers.add_parser("web", help="本体UI(ローカルWebアプリ)を起動する")
    run_parser = subparsers.add_parser("run", help="投稿ジョブを実行する")
    run_parser.add_argument("job", choices=["drop"], help="実行するジョブ名")
    run_parser.add_argument("--count", type=int, default=1, help="投稿を試みる最大件数(既定1)")
    run_parser.add_argument("--kind", choices=["image", "video"], default=None, help="対象を種別で絞り込む")
    run_parser.add_argument(
        "--rating", choices=["sfw", "suggestive", "explicit"], default=None, help="対象をcontent_ratingで絞り込む"
    )
    subparsers.add_parser("run-due", help="config.yamlのcadence設定を見て、時刻が来ているジョブを実行する")
    watch_parser = subparsers.add_parser(
        "watch", help="run-dueを一定間隔で繰り返す常駐プロセスとして起動する(Ctrl+Cで終了)"
    )
    watch_parser.add_argument(
        "--interval", type=int, default=60, help="run-dueを実行する間隔(秒、既定60)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(base_dir=Path.cwd())

    if args.command == "doctor":
        return cmd_doctor(config)
    if args.command == "init":
        return cmd_init(config)
    if args.command == "ingest":
        return cmd_ingest(config)
    if args.command == "analyze":
        return cmd_analyze(config)
    if args.command == "web":
        return cmd_web(config)
    if args.command == "run" and args.job == "drop":
        return cmd_run_drop(config, count=args.count, kind=args.kind, rating=args.rating)
    if args.command == "run-due":
        return cmd_run_due(config)
    if args.command == "watch":
        return cmd_watch(config, interval_seconds=args.interval)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
