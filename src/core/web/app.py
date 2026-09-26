"""本体Web UI。ADR-0010/0012に基づき、ローカルWebアプリとして動作する。

一覧・フォルダ・タグ管理・NSFW仕分け結果の確認と`content_rating`確定操作を提供する。
全文検索は対象外（ADR-0010）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from core import db
from core.config import Config

CONTENT_RATINGS = ("sfw", "suggestive", "explicit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        return render_template(
            "asset_detail.html",
            asset=asset,
            channels=channels,
            tags=tags,
            asset_folders=asset_folders,
            all_folders=all_folders,
            content_ratings=CONTENT_RATINGS,
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
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/tags/<tag_name>/remove", methods=["POST"])
    def remove_tag(asset_id, tag_name):
        conn = get_conn()
        db.remove_tag_from_asset(conn, asset_id, tag_name)
        conn.close()
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
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.route("/assets/<asset_id>/folders/<int:folder_id>/remove", methods=["POST"])
    def remove_from_folder(asset_id, folder_id):
        conn = get_conn()
        db.remove_asset_from_folder(conn, asset_id, folder_id)
        conn.close()
        return redirect(url_for("asset_detail", asset_id=asset_id))

    return app
