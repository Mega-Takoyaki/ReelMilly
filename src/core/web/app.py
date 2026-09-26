"""本体Web UI。ADR-0010/0012に基づき、ローカルWebアプリとして動作する。

一覧・フォルダ・タグ管理・NSFW仕分け結果の確認と`content_rating`確定操作を提供する。
全文検索は対象外（ADR-0010）。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from core import db, env_settings, generation
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


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["REELMILLY_CONFIG"] = config

    def get_conn():
        conn = db.get_connection(config.paths.db_path)
        db.init_db(conn)
        return conn

    @app.route("/")
    def index():
        conn = get_conn()
        status_filter = request.args.get("status") or None
        rating_filter = request.args.get("content_rating") or None
        tag_filter = request.args.get("tag") or None
        folder_id = request.args.get("folder_id", type=int)

        assets = db.list_assets(
            conn,
            status=status_filter,
            content_rating=rating_filter,
            tag=tag_filter,
            folder_id=folder_id,
            limit=200,
        )
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
            content_ratings=CONTENT_RATINGS,
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

        conn = get_conn()
        nsfw_classifier = try_create_classifier(config.nsfw)
        generator = generation.try_create_generator(conn)
        results = ingest_inbox(config, conn, nsfw_classifier=nsfw_classifier, generator=generator)
        conn.close()

        return jsonify(
            {
                "uploaded": len(saved_names),
                "rejected": rejected,
                "ingested": len(results),
                "asset_ids": [r.asset_id for r in results],
            }
        )

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
        db.update_asset(
            conn,
            asset_id,
            content_rating=content_rating,
            content_rating_confirmed=1,
            updated_at=_now(),
        )
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
            env_updates = {key: (request.form.get(key) or "").strip() for key in env_settings.CONNECTION_ENV_KEYS}
            env_settings.update_connection_values(config.env_path, env_updates)
        current_settings = settings_module.get_all_settings(conn)
        connections = env_settings.read_connection_status(config.env_path)
        conn.close()
        return render_template("settings.html", settings=current_settings, connections=connections)

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
            db.update_asset(
                conn,
                asset_id,
                content_rating=content_rating,
                content_rating_confirmed=1,
                updated_at=now,
            )
        conn.close()
        return jsonify({"updated": len(asset_ids), "content_rating": content_rating})

    return app
