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
from core import storage
from core import generation, settings
from core.config import Config, ensure_directories, load_config
from core.db import get_connection, init_db
from core.events import log_event
from core.ingest import ingest_inbox
from core.nsfw import try_create_classifier
from core import worker as worker_module
from core.worker import AnalysisWorker


def _try_create_fanvue_client(config: Config):
    """OAuth連携済み(ADR-0021)の場合のみFanvueClientを返す。

    coreはposting/telegramに依存しない方針(ADR-0013)だが、doctor/run drop
    はCLIエントリポイントとしてここでのみ遅延importする。
    未連携の場合は`(None, 理由)`を返す。
    """
    from posting.fanvue import DEFAULT_API_BASE_URL, DEFAULT_API_VERSION, FanvueClient
    from posting.fanvue_oauth import FanvueTokenStore

    client_id = os.environ.get("FANVUE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("FANVUE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None, "FANVUE_OAUTH_CLIENT_ID/FANVUE_OAUTH_CLIENT_SECRETが未設定です（.envを確認してください）"

    store = FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json")
    if store.load() is None:
        return None, "Fanvueと未連携です（本体UIの設定画面から連携してください）"

    base_url = os.environ.get("FANVUE_API_BASE_URL", DEFAULT_API_BASE_URL)
    api_version = os.environ.get("FANVUE_API_VERSION", DEFAULT_API_VERSION)
    token_provider = lambda: store.get_access_token(client_id, client_secret)  # noqa: E731
    return FanvueClient(token_provider, base_url=base_url, api_version=api_version), None


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

    fanvue_client, fanvue_skip_reason = _try_create_fanvue_client(config)
    if fanvue_client is None:
        print(f"[doctor] Fanvue API: 未接続のためスキップ（{fanvue_skip_reason}）")
    else:
        try:
            fanvue_client.get_me()
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


def _storage_ready(config: Config, conn, label: str) -> bool:
    """ストレージが使える状態かを調べる。使えないときは理由を表示してFalseを返す。"""
    status = storage.check(config, conn)
    if not status.available:
        print(f"[{label}] {status.reason}")
        print(f"[{label}] 画面の設定「ストレージ」で、場所を直してください")
    return status.available


def _notify_duplicates(config: Config) -> None:
    """重複(同じ中身のファイル)が増えていれば、通知する。"""
    from core import duplicates

    conn = get_connection(config.paths.db_path)
    try:
        init_db(conn)
        duplicates.notify_new_duplicates(conn)
    finally:
        conn.close()


def cmd_ingest(config: Config, defer_analysis: bool = False) -> int:
    conn = get_connection(config.paths.db_path)
    init_db(conn)
    storage.apply_override(config, conn)
    if not _storage_ready(config, conn, "ingest"):
        conn.close()
        return 1
    if defer_analysis:
        results = ingest_inbox(config, conn, defer_analysis=True)
        conn.close()
        for result in results:
            print(f"ingested {result.asset_id} ({result.kind}) -> 分析待ち")
        _notify_duplicates(config)
        return 0
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
    _notify_duplicates(config)
    return 0


def cmd_analyze(
    config: Config,
    worker: AnalysisWorker | None = None,
    enqueue_pending: bool = True,
) -> int:
    """AI処理(sfw/nsfw判定・説明文生成)のキューを処理する(ADR-0015)。

    `enqueue_pending=True`(手動の`reelmilly analyze`)では、まだ判定結果・説明が無い
    アセットを全てキューに積んでから処理する。`watch`は`False`で呼び、積むのは
    UIの操作と設定画面の定期実行に任せる。判定と説明の両方が揃った分析中のアセットは
    `status="pending_approval"`(人間の承認待ち)になる。分析待ちの間に既に
    `content_rating_confirmed`が立てられていた場合は`status="ready"`に直接昇格する
    (ADR-0019)。実処理は`core.worker.AnalysisWorker`が行い、複数プロセスの
    同時実行は排他される。`watch`は同じworkerを使い回してモデルの再ロードを避ける。
    """
    conn = get_connection(config.paths.db_path)
    init_db(conn)
    if enqueue_pending:
        for kind in ("nsfw", "describe"):
            worker_module.enqueue_for_assets(conn, db_module.unprocessed_asset_ids(conn, kind), [kind])
    result = (worker or AnalysisWorker(config)).run_once(conn)
    conn.close()
    if result is None:
        return 0
    done, failed = result
    if done == 0 and failed == 0:
        print("[analyze] 処理待ちのAI処理はありません")
    else:
        print(f"[analyze] 成功 {done}件 / 失敗 {failed}件")
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

    client, skip_reason = _try_create_fanvue_client(config)
    if client is None:
        print(f"[run drop] 実行できません（{skip_reason}）")
        conn.close()
        return 1

    # coreはposting/telegramに依存しない方針(ADR-0013)だが、CLIエントリポイント
    # としてここでのみ遅延importする(doctorと同様の扱い)
    from posting.jobs import run_fanvue_drop_batch

    handle = os.environ.get("FANVUE_HANDLE", "")
    url_template = os.environ.get("FANVUE_POST_URL_TEMPLATE", "https://www.fanvue.com/{handle}")

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
    設定画面の「inboxフォルダの自動取り込み」がオンの場合、`ingest`も
    毎回実行する(ADR-0020)。オン/オフは`settings`テーブルに保存され、
    watchプロセスを再起動しなくても次のループから反映される。
    """
    print(f"[watch] {interval_seconds}秒間隔でanalyze/run-dueを実行します（Ctrl+Cで終了）")
    worker = AnalysisWorker(config, persistent=True)
    try:
        while True:
            conn = get_connection(config.paths.db_path)
            init_db(conn)
            storage.apply_override(config, conn)
            auto_ingest = settings.get_auto_ingest(conn) and storage.check(config, conn).available
            conn.close()
            if auto_ingest:
                cmd_ingest(config, defer_analysis=True)
            cmd_analyze(config, worker, enqueue_pending=False)
            _notify_duplicates(config)  # 定期処理: 取り込み(自動取り込み等)で増えた重複を検出して通知する
            cmd_run_due(config)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n[watch] 終了します")
    finally:
        conn = get_connection(config.paths.db_path)
        worker.close(conn)
        conn.close()
    return 0


def cmd_migrate_filenames(config: Config) -> int:
    """既存の作品のファイル名を作品ID(`<ID>.<拡張子>`)へそろえ、元の名前を記録する。何度実行しても安全。"""
    from core.filenames import normalize_asset_files

    conn = get_connection(config.paths.db_path)
    init_db(conn)
    result = normalize_asset_files(config, conn)
    conn.close()
    print(f"[migrate-filenames] {result.renamed}件のファイル名を作品IDに改名しました")
    for message in result.skipped:
        print(f"[migrate-filenames] スキップ: {message}")
    return 0


def cmd_web(config: Config) -> int:
    from core.web.app import create_app

    conn = get_connection(config.paths.db_path)
    init_db(conn)
    storage.apply_override(config, conn)
    status = storage.check(config, conn)
    conn.close()
    if status.available:
        cmd_migrate_filenames(config)  # 旧バージョンで取り込んだ作品のファイル名も、起動時にそろえる

        def backfill() -> None:  # 旧バージョンで取り込んだ作品の幅・高さ(「フル」表示用)を、背景で補う
            from core import duplicates
            from core.dimensions import backfill_dimensions

            bg = get_connection(config.paths.db_path)
            try:
                backfill_dimensions(bg)
                duplicates.backfill_hashes(bg)  # 旧バージョンで取り込んだ作品の、中身のハッシュ(重複の検出用)
                duplicates.notify_new_duplicates(bg)
            finally:
                bg.close()

        import threading

        threading.Thread(target=backfill, name="dimensions-backfill", daemon=True).start()
    else:
        print(f"[web] {status.reason}")
        print("[web] 画像なしで起動します。画面の設定「ストレージ」で場所を直せます")

    app = create_app(config)
    print(f"[web] starting on http://{config.web.host}:{config.web.port} (Ctrl+Cで終了)")
    app.run(host=config.web.host, port=config.web.port, debug=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reelmilly")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="ディレクトリ・DB疎通を確認する")
    subparsers.add_parser("init", help="ディレクトリとDBを初期化する")
    subparsers.add_parser(
        "migrate-filenames", help="既存の作品のファイル名を作品IDへそろえ、元のファイル名を記録する"
    )
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
    if args.command != "init":
        conn = get_connection(config.paths.db_path)
        init_db(conn)
        storage.apply_override(config, conn)  # 設定画面で変えたストレージの場所
        conn.close()

    if args.command == "doctor":
        return cmd_doctor(config)
    if args.command == "init":
        return cmd_init(config)
    if args.command == "migrate-filenames":
        return cmd_migrate_filenames(config)
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
