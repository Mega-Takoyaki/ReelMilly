# TODO

大きめのタスク（Phase単位の実装、仕様確定が必要なもの）は GitHub Issues で管理する。ここには小粒タスク・決め忘れ防止用のメモを置く。

## 最優先（今すぐ着手）

- [ ] X Developer Portalで開発者アプリを申請する（[ADR-0014](docs/adr/0014-x-official-api-application-first.md)）。審査所要期間・要件を実地で確認し、本ファイルに追記する
- [ ] 申請結果が出るまで、Phase 0/1/1.5（本体側、SNS投稿と無関係な部分）を先行して進める

## 未確定事項（着手前に確定が必要）

- [ ] デプロイ環境の決定（本体: ローカルWebアプリ vs AWS常時稼働）→ `docs/adr/0005-deployment-environment.md`。SNS投稿モジュールはX API承認結果次第で決まる
- [ ] X開発者アプリが承認されない場合のフォールバック計画（Playwright実装、[ADR-0002](docs/adr/0002-x-posting-via-playwright.md)参照）を発動するかどうかの判断基準・タイムリミットを決める
- [ ] Fanvue handle と実際の投稿URL例1本の取得（`FANVUE_POST_URL_TEMPLATE`確定用）
- [ ] Telegram BotFatherでのBot作成（`ReelmillyBot`または空き名称）とtoken取得
- [ ] Fanvue API疎通確認（`reelmilly doctor`にFanvue疎通チェックを実装済み。`.env`に実トークンを設定して1回実行する）

## CREAM由来の検討事項

- [x] ~~`assets`テーブル（SQLite）に`content_rating`、`config.yaml`に`platform_content_rules`を追加~~ → 実装済み
- [x] ~~プラットフォーム別コンテンツルールを検証するロジックを追加~~ → `src/core/policy.py`(`can_auto_post`)として実装済み。実際のdrop/x_teaserジョブへの組み込みは`posting`モジュール実装時（Phase 4）
- [ ] **運用ルール（要順守）**: 投稿前のコンプライアンス確認（AI生成であることの明示・ペルソナが18歳未満に見えないことの確認）は、システム実装を見送り運用者が毎回目視で確認する。この運用ルールは省略しないこと
- [ ] 将来的にコンプライアンス確認の記録用ゲート（判定はしない、確認済みフラグの記録のみ）をシステム化するか、運用実績を見て再検討する

## アセット管理の中核化・NSFW自動仕分け（ADR-0007/0008/0009）

- [x] ~~NSFW自動仕分けの実装方式の選定~~ → 決定: Marqo/nsfw-image-detection-384（[ADR-0009](docs/adr/0009-nsfw-classifier-marqo.md)）
- [x] ~~`assets`テーブル（SQLite）に`content_rating_confirmed`・`nsfw_auto_rating`・`nsfw_auto_confidence`を追加~~ → `src/core/schema.sql`に実装済み
- [x] ~~`timm` / `torch` / `pillow` / `opencv-python` を依存関係に追加~~ → `pyproject.toml`の`nsfw`/`dev` extraに実装済み
- [ ] 動画のフレームサンプリング間隔（初期値2秒）・判定閾値（初期値0.5）を実機でReelMilly実データを使い検証・調整する（`src/core/nsfw.py`のロジック自体はテスト済み、精度検証は未実施）
- [ ] オフライン運用が必要な場合、Marqoモデルの事前キャッシュ手順を用意
- [x] ~~`config.yaml`に`platform_auto_post_ratings`を追加~~ → 実装済み
- [x] ~~自動仕分け結果の確認・補正操作を本体UI（Phase 1.5）に実装する~~ → `/assets/<id>/confirm`として実装済み。Telegram簡易版は未着手
- [x] ~~drop/x_teaser実行前に`content_rating_confirmed == true`および`platform_auto_post_ratings`を検証するフィルタを追加~~ → 判定ロジックは`src/core/policy.py`として実装済み。ジョブへの組み込みは`posting`モジュール実装時（Phase 4）

## Fanvue投稿機能（Phase 2〜4、ADR-0003）

- [x] ~~Fanvueクライアント実装（multipart upload、post作成）~~ → `src/posting/fanvue.py`(`FanvueClient`)として実装済み。外部HTTPはモックでテスト済み(10件)
- [x] ~~`reelmilly doctor`にFanvue疎通確認を追加~~ → `.env`の`FANVUE_API_TOKEN`が設定されていれば`GET /users/me`を実行
- [ ] レスポンス形式（`uploadId`/`mediaUuid`/`status`等のフィールド名）は一次情報を検証しておらず、CLAUDE_HANDOFF.md 7章からの推測実装。実際のFanvue APIで疎通確認する際に調整が必要になる可能性が高い
- [x] ~~「1アセットをFanvueへ投稿する」一連の処理をまとめるジョブ関数を実装する~~ → `src/posting/jobs.py`(`run_fanvue_drop`)として実装済み。対象選定→ポリシー判定→upload→ready待ち→post作成→DB更新（成功時`status=posted`、失敗時`status=failed_fanvue`）を一通り実装、8件のテストで検証
- [x] ~~`reelmilly run drop`コマンドと同日二重実行防止を実装~~ → `job_runs`テーブル（Asia/Tokyo基準の日付）で管理
- [ ] `build_post_url`の`FANVUE_POST_URL_TEMPLATE`は実際の投稿URL1本で検証する（上記の未確定事項参照）
- [ ] `wait_for_media_ready`のタイムアウト（現状固定90秒）を`config.yaml`で設定可能にするか検討する
- [ ] X投稿機能の実装後、`drop`ジョブにX紹介投稿のステップを追加する（現状はFanvue投稿のみで完結。`x_ok`フラグは既存スキーマにあるが未使用）
- [ ] `x_teaser`・`x_engagement`ジョブは`posting`モジュールにX投稿機能を追加してから実装する

## 本体・SNS投稿モジュールの分離（ADR-0012/0013）

- [x] ~~`src/core`（画像管理本体）のパッケージ構成を確定する~~ → 実装済み。`src/posting`・`src/telegram`はPhase 2以降で着手
- [x] ~~`core`が提供するデータアクセス層のインターフェースを設計する~~ → `src/core/db.py`として実装済み
- [ ] 本体UIとTelegramの双方から同じアセットを操作した場合の競合・整合性の扱いを検討する（`telegram`モジュール着手時）

## 画像・動画管理の自作・SQLite移行（ADR-0010/0011）

- [x] ~~SQLiteスキーマの実装~~ → `src/core/schema.sql`・`src/core/db.py`として実装済み
- [x] ~~`meta.yaml`ベースのingest処理をSQLite書き込みに置き換える~~ → `src/core/ingest.py`は最初からSQLite書き込みで実装
- [ ] マイグレーションツール（`alembic`等）の要否を検討する（現状は`CREATE TABLE IF NOT EXISTS`のみ。スキーマ変更が増えたら再検討）
- [x] ~~Phase 1.5: アセット管理UIの技術スタックを選定する~~ → Flask + Jinja2で実装済み（`src/core/web/`）
- [ ] SQLiteのインデックス設計（`status`/`content_rating`/`created_at`）をPhase 4のジョブ選定ロジックとあわせて検証する

## 本体UIの高度化

- [x] ~~一覧画面にドラッグ&ドロップでの複数画像・動画アップロード機能を追加する~~ → `POST /assets/upload`として実装済み（既存のingestロジックを再利用）
- [x] ~~詳細画面でプロパティを表示しながら、タグ・フォルダ編集をよりシームレスに行えるレイアウトに改善する~~ → `src/core/media.py`でプロパティ取得、タグ/フォルダ編集はAjax化して実装済み
- [x] ~~一覧画面に複数選択機能を追加し、一括タグ付与・一括承認等の操作を可能にする~~ → `POST /assets/bulk/{tag,folder,confirm}`として実装済み
- [ ] フロントエンド技術（現状: Flask + Jinja2 + 素のJS/CSS）を見直すか判断する。機能追加でJSがある程度の量になってきたため、htmx/Alpine.js等の軽量ライブラリ導入や、コンポーネント分割の要否を今後検討する

## 確定済みだが実装時に再確認するデフォルト値

- [ ] drop前承認の要否（デフォルト: 必須）
- [ ] X紹介文へのサムネ添付要否（デフォルト: なし）
- [ ] Fanvue投稿の価格デフォルト値（デフォルト: 499セント）
- [ ] Fanvue audience デフォルト（デフォルト: subscribers）

## 開発環境・品質まわり

- [ ] 静的解析ツール（ruff、mypy）の導入 ← 当初は導入見送り。忘れず後日対応する
- [ ] 静的解析導入後、`.github/workflows/ci.yml` にlintジョブを追加する
- [x] ~~`pyproject.toml` / `requirements.txt` の整備~~ → `pyproject.toml`を採用し実装済み

## ドキュメント

- [ ] `docs/adr/0005-deployment-environment.md` をデプロイ環境確定後に更新する
- [x] ~~Phase 0完了後、README.mdにセットアップ手順を追記する~~ → 実装済み（ローカルセットアップ手順一式）

## 実機での動作確認（要ユーザー対応）

- [ ] 稼働予定のローカルPCで、README.mdのセットアップ手順どおりにセットアップできるか確認する（このセッションの開発環境とは別PCのため未検証）
- [ ] 実際のGrok Imagine出力（画像・動画）でingest・NSFW自動仕分け・本体UIでの承認操作を一通り試す
- [ ] NSFW自動仕分けを有効化する場合、`pip install -e ".[nsfw]"`でのtorch/timmインストールが実機で問題なく完了するか確認する
