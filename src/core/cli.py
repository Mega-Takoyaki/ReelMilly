"""ReelMilly CLIエントリポイント。"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from core.config import Config, ensure_directories, load_config
from core.db import get_connection, init_db
from core.events import log_event
from core.ingest import ingest_inbox
from core.nsfw import NsfwClassifier


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


def _try_create_nsfw_classifier(config: Config) -> NsfwClassifier | None:
    """torch/timmが未インストールの環境でもingest自体は動くよう、事前に有無を確認する。"""
    if importlib.util.find_spec("torch") is None or importlib.util.find_spec("timm") is None:
        print("[ingest] torch/timm が見つからないため、NSFW自動仕分けをスキップします")
        print("[ingest] 有効化するには: pip install -e \".[nsfw]\"")
        return None
    return NsfwClassifier(config.nsfw)


def cmd_ingest(config: Config) -> int:
    conn = get_connection(config.paths.db_path)
    init_db(conn)
    nsfw_classifier = _try_create_nsfw_classifier(config)
    results = ingest_inbox(config, conn, nsfw_classifier=nsfw_classifier)
    conn.close()
    if not results:
        print("no new files in inbox")
        return 0
    for result in results:
        print(f"ingested {result.asset_id} ({result.kind}) -> {result.dest_path}")
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
    subparsers.add_parser("web", help="本体UI(ローカルWebアプリ)を起動する")
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
    if args.command == "web":
        return cmd_web(config)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
