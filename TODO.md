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
- [ ] Fanvue API疎通確認（`GET /users/me`を実トークンで1回叩く）

## CREAM由来の検討事項

- [ ] `assets`テーブル（SQLite）に`content_rating`、`config.yaml`に`platform_content_rules`を追加（[ADR-0006](docs/adr/0006-platform-content-rules-from-cream.md)、Phase 1着手時）
- [ ] drop/x_teaser実行前にプラットフォーム別コンテンツルールを検証するロジックを追加（Phase 4着手時）
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
- [ ] drop/x_teaser実行前に`content_rating_confirmed == true`および`platform_auto_post_ratings`を検証するフィルタを追加（Phase 4着手時、`posting`モジュール未着手のため）

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
