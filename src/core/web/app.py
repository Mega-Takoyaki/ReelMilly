"""本体Web UI。ADR-0010/0012に基づき、ローカルWebアプリとして動作する。

一覧・フォルダ・タグ管理・NSFW仕分け結果の確認と`content_rating`確定操作を提供する。
全文検索は対象外（ADR-0010）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, url_for

from core import db, env_settings, generation
from core import duplicates, notifications, storage, watermark
from core.dimensions import read_dimensions
from core import worker as worker_module
from core.channels import POST_CHANNELS, POST_STATUS_LABELS
from core.ingest import DEFAULT_CHANNELS as DEFAULT_POST_CHANNELS
from core import settings as settings_module
from core.config import Config
from core.purge import purge_assets
from core.ingest import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ingest_inbox
from core.media import get_media_properties
from core.nsfw import try_create_classifier

CONTENT_RATINGS = ("sfw", "suggestive", "explicit")
SORT_LABELS = [
    ("created_desc", "登録日時（新しい順）"),
    ("created_asc", "登録日時（古い順）"),
    ("updated_desc", "更新日時（新しい順）"),
    ("name_asc", "ファイル名（A→Z）"),
    ("name_desc", "ファイル名（Z→A）"),
    ("pixels_desc", "画像サイズ（大きい順）"),
    ("pixels_asc", "画像サイズ（小さい順）"),
]
GRID_PAGE_SIZE = 200  # 一覧・ごみ箱で、1回に読み込む件数(下へスクロールすると続きを読み込む)
ALLOWED_UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_xhr() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _fanvue_token_store_path(config: Config) -> Path:
    return config.paths.state_dir / "fanvue_oauth_tokens.json"


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_upload_filename(original: str) -> str:
    """アップロードされたファイル名を、保存してよい名前に整える。

    werkzeugのsecure_filenameは日本語などASCII以外を全部消してしまい、「日本語.png」が
    拡張子なしの「png」になって取り込めなかった。ここでは、パスの区切りや保存できない
    文字だけを除き、日本語はそのまま残す。名前の部分が空になったときは「upload」にする。
    拡張子は小文字にそろえる。
    """
    name = re.split(r"[\\/]", original or "")[-1]  # フォルダ部分は捨てる
    path = Path(name)
    suffix = path.suffix.lower()
    stem = _FORBIDDEN_FILENAME_CHARS.sub("_", path.stem).strip(" .")
    if len(stem) > 80:
        stem = stem[:80]
    return f"{stem or 'upload'}{suffix}"


def _unique_inbox_path(inbox: Path, filename: str) -> Path:
    dest = inbox / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for _ in range(100):
        candidate = inbox / f"{stem}-{secrets.token_hex(3)}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not find a unique filename in inbox")


def _suggested_rating(asset) -> str | None:
    """承認フォームの初期選択値。確定済みならその値、未確定ならAI判定から保守的に推定する。"""
    if asset["content_rating"]:
        return asset["content_rating"]
    return {"sfw": "sfw", "nsfw": "explicit"}.get(asset["nsfw_auto_rating"])


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["REELMILLY_CONFIG"] = config

    def get_conn():
        conn = db.get_connection(config.paths.db_path)
        db.init_db(conn)
        storage.apply_override(config, conn)  # 設定画面で変えたストレージの場所を、このリクエストにも反映する
        return conn

    def storage_status():
        """このリクエストでのストレージの状態(リクエスト内では1回だけ調べる)。"""
        if "storage_status" not in g:
            conn = get_conn()
            g.storage_status = storage.check(config, conn)
            conn.close()
        return g.storage_status

    def require_storage():
        """取り込み・削除など、ファイルを触る操作の前に呼ぶ。使えないときは、理由つきで中断する。"""
        status = storage_status()
        if not status.available:
            abort(
                app.response_class(
                    json.dumps({"error": status.reason}, ensure_ascii=False), status=503, mimetype="application/json"
                )
            )

    app.add_template_filter(watermark.describe_positions, "wm_label")

    @app.template_filter("localtime")
    def localtime_filter(value):
        """ISO8601(UTC)の日時を、設定のタイムゾーンの「YYYY-MM-DD HH:MM」に変換する。"""
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return value or ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(config.timezone)).strftime("%Y-%m-%d %H:%M")

    @app.route("/")
    def index():
        conn = get_conn()
        # 絞り込みは項目内で複数選択でき(同じ名前のパラメータを繰り返す)、項目間はAND
        status_sel = request.args.getlist("status")
        rating_sel = request.args.getlist("content_rating")
        auto_sel = request.args.getlist("nsfw_auto")
        plan_sel = [v for v in request.args.getlist("plan") if v in ("planned", "none")]
        tag_sel = [v for v in request.args.getlist("tag") if v]
        folder_sel = [v for v in request.args.getlist("folder_id", type=int)]
        confirmed_sel = [v for v in request.args.getlist("confirmed") if v in ("0", "1")]
        post_sel = []
        for value in request.args.getlist("post"):  # "fanvue:posted"のように「投稿先:状態」
            ch, _, st = value.partition(":")
            if ch in POST_CHANNELS and st in POST_STATUS_LABELS:
                post_sel.append(value)
        tag_mode = "any" if request.args.get("tag_mode") == "any" else "all"  # 既定: すべて含む
        folder_mode = "all" if request.args.get("folder_mode") == "all" else "any"  # 既定: いずれか
        kind_sel = [v for v in request.args.getlist("kind") if v in ("image", "video")]
        ext_sel = [v.lower() for v in request.args.getlist("ext") if v.isalnum()]
        broken_mode = request.args.get("broken") if request.args.get("broken") in ("show", "only") else "hide"
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort") if request.args.get("sort") in db.SORTS else "created_desc"
        flat = request.args.get("flat") == "1"  # フラット表示(フォルダの中身も、すべて並べる)
        active_filters = bool(
            broken_mode != "hide" or kind_sel or ext_sel or status_sel or rating_sel or auto_sel or plan_sel or tag_sel or folder_sel or confirmed_sel or post_sel or q
        )
        # フォルダ表示(既定): 何も絞り込んでいないときは、最上部にフォルダを並べ、フォルダに入っていない作品だけを出す。
        # 絞り込み・検索中や、フォルダを開いているときは、該当する作品をそのまま出す
        folder_top = not flat and not active_filters

        list_args = dict(
            sort=sort,
            no_folder=folder_top,
            status=status_sel,
            content_rating=rating_sel,
            nsfw_auto=auto_sel,
            plan=plan_sel,
            kind=kind_sel,
            ext=ext_sel,
            tag_mode=tag_mode,
            folder_mode=folder_mode,
            broken=broken_mode,
            tag=tag_sel,
            folder_id=folder_sel,
            confirmed=[v == "1" for v in confirmed_sel],
            post_filters=[tuple(v.split(":", 1)) for v in post_sel],
            q=q,
        )
        offset = max(request.args.get("offset", 0, type=int), 0)
        total = db.list_assets(conn, count=True, **list_args)
        assets = db.list_assets(conn, limit=GRID_PAGE_SIZE, offset=offset, **list_args)
        # 幅・高さが未記録の作品(旧バージョンで取り込んだもの)は、ファイルから読んで補う(「フル」表示の枠用)
        if storage_status().available:
            for a in [a for a in assets if not a.get("width")][:60]:
                size = read_dimensions(Path(a["file_path"]), a["kind"])
                if size:
                    a["width"], a["height"] = size
                    conn.execute("UPDATE assets SET width = ?, height = ? WHERE id = ?", (size[0], size[1], a["id"]))
            conn.commit()
        asset_ids = [a["id"] for a in assets]
        posts = db.get_posts(conn, asset_ids)
        channels_map = db.get_channels_map(conn, asset_ids)
        if request.args.get("partial") == "cards":  # 下へスクロールしたときの続き(カードだけ)
            conn.close()
            html = render_template("_cards.html", assets=assets, posts=posts, channels_map=channels_map, post_channels=POST_CHANNELS)
            return jsonify({"html": html, "next": offset + len(assets), "total": total})
        facets = db.facets(conn, list(POST_CHANNELS), broken_mode)
        broken_total = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE is_broken = 1 AND deleted_at IS NULL"
        ).fetchone()[0]
        folders = db.list_folders(conn)
        folder_cards = db.folder_summaries(conn, broken_mode) if folder_top else []
        conn.close()

        def options(rows, labels=None, selected=()):
            """実在する値(件数つき)を選択肢にする。選択中の値が0件でも、選べる/外せるよう残す。"""
            out = [(v, f"{(labels or {}).get(v, v)} ({n})") for v, n in rows]
            for v in selected:
                if str(v) not in [str(x[0]) for x in out]:
                    out.append((v, f"{(labels or {}).get(v, v)} (0)"))
            return out

        status_labels = {
            "analyzing": "analyzing（未処理・分析中）",
            "pending_approval": "pending_approval（承認待ち）",
            "ready": "ready（承認済み）",
        }
        rating_labels = {db.NONE_VALUE: "（未設定・未承認）"}
        auto_labels = {db.NONE_VALUE: "（未判定）", "sfw": "sfw", "nsfw": "nsfw"}
        filter_options = {
            "status": options(facets["status"], status_labels, status_sel),
            "content_rating": options(facets["content_rating"], rating_labels, rating_sel),
            "nsfw_auto": options(facets["nsfw_auto"], auto_labels, auto_sel),
            "plan": options(facets["plan"], {"planned": "投稿予定あり", "none": "投稿予定なし"}, plan_sel),
            "tag": options(facets["tag"], None, tag_sel),
            "folder_id": [(fid, f"{name} ({n})") for fid, name, n in facets["folder"]]
            + [(fid, f["name"] + " (0)") for fid in folder_sel for f in folders if f["id"] == fid and fid not in [x[0] for x in facets["folder"]]],
            "confirmed": options(facets["confirmed"], {"0": "承認待ち", "1": "承認済み"}, confirmed_sel),
            "post": [
                (f"{ch}:{st}", f"{POST_CHANNELS[ch]['label']} {POST_STATUS_LABELS[st]} ({n})")
                for ch, st, n in facets["post"]
            ],
        }
        known_post = [v for v, _ in filter_options["post"]]
        filter_options["post"] += [(v, v + " (0)") for v in post_sel if v not in known_post]
        # ファイルタイプの2階層の選択肢。親(種別)を選ぶと、その配下の拡張子がすべて選ばれた扱いになる
        type_labels = {"image": "画像", "video": "動画"}
        type_tree = [
            {
                "kind": kind,
                "label": f"{type_labels.get(kind, kind)} ({count})",
                "checked": kind in kind_sel,
                "exts": [
                    {"ext": e, "label": f".{e or '(なし)'} ({n})", "checked": kind in kind_sel or e in ext_sel}
                    for e, n in exts
                ],
            }
            for kind, count, exts in facets["type"]
        ]
        return render_template(
            "index.html",
            assets=assets,
            total=total,
            next_offset=offset + len(assets),
            sort=sort,
            sort_options=SORT_LABELS,
            flat=flat,
            folder_cards=folder_cards if folder_top else [],
            open_folders=[f for f in folders if f["id"] in folder_sel] if not flat else [],
            folders=folders,
            filter_options=filter_options,
            tag_mode=tag_mode,
            folder_mode=folder_mode,
            broken_mode=broken_mode,
            broken_total=broken_total,
            type_tree=type_tree,
            type_count=len(ext_sel) if ext_sel else len(kind_sel),
            status_sel=status_sel,
            rating_sel=rating_sel,
            auto_sel=auto_sel,
            plan_sel=plan_sel,
            tag_sel=tag_sel,
            folder_sel=folder_sel,
            confirmed_sel=confirmed_sel,
            post_sel=post_sel,
            q=q,
            active_filters=active_filters,
            content_ratings=CONTENT_RATINGS,
            posts=posts,
            channels_map=channels_map,
            post_channels=POST_CHANNELS,
            post_status_labels=POST_STATUS_LABELS,
        )

    @app.context_processor
    def inject_common():
        conn = get_conn()
        count = db.count_trashed(conn)
        wm_defaults = settings_module.get_watermark_defaults(conn)  # 設定画面で編集する既定値
        conn.close()
        return {
            "storage": storage_status(),
            "trash_count": count,
            "wm_defaults": wm_defaults,
            "wm_positions": watermark.POSITIONS,
            "wm_grid": [[f"r{r}c{c}" for c in range(5)] for r in range(5)],
            "wm_ranges": {"opacity": watermark.OPACITY_RANGE, "size": watermark.SIZE_RANGE},
        }

    @app.route("/trash")
    def trash_page():
        conn = get_conn()
        offset = max(request.args.get("offset", 0, type=int), 0)
        total = db.list_assets(conn, trashed=True, count=True)
        assets = db.list_assets(conn, trashed=True, limit=GRID_PAGE_SIZE, offset=offset)
        conn.close()
        if request.args.get("partial") == "cards":
            html = render_template("_trash_cards.html", assets=assets)
            return jsonify({"html": html, "next": offset + len(assets), "total": total})
        return render_template("trash.html", assets=assets, total=total, next_offset=offset + len(assets))

    @app.route("/api/assets/trash", methods=["POST"])
    def api_trash_assets():
        ids = (request.get_json(silent=True) or {}).get("asset_ids") or []
        if not ids:
            return jsonify({"error": "asset_ids is required"}), 400
        conn = get_conn()
        moved = db.trash_assets(conn, ids)
        conn.close()
        return jsonify({"moved": moved})

    @app.route("/api/assets/restore", methods=["POST"])
    def api_restore_assets():
        ids = (request.get_json(silent=True) or {}).get("asset_ids") or []
        if not ids:
            return jsonify({"error": "asset_ids is required"}), 400
        conn = get_conn()
        restored = db.restore_assets(conn, ids)
        conn.close()
        return jsonify({"restored": restored})

    @app.route("/api/assets/purge", methods=["POST"])
    def api_purge_assets():
        """ごみ箱の作品を完全に削除する(元に戻せない)。asset_ids指定、またはall=trueでごみ箱を空にする。"""
        payload = request.get_json(silent=True) or {}
        ids = payload.get("asset_ids")
        if not payload.get("all") and not ids:
            return jsonify({"error": "asset_ids or all is required"}), 400
        require_storage()  # ファイルが見えない状態で記録だけ消さないように
        conn = get_conn()
        result = purge_assets(config, conn, None if payload.get("all") else ids)
        if result.deleted >= 2 or result.errors:
            notifications.add(
                conn, "trash", f"{result.deleted}件を完全に削除しました",
                "；".join(result.errors[:3]), "warning" if result.errors else "success",
            )
        conn.close()
        return jsonify({"deleted": result.deleted, "errors": result.errors})

    @app.route("/assets/<asset_id>/trash", methods=["POST"])
    def trash_asset(asset_id):
        conn = get_conn()
        db.trash_assets(conn, [asset_id])
        conn.close()
        return redirect(url_for("index"))

    @app.route("/assets/<asset_id>/restore", methods=["POST"])
    def restore_asset(asset_id):
        conn = get_conn()
        db.restore_assets(conn, [asset_id])
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/api/assets/broken", methods=["POST"])
    def api_set_broken():
        """破綻画像(キメラ)のフラグを付け外しする。付けた作品は、既定の一覧・投稿の対象から外れる。"""
        payload = request.get_json(silent=True) or {}
        ids = payload.get("asset_ids") or []
        if not ids or "broken" not in payload:
            return jsonify({"error": "asset_ids and broken are required"}), 400
        conn = get_conn()
        changed = db.set_broken(conn, ids, bool(payload["broken"]))
        conn.close()
        return jsonify({"changed": changed, "broken": bool(payload["broken"])})

    @app.route("/assets/<asset_id>/broken", methods=["POST"])
    def toggle_broken(asset_id):
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        if asset is None:
            conn.close()
            abort(404)
        db.set_broken(conn, [asset_id], not asset["is_broken"])
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/api/assets/post-plan", methods=["POST"])
    def api_post_plan():
        """投稿予定の有無を一括で切り替える。planned=falseで投稿先を空に(=投稿予定なし)、trueで既定の投稿先に戻す。"""
        payload = request.get_json(silent=True) or {}
        ids = payload.get("asset_ids") or []
        if not ids or "planned" not in payload:
            return jsonify({"error": "asset_ids and planned are required"}), 400
        targets = list(DEFAULT_POST_CHANNELS) if payload["planned"] else []
        conn = get_conn()
        for asset_id in ids:
            if db.get_asset(conn, asset_id) is not None:
                db.set_channels(conn, asset_id, targets)
        conn.close()
        return jsonify({"updated": len(ids), "planned": bool(payload["planned"])})

    @app.route("/assets/<asset_id>/channels", methods=["POST"])
    def update_channels(asset_id):
        """詳細画面から、投稿先(チェックしたSNS)を保存する。すべてオフなら投稿予定なし。"""
        conn = get_conn()
        if db.get_asset(conn, asset_id) is None:
            conn.close()
            abort(404)
        chosen = [c for c in request.form.getlist("channel") if c in POST_CHANNELS]
        db.set_channels(conn, asset_id, chosen)
        conn.close()
        if _is_xhr():
            return jsonify({"channels": chosen, "no_plan": not chosen})
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>")
    def asset_detail(asset_id):
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        if asset is None:
            conn.close()
            abort(404)
        channels = db.get_channels(conn, asset_id)
        tags = db.list_tags_for_asset(conn, asset_id)
        asset_folders = db.list_folders_for_asset(conn, asset_id)
        all_folders = db.list_folders(conn)
        asset_posts = db.get_posts(conn, [asset_id]).get(asset_id, {})
        conn.close()
        properties = get_media_properties(Path(asset["file_path"]), asset["kind"])
        return render_template(
            "asset_detail.html",
            asset=asset,
            channels=channels,
            tags=tags,
            asset_folders=asset_folders,
            all_folders=all_folders,
            content_ratings=CONTENT_RATINGS,
            properties=properties,
            suggested_rating=_suggested_rating(asset),
            posts=asset_posts,
            post_channels=POST_CHANNELS,
        )

    @app.route("/assets/upload", methods=["POST"])
    def upload_assets():
        require_storage()
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "no files provided"}), 400

        config.paths.inbox.mkdir(parents=True, exist_ok=True)
        saved_names = []
        rejected = []
        held = []  # 重複のため保留にしたファイル名
        seen_hashes: dict[str, str] = {}
        token = duplicates.new_token()
        conn_dup = get_conn()
        duplicates.cleanup_stale(config)
        for file in files:
            if not file.filename:
                continue
            filename = safe_upload_filename(file.filename)
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                rejected.append(file.filename)
                continue
            dest = _unique_inbox_path(config.paths.inbox, filename)
            file.save(dest)
            # 既にある作品(ごみ箱の中も含む)や、このアップロードの中の先のファイルと、同じ中身なら、
            # 取り込まずに保留にして、取り込むかどうかをあとで確認する
            content_hash = duplicates.file_hash(dest)
            existing = [a["id"] for a in duplicates.assets_with_hash(conn_dup, content_hash)] if content_hash else []
            same_as = seen_hashes.get(content_hash)
            if content_hash and (existing or same_as):
                duplicates.stage_duplicate(config, token, dest, content_hash, existing, same_as)
                held.append(dest.name)
                continue
            if content_hash:
                seen_hashes[content_hash] = dest.name
            saved_names.append(dest.name)

        # 分析(NSFW仕分け・内容説明)はVLM推論に数分かかるため、アップロードでは行わず
        # status="analyzing"で登録して即応答する。分析は別プロセスのワーカーが行う
        conn = get_conn()
        results = ingest_inbox(config, conn, defer_analysis=True)
        if results or held or rejected:
            parts = [f"{len(results)}件を取り込みました"]
            if held:
                parts.append(f"重複のため保留 {len(held)}件")
            if rejected:
                parts.append(f"対象外の形式 {len(rejected)}件")
            notifications.add(
                conn, "import", "アップロードが完了しました", "、".join(parts),
                "warning" if held or rejected else "success", action="duplicates" if held else None,
            )
        conn.close()
        conn_dup.close()

        return jsonify(
            {
                "uploaded": len(saved_names),
                "rejected": rejected,
                "ingested": len(results),
                "asset_ids": [r.asset_id for r in results],
                "duplicates_held": len(held),  # 重複のため、取り込まずに保留にした数(確認のダイアログを出す)
            }
        )

    @app.route("/api/ai-tasks", methods=["POST"])
    def enqueue_ai_tasks():
        """sfw/nsfw判定・説明文生成をキューに積む。処理は別プロセスのワーカーが非同期で行う。"""
        payload = request.get_json(silent=True) or {}
        asset_ids = payload.get("asset_ids") or []
        kind = payload.get("kind")
        kinds = {"nsfw": ["nsfw"], "describe": ["describe"], "both": ["nsfw", "describe"]}.get(kind)
        if not asset_ids or kinds is None:
            return jsonify({"error": "asset_ids and a valid kind are required"}), 400
        conn = get_conn()
        known = [
            a for a in asset_ids
            if (db.get_asset(conn, a) or {}).get("deleted_at", "x") is None  # ごみ箱の作品は対象外
        ]
        queued = worker_module.enqueue_for_assets(conn, known, kinds)
        status = worker_module.get_status(conn, config.timezone)
        conn.close()
        return jsonify({"queued": queued, "skipped": len(known) * len(kinds) - queued, "status": status})

    @app.route("/api/ai-live")
    def ai_live():
        """一覧/詳細画面が定期的に取得する、AI処理の全体状況と表示中アセットの要約。"""
        ids = [i for i in (request.args.get("ids") or "").split(",") if i]
        conn = get_conn()
        states = db.ai_task_states(conn, ids)
        posts = db.get_posts(conn, ids)
        channels_map = db.get_channels_map(conn, ids)
        assets = {}
        for asset_id in ids:
            asset = db.get_asset(conn, asset_id)
            if asset is None:
                continue
            tags = db.list_tags_for_asset(conn, asset_id)
            assets[asset_id] = {
                "status": asset["status"],
                "content_rating": asset["content_rating"],
                "content_rating_confirmed": bool(asset["content_rating_confirmed"]),
                "nsfw_auto_rating": asset["nsfw_auto_rating"],
                "nsfw_auto_confidence": asset["nsfw_auto_confidence"],
                "has_description": asset["content_description"] is not None,
                "tags": tags,
                "tasks": states.get(asset_id, {}),
                "no_plan": not channels_map.get(asset_id),
                "broken": bool(asset["is_broken"]),
                "wm": {"text": asset["wm_text"], "label": watermark.describe_positions(asset["wm_position"])}
                if asset.get("wm_path") else None,
                "posts": {
                    ch: {k: p[k] for k in ("status", "url", "error", "posted_at", "source")}
                    for ch, p in posts.get(asset_id, {}).items()
                },
                "rev": f"{asset['updated_at']}|{len(tags)}|{asset.get('wm_path') or ''}|"
                + ",".join(f"{c}:{p['status']}" for c, p in sorted(posts.get(asset_id, {}).items())),
            }
        status = worker_module.get_status(conn, config.timezone)
        conn.close()
        return jsonify({"status": status, "assets": assets})

    @app.route("/assets/<asset_id>/media")
    def asset_media(asset_id):
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        conn.close()
        if asset is None:
            abort(404)
        if request.args.get("variant") == "wm" and asset.get("wm_path") and Path(asset["wm_path"]).exists():
            return send_file(asset["wm_path"])  # 透かし入り(確認用)
        if not Path(asset["file_path"]).exists():
            # ストレージが外れている・ファイルが無いとき。画像は「見つかりません」の絵を返し、動画は404にする
            if asset["kind"] == "image":
                return app.response_class(
                    (Path(app.static_folder) / "missing.svg").read_bytes(),
                    mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store", "X-Reelmilly-Missing": "1"},
                )
            abort(404)
        return send_file(asset["file_path"])

    @app.route("/assets/<asset_id>/download")
    def asset_download(asset_id):
        """元のファイル名で保存できるようにダウンロードする。variant=wmで透かし入り。"""
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        conn.close()
        if asset is None:
            abort(404)
        original = asset.get("original_name") or Path(asset["file_path"]).name
        if request.args.get("variant") == "wm" and asset.get("wm_path") and Path(asset["wm_path"]).exists():
            path = Path(asset["wm_path"])
            stem = Path(original).stem
            name = f"{stem}_watermarked{path.suffix}"
        else:
            path, name = Path(asset["file_path"]), original
        if not path.exists():
            abort(404)
        return send_file(path, as_attachment=True, download_name=name)

    # --- 透かし(ウォーターマーク) ---

    @app.route("/api/watermark", methods=["POST"])
    def api_watermark():
        """透かしの挿入を非同期ジョブとして積む。文字・位置・濃さ・大きさを実行前に指定する。"""
        payload = request.get_json(silent=True) or {}
        ids = payload.get("asset_ids") or []
        if not ids:
            return jsonify({"error": "asset_ids is required"}), 400
        try:
            params = watermark.clean_params(
                payload.get("text"), payload.get("positions") or payload.get("position"),
                payload.get("opacity", watermark.DEFAULTS["opacity"]), payload.get("size", watermark.DEFAULTS["size"]),
            )
        except watermark.WatermarkError as exc:
            return jsonify({"error": str(exc)}), 400
        conn = get_conn()
        queued = skipped_video = 0
        for asset_id in ids:
            asset = db.get_asset(conn, asset_id)
            if asset is None or asset.get("deleted_at"):
                continue
            if asset["kind"] != "image":
                skipped_video += 1
                continue
            if db.enqueue_ai_task(conn, asset_id, "watermark", params):
                queued += 1
        status = worker_module.get_status(conn, config.timezone)
        conn.close()
        return jsonify({"queued": queued, "skipped_video": skipped_video, "status": status})

    @app.route("/api/watermark/clear", methods=["POST"])
    def api_watermark_clear():
        """透かしを外す(透かし入りファイルを削除し、元のファイルに戻す)。"""
        ids = (request.get_json(silent=True) or {}).get("asset_ids") or []
        if not ids:
            return jsonify({"error": "asset_ids is required"}), 400
        conn = get_conn()
        cleared = 0
        for asset_id in ids:
            asset = db.get_asset(conn, asset_id)
            if asset and asset.get("wm_path"):
                Path(asset["wm_path"]).unlink(missing_ok=True)
                db.update_asset(conn, asset_id, wm_path=None, wm_text=None, wm_position=None, updated_at=_now())
                cleared += 1
        conn.close()
        return jsonify({"cleared": cleared})

    @app.route("/assets/<asset_id>/watermark-preview")
    def watermark_preview(asset_id):
        """設定の確認用プレビュー(保存しない)。実際の薄さのまま縮小して返す。"""
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        conn.close()
        if asset is None or asset["kind"] != "image":
            abort(404)
        try:
            params = watermark.clean_params(
                request.args.get("text"), request.args.getlist("position") or request.args.get("positions"),
                request.args.get("opacity", watermark.DEFAULTS["opacity"]), request.args.get("size", watermark.DEFAULTS["size"]),
            )
            data = watermark.preview_jpeg(
                Path(asset["file_path"]), params["text"], params["positions"], params["opacity"], params["size"]
            )
        except watermark.WatermarkError as exc:
            return str(exc), 400
        return app.response_class(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.route("/assets/<asset_id>/confirm", methods=["POST"])
    def confirm_rating(asset_id):
        conn = get_conn()
        content_rating = request.form.get("content_rating")
        if content_rating not in CONTENT_RATINGS:
            conn.close()
            abort(400)
        asset = db.get_asset(conn, asset_id)
        updates = {
            "content_rating": content_rating,
            "content_rating_confirmed": 1,
            "updated_at": _now(),
        }
        if asset is not None and asset["status"] == "pending_approval":
            updates["status"] = "ready"
        db.update_asset(conn, asset_id, **updates)
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/api/assets/<asset_id>/posts/<channel>/manual", methods=["POST"])
    def api_manual_post(asset_id, channel):
        """手動で投稿したことを記録する(投稿済み・日時・URL)。アプリの自動投稿の二重投稿を防げる。"""
        if channel not in POST_CHANNELS:
            return jsonify({"error": "不明な投稿先です"}), 400
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip() or None
        if url and not re.match(r"^https?://", url):
            return jsonify({"error": "URLは http:// または https:// で始まる形で入力してください"}), 400
        posted_at = None
        if payload.get("posted_at"):
            try:
                dt = datetime.fromisoformat(str(payload["posted_at"]).replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"error": "投稿日時の形式が正しくありません"}), 400
            if dt.tzinfo is None:
                from zoneinfo import ZoneInfo

                dt = dt.replace(tzinfo=ZoneInfo(config.timezone))
            posted_at = dt.astimezone(timezone.utc).isoformat()
        conn = get_conn()
        if db.get_asset(conn, asset_id) is None:
            conn.close()
            return jsonify({"error": "作品が見つかりません"}), 404
        db.set_post(conn, asset_id, channel, "posted", url=url, posted_at=posted_at, source="manual")
        conn.close()
        return jsonify({"recorded": True, "channel": channel})

    @app.route("/api/assets/<asset_id>/posts/<channel>/clear", methods=["POST"])
    def api_clear_post(asset_id, channel):
        """投稿の記録を取り消す(未投稿に戻す)。手動で記録した間違いの訂正や、再投稿の対象に戻すときに使う。"""
        if channel not in POST_CHANNELS:
            return jsonify({"error": "不明な投稿先です"}), 400
        conn = get_conn()
        had = channel in db.get_posts(conn, [asset_id]).get(asset_id, {})
        db.delete_post(conn, asset_id, channel)
        conn.close()
        return jsonify({"cleared": had})

    @app.route("/assets/<asset_id>/posts/<channel>/retry", methods=["POST"])
    def retry_post(asset_id, channel):
        """失敗した投稿を、次回の投稿対象に戻す(投稿そのものは次回のジョブが行う)。"""
        conn = get_conn()
        post = db.get_posts(conn, [asset_id]).get(asset_id, {}).get(channel)
        if post is None or post["status"] != "failed":
            conn.close()
            abort(400)
        db.delete_post(conn, asset_id, channel)
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/tags", methods=["POST"])
    def add_tag(asset_id):
        conn = get_conn()
        tag_name = (request.form.get("tag_name") or "").strip()
        if tag_name:
            db.add_tag_to_asset(conn, asset_id, tag_name)
        tags = db.list_tags_for_asset(conn, asset_id)
        conn.close()
        if _is_xhr():
            return jsonify({"tags": tags})
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/tags/<tag_name>/remove", methods=["POST"])
    def remove_tag(asset_id, tag_name):
        conn = get_conn()
        db.remove_tag_from_asset(conn, asset_id, tag_name)
        tags = db.list_tags_for_asset(conn, asset_id)
        conn.close()
        if _is_xhr():
            return jsonify({"tags": tags})
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/folders", methods=["POST"])
    def create_folder():
        conn = get_conn()
        name = (request.form.get("name") or "").strip()
        if name:
            db.create_folder(conn, name)
        conn.close()
        return redirect(request.referrer or url_for("index"))

    @app.route("/assets/<asset_id>/folders", methods=["POST"])
    def add_to_folder(asset_id):
        conn = get_conn()
        folder_id = request.form.get("folder_id", type=int)
        if folder_id:
            db.add_asset_to_folder(conn, asset_id, folder_id)
        folders = db.list_folders_for_asset(conn, asset_id)
        conn.close()
        if _is_xhr():
            return jsonify({"folders": folders})
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/folders/<int:folder_id>/remove", methods=["POST"])
    def remove_from_folder(asset_id, folder_id):
        conn = get_conn()
        db.remove_asset_from_folder(conn, asset_id, folder_id)
        folders = db.list_folders_for_asset(conn, asset_id)
        conn.close()
        if _is_xhr():
            return jsonify({"folders": folders})
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/caption", methods=["POST"])
    def update_caption(asset_id):
        conn = get_conn()
        fanvue_text = (request.form.get("fanvue_text") or "").strip() or None
        db.update_asset(conn, asset_id, fanvue_text=fanvue_text, updated_at=_now())
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/caption/adopt", methods=["POST"])
    def adopt_caption_draft(asset_id):
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        if asset is None:
            conn.close()
            abort(404)
        draft = asset.get("fanvue_caption_draft")
        if draft:
            db.update_asset(
                conn, asset_id, fanvue_text=draft, fanvue_caption_draft=None, updated_at=_now()
            )
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        conn = get_conn()
        if request.method == "POST":
            settings_module.update_settings(
                conn,
                description_system_prompt=request.form.get("description_system_prompt"),
                caption_system_prompt=request.form.get("caption_system_prompt"),
                generation_provider=request.form.get("generation_provider"),
                generation_model=request.form.get("generation_model"),
                caption_mode=request.form.get("caption_mode"),
            )
            settings_module.set_auto_ingest(conn, bool(request.form.get("auto_ingest")))
            settings_module.set_ai_schedules(
                conn,
                [
                    {"id": sid, "kind": kind, "enabled": enabled == "1", "time": time, "scope": scope, "days": days}
                    for sid, kind, enabled, time, scope, days in zip(
                        request.form.getlist("sched_id"),
                        request.form.getlist("sched_kind"),
                        request.form.getlist("sched_enabled"),
                        request.form.getlist("sched_time"),
                        request.form.getlist("sched_scope"),
                        request.form.getlist("sched_days"),
                    )
                ],
            )
            settings_module.set_tag_categories(
                conn,
                [
                    {"name": name, "options": options}
                    for name, options in zip(
                        request.form.getlist("tag_category_name"),
                        request.form.getlist("tag_category_options"),
                    )
                ],
            )
            try:
                settings_module.set_watermark_defaults(
                    conn,
                    request.form.get("wm_text"),
                    request.form.getlist("wm_position"),
                    request.form.get("wm_opacity"),
                    request.form.get("wm_size"),
                )
            except watermark.WatermarkError:
                pass  # 入力が不正なら、既存の既定値を変えない
            env_updates = {key: (request.form.get(key) or "").strip() for key in env_settings.CONNECTION_ENV_KEYS}
            env_settings.update_connection_values(config.env_path, env_updates)
            conn.close()
            return redirect(url_for("settings_page", saved="1"))
        current_settings = settings_module.get_all_settings(conn)
        connections = env_settings.read_connection_status(config.env_path)
        model_choices = settings_module.get_model_choices(conn)
        schedules = settings_module.get_ai_schedules(conn)
        tag_categories = settings_module.get_tag_categories(conn)
        conn.close()

        from posting.fanvue_oauth import FanvueTokenStore

        fanvue_connected = FanvueTokenStore(_fanvue_token_store_path(config)).load() is not None

        return render_template(
            "settings.html",
            settings=current_settings,
            model_choices=model_choices,
            schedules=schedules,
            schedule_labels=settings_module.AI_KIND_LABELS,
            tag_categories=tag_categories,
            connections=connections,
            saved=request.args.get("saved") == "1",
            fanvue_connected=fanvue_connected,
            fanvue_just_connected=request.args.get("fanvue_connected") == "1",
            fanvue_error=request.args.get("fanvue_error"),
        )

    @app.route("/favicon.ico")
    def favicon():
        return redirect(url_for("static", filename="favicon.ico"))

    # --- ストレージ(画像・動画の置き場所) ---

    @app.route("/api/storage/status")
    def api_storage_status():
        conn = get_conn()
        status = storage_status().as_dict()
        status["default_root"] = str(config.default_root or config.paths.root)
        if request.args.get("usage"):
            status["usage"] = storage.usage_summary(conn, config.paths.root) if status["available"] else None
        status["job"] = storage.current_job(conn)
        conn.close()
        return jsonify(status)

    @app.route("/api/storage/drives")
    def api_storage_drives():
        return jsonify({"drives": storage.list_drives()})

    @app.route("/api/storage/browse")
    def api_storage_browse():
        try:
            return jsonify(storage.browse(request.args.get("path", "")))
        except storage.StorageError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/storage/change", methods=["POST"])
    def api_storage_change():
        """ストレージの場所を変更する。時間がかかるので、別スレッドで始めて、進捗は/api/storage/statusで読む。"""
        payload = request.get_json(silent=True) or {}
        try:
            storage.start_change_in_background(
                config, config.paths.db_path, payload.get("path", ""), payload.get("mode", "move"),
                bool(payload.get("delete_source")),
            )
        except storage.StorageError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"started": True})

    # --- 重複の確認 ---

    @app.route("/api/duplicates")
    def api_duplicates():
        """確認が必要な重複: 保留中のアップロードと、登録済みの重複グループ。"""
        conn = get_conn()
        pending = duplicates.list_pending(config, conn)
        groups = duplicates.duplicate_groups(conn)
        conn.close()
        return jsonify({"pending": pending, "groups": groups})

    @app.route("/api/duplicates/pending/<token>/<path:name>")
    def api_duplicate_pending_file(token, name):
        path = duplicates.pending_file(config, token, name)
        if path is None:
            abort(404)
        return send_file(path)

    @app.route("/api/duplicates/resolve", methods=["POST"])
    def api_duplicates_resolve():
        """重複の扱いを、まとめて実行する。"""
        payload = request.get_json(silent=True) or {}
        conn = get_conn()
        out = {}
        if payload.get("uploads"):
            require_storage()
            out["uploads"] = duplicates.resolve_uploads(config, conn, payload["uploads"])
        if payload.get("groups"):
            out["groups"] = duplicates.resolve_groups(conn, payload["groups"])
        handled = sum(sum(v.values()) for k, v in out.items() if k in ("uploads", "groups") and isinstance(v, dict))
        if handled >= 2:
            ups, grs = out.get("uploads"), out.get("groups")
            parts = []
            if ups:
                parts.append(f"アップロード分: 取り込み{ups['imported']}件・破棄{ups['skipped']}件")
            if grs:
                parts.append(f"登録済み: ごみ箱へ{grs['trashed']}件・残す{grs['kept_all']}組")
            notifications.add(conn, "duplicates", "重複を処理しました", "、".join(parts), "success")
        out["remaining"] = {
            "pending": len(duplicates.list_pending(config, conn)),
            "groups": len(duplicates.duplicate_groups(conn)),
        }
        conn.close()
        return jsonify(out)

    # --- 通知(ベル・履歴) ---

    @app.route("/api/notifications")
    def api_notifications():
        conn = get_conn()
        limit = min(max(request.args.get("limit", 10, type=int), 1), 200)
        offset = max(request.args.get("offset", 0, type=int), 0)
        kind = request.args.get("kind") or None
        data = {
            "unread": notifications.unread_count(conn),
            "total": notifications.count(conn, kind),
            "items": notifications.list_notifications(conn, limit, offset, kind),
            "kinds": notifications.KINDS,
        }
        conn.close()
        return jsonify(data)

    @app.route("/api/notifications/read", methods=["POST"])
    def api_notifications_read():
        payload = request.get_json(silent=True) or {}
        conn = get_conn()
        marked = notifications.mark_read(conn, None if payload.get("all") else [int(i) for i in payload.get("ids", [])])
        data = {"marked": marked, "unread": notifications.unread_count(conn)}
        conn.close()
        return jsonify(data)

    @app.route("/help")
    def help_page():
        return render_template("help.html")

    @app.route("/settings/models/refresh", methods=["POST"])
    def refresh_models():
        """保存済みAPIキーで実際に接続し、利用可能なモデル一覧を取得・保存する。"""
        body = request.get_json(silent=True) or {}
        provider = body.get("provider")
        query = (body.get("query") or "").strip()
        env_key = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
        api_key = ""
        if provider == "local":
            pass  # Hugging Face Hubの公開APIを使うためキー不要
        elif env_key is None:
            return jsonify({"error": "このプロバイダーはモデル一覧の取得に対応していません"}), 400
        else:
            api_key = os.environ.get(env_key) or env_settings.read_env_value(config.env_path, env_key)
            if not api_key:
                return jsonify({"error": f"{env_key}が未設定です。先にAPIキーを保存してください"}), 400
        try:
            models = generation.list_available_models(provider, api_key, query)
        except generation.GenerationError as exc:
            return jsonify({"error": str(exc)}), 502
        if not models:
            return jsonify({"error": "利用可能なモデルが見つかりませんでした"}), 502
        if not query:  # 絞り込み検索の結果はキャッシュしない
            conn = get_conn()
            settings_module.set_model_cache(conn, provider, models)
            conn.close()
        return jsonify({"models": models})

    @app.route("/settings/fanvue/oauth/start")
    def fanvue_oauth_start():
        """Fanvue OAuth連携を開始する(ADR-0021)。認可ページへリダイレクトする。"""
        from posting import fanvue_oauth

        client_id = os.environ.get("FANVUE_OAUTH_CLIENT_ID")
        if not client_id:
            return redirect(url_for("settings_page", fanvue_error="FANVUE_OAUTH_CLIENT_IDが未設定です"))

        redirect_uri = os.environ.get("FANVUE_OAUTH_REDIRECT_URI") or url_for(
            "fanvue_oauth_callback", _external=True
        )
        pkce = fanvue_oauth.generate_pkce_pair()
        state = fanvue_oauth.generate_state()

        conn = get_conn()
        db.set_setting(conn, "_fanvue_oauth_pending_state", state)
        db.set_setting(conn, "_fanvue_oauth_pending_verifier", pkce.verifier)
        db.set_setting(conn, "_fanvue_oauth_pending_redirect_uri", redirect_uri)
        conn.close()

        authorization_url = fanvue_oauth.build_authorization_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=pkce.challenge,
        )
        return redirect(authorization_url)

    @app.route("/settings/fanvue/oauth/callback")
    def fanvue_oauth_callback():
        """Fanvueからの認可コードを受け取り、アクセストークンと交換する(ADR-0021)。"""
        from posting import fanvue_oauth

        oauth_error = request.args.get("error")
        if oauth_error:
            return redirect(url_for("settings_page", fanvue_error=oauth_error))

        code = request.args.get("code")
        state = request.args.get("state")

        conn = get_conn()
        pending_state = db.get_setting(conn, "_fanvue_oauth_pending_state")
        pending_verifier = db.get_setting(conn, "_fanvue_oauth_pending_verifier")
        pending_redirect_uri = db.get_setting(conn, "_fanvue_oauth_pending_redirect_uri")
        db.delete_setting(conn, "_fanvue_oauth_pending_state")
        db.delete_setting(conn, "_fanvue_oauth_pending_verifier")
        db.delete_setting(conn, "_fanvue_oauth_pending_redirect_uri")
        conn.close()

        if not code or not state or not pending_state or state != pending_state:
            return redirect(url_for("settings_page", fanvue_error="連携状態が確認できませんでした。もう一度お試しください"))

        try:
            tokens = fanvue_oauth.exchange_code_for_tokens(
                client_id=os.environ.get("FANVUE_OAUTH_CLIENT_ID", ""),
                client_secret=os.environ.get("FANVUE_OAUTH_CLIENT_SECRET", ""),
                redirect_uri=pending_redirect_uri,
                code=code,
                code_verifier=pending_verifier,
            )
        except fanvue_oauth.FanvueOAuthError as exc:
            return redirect(url_for("settings_page", fanvue_error=str(exc)))

        fanvue_oauth.FanvueTokenStore(_fanvue_token_store_path(config)).save(tokens)
        return redirect(url_for("settings_page", fanvue_connected="1"))

    @app.route("/settings/fanvue/disconnect", methods=["POST"])
    def fanvue_oauth_disconnect():
        from posting.fanvue_oauth import FanvueTokenStore

        FanvueTokenStore(_fanvue_token_store_path(config)).clear()
        return redirect(url_for("settings_page"))

    @app.route("/assets/bulk/tag", methods=["POST"])
    def bulk_add_tag():
        payload = request.get_json(silent=True) or {}
        asset_ids = payload.get("asset_ids") or []
        tag_name = (payload.get("tag_name") or "").strip()
        if not asset_ids or not tag_name:
            return jsonify({"error": "asset_ids and tag_name are required"}), 400

        conn = get_conn()
        for asset_id in asset_ids:
            db.add_tag_to_asset(conn, asset_id, tag_name)
        conn.close()
        return jsonify({"updated": len(asset_ids), "tag_name": tag_name})

    @app.route("/assets/bulk/folder", methods=["POST"])
    def bulk_add_to_folder():
        payload = request.get_json(silent=True) or {}
        asset_ids = payload.get("asset_ids") or []
        folder_id = payload.get("folder_id")
        if not asset_ids or not folder_id:
            return jsonify({"error": "asset_ids and folder_id are required"}), 400

        conn = get_conn()
        for asset_id in asset_ids:
            db.add_asset_to_folder(conn, asset_id, folder_id)
        conn.close()
        return jsonify({"updated": len(asset_ids), "folder_id": folder_id})

    @app.route("/assets/bulk/confirm", methods=["POST"])
    def bulk_confirm_rating():
        payload = request.get_json(silent=True) or {}
        asset_ids = payload.get("asset_ids") or []
        content_rating = payload.get("content_rating")
        if not asset_ids or content_rating not in CONTENT_RATINGS:
            return jsonify({"error": "asset_ids and a valid content_rating are required"}), 400

        conn = get_conn()
        now = _now()
        for asset_id in asset_ids:
            asset = db.get_asset(conn, asset_id)
            updates = {
                "content_rating": content_rating,
                "content_rating_confirmed": 1,
                "updated_at": now,
            }
            if asset is not None and asset["status"] == "pending_approval":
                updates["status"] = "ready"
            db.update_asset(conn, asset_id, **updates)
        conn.close()
        return jsonify({"updated": len(asset_ids), "content_rating": content_rating})

    return app
