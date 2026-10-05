"""本体Web UI。ADR-0010/0012に基づき、ローカルWebアプリとして動作する。

一覧・フォルダ・タグ管理・NSFW仕分け結果の確認と`content_rating`確定操作を提供する。
全文検索は対象外（ADR-0010）。
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from core import db, env_settings, generation
from core import worker as worker_module
from core.channels import POST_CHANNELS, POST_STATUS_LABELS
from core import settings as settings_module
from core.config import Config
from core.ingest import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ingest_inbox
from core.media import get_media_properties
from core.nsfw import try_create_classifier

CONTENT_RATINGS = ("sfw", "suggestive", "explicit")
ALLOWED_UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_xhr() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _fanvue_token_store_path(config: Config) -> Path:
    return config.paths.state_dir / "fanvue_oauth_tokens.json"


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
        return conn

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
        status_filter = request.args.get("status") or None
        rating_filter = request.args.get("content_rating") or None
        tag_filter = request.args.get("tag") or None
        folder_id = request.args.get("folder_id", type=int)
        confirmed_param = request.args.get("confirmed") or None
        confirmed_filter = {"0": False, "1": True}.get(confirmed_param)
        # 投稿状態の絞り込み("fanvue:posted"のように「投稿先:状態」)
        post_param = request.args.get("post") or None
        post_channel = post_status = None
        if post_param and ":" in post_param:
            ch, st = post_param.split(":", 1)
            if ch in POST_CHANNELS and st in POST_STATUS_LABELS:
                post_channel, post_status = ch, st

        assets = db.list_assets(
            conn,
            status=status_filter,
            content_rating=rating_filter,
            tag=tag_filter,
            folder_id=folder_id,
            confirmed=confirmed_filter,
            post_channel=post_channel,
            post_status=post_status,
            limit=200,
        )
        posts = db.get_posts(conn, [a["id"] for a in assets])
        folders = db.list_folders(conn)
        tags = db.list_all_tags(conn)
        conn.close()
        return render_template(
            "index.html",
            assets=assets,
            folders=folders,
            tags=tags,
            status_filter=status_filter,
            rating_filter=rating_filter,
            tag_filter=tag_filter,
            folder_id=folder_id,
            confirmed_param=confirmed_param,
            content_ratings=CONTENT_RATINGS,
            posts=posts,
            post_param=post_param if post_channel else None,
            post_channels=POST_CHANNELS,
            post_status_labels=POST_STATUS_LABELS,
        )

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
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "no files provided"}), 400

        config.paths.inbox.mkdir(parents=True, exist_ok=True)
        saved_names = []
        rejected = []
        for file in files:
            if not file.filename:
                continue
            filename = secure_filename(file.filename)
            ext = Path(filename).suffix.lower()
            if not filename or ext not in ALLOWED_UPLOAD_EXTENSIONS:
                rejected.append(file.filename)
                continue
            dest = _unique_inbox_path(config.paths.inbox, filename)
            file.save(dest)
            saved_names.append(dest.name)

        # 分析(NSFW仕分け・内容説明)はVLM推論に数分かかるため、アップロードでは行わず
        # status="analyzing"で登録して即応答する。分析は別プロセスのワーカーが行う
        conn = get_conn()
        results = ingest_inbox(config, conn, defer_analysis=True)
        conn.close()

        return jsonify(
            {
                "uploaded": len(saved_names),
                "rejected": rejected,
                "ingested": len(results),
                "asset_ids": [r.asset_id for r in results],
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
        known = [a for a in asset_ids if db.get_asset(conn, a) is not None]
        queued = worker_module.enqueue_for_assets(conn, known, kinds)
        status = worker_module.get_status(conn)
        conn.close()
        return jsonify({"queued": queued, "skipped": len(known) * len(kinds) - queued, "status": status})

    @app.route("/api/ai-live")
    def ai_live():
        """一覧/詳細画面が定期的に取得する、AI処理の全体状況と表示中アセットの要約。"""
        ids = [i for i in (request.args.get("ids") or "").split(",") if i]
        conn = get_conn()
        states = db.ai_task_states(conn, ids)
        posts = db.get_posts(conn, ids)
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
                "posts": {
                    ch: {k: p[k] for k in ("status", "url", "error", "posted_at")}
                    for ch, p in posts.get(asset_id, {}).items()
                },
                "rev": f"{asset['updated_at']}|{len(tags)}|"
                + ",".join(f"{c}:{p['status']}" for c, p in sorted(posts.get(asset_id, {}).items())),
            }
        status = worker_module.get_status(conn)
        conn.close()
        return jsonify({"status": status, "assets": assets})

    @app.route("/assets/<asset_id>/media")
    def asset_media(asset_id):
        conn = get_conn()
        asset = db.get_asset(conn, asset_id)
        conn.close()
        if asset is None:
            abort(404)
        return send_file(asset["file_path"])

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
            for kind in settings_module.AI_SCHEDULE_KINDS:
                settings_module.set_ai_schedule(
                    conn,
                    kind,
                    enabled=bool(request.form.get(f"schedule_{kind}_enabled")),
                    time=request.form.get(f"schedule_{kind}_time", ""),
                    scope=request.form.get(f"schedule_{kind}_scope", "all"),
                    days=request.form.get(f"schedule_{kind}_days"),
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
            env_updates = {key: (request.form.get(key) or "").strip() for key in env_settings.CONNECTION_ENV_KEYS}
            env_settings.update_connection_values(config.env_path, env_updates)
            conn.close()
            return redirect(url_for("settings_page", saved="1"))
        current_settings = settings_module.get_all_settings(conn)
        connections = env_settings.read_connection_status(config.env_path)
        model_choices = settings_module.get_model_choices(conn)
        schedules = {k: settings_module.get_ai_schedule(conn, k) for k in settings_module.AI_SCHEDULE_KINDS}
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
