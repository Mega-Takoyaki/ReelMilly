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
- [x] ~~NSFW自動仕分けが実機で動作するか確認する~~ → torch(CPU版)+timmをインストールし、Marqoモデルのロード・画像分類・`reelmilly ingest`経由での統合動作をこのセッションの開発環境で確認済み（image: nsfw判定、confidence記録まで正常動作）。この過程で発見した「破損・非画像ファイルの分類失敗でingest全体が止まる」問題は`ingest_inbox`で例外を捕捉し`nsfw_classify_failed`イベントを記録する形に修正済み
- [ ] 動画のフレームサンプリング間隔（初期値2秒）・判定閾値（初期値0.5）を実際のGrok Imagine出力データを使い検証・調整する（ロジック自体はテスト済み、判定精度の検証は未実施。ReelMillyコンテンツに対する誤判定率を見て閾値を調整する必要がある）
- [ ] オフライン運用が必要な場合、Marqoモデルの事前キャッシュ手順を用意
- [x] ~~`config.yaml`に`platform_auto_post_ratings`を追加~~ → 実装済み
- [x] ~~自動仕分け結果の確認・補正操作を本体UI（Phase 1.5）に実装する~~ → `/assets/<id>/confirm`として実装済み。Telegram簡易版は未着手
- [x] ~~drop/x_teaser実行前に`content_rating_confirmed == true`および`platform_auto_post_ratings`を検証するフィルタを追加~~ → 判定ロジックは`src/core/policy.py`として実装済み。ジョブへの組み込みは`posting`モジュール実装時（Phase 4）

## 画像内容説明・Fanvue投稿文の自動生成（ADR-0015）

- [x] ~~生成AIプロバイダーの抽象化（Claude API既定、OpenAI API選択可）を実装~~ → `src/core/generation.py`(`ClaudeGenerator`/`OpenAiGenerator`/`try_create_generator`)として実装済み。ClaudeGeneratorはモックでテスト済み(6件)
- [ ] OpenAiGeneratorは実API疎通を検証していない。Fanvueクライアントと同様、実行して失敗する場合は本項目を参照して調整すること
- [ ] AWS Bedrock対応（ADR-0015で将来対応として保留）。AWSへのデプロイを行う場合に着手する
- [x] ~~`assets`テーブルに`content_description`・`fanvue_caption_draft`を追加~~ → `src/core/schema.sql`に実装済み
- [x] ~~システムプロンプト等を保存する`settings`テーブルと本体UIの設定画面を実装~~ → `src/core/settings.py`・`/settings`ルート・`settings.html`として実装済み
- [x] ~~READY昇格条件をNSFW自動仕分け・内容説明の両方の成功に変更~~ → `src/core/analysis.py`(`analyze_asset`)・`src/core/ingest.py`として実装済み。**重要な方針転換**: 以前は本体機能単体（NSFW/生成AI未設定）でもreadyになったが、現在は両方成功しないとreadyにならない（README参照）
- [x] ~~`reelmilly analyze`コマンドで`analyzing`状態のアセットを再試行~~ → 実装済み。`reelmilly watch`のループにも組み込み済み
- [ ] 動画の内容説明は代表フレーム1枚のみを見る簡易実装（ADR-0015）。精度は実データでの検証が必要
- [x] ~~投稿文の自動生成(`auto`/`draft`モード)を`run_fanvue_drop`に統合~~ → `posting/jobs.py`(`_resolve_caption`)として実装済み
- [x] ~~投稿オプション（枚数・種別・レーティング）をCLI(`--count`/`--kind`/`--rating`)とconfig.yamlのcadence(辞書形式)の両方に対応~~ → 実装済み。`run_fanvue_drop_batch`は候補が尽きる・スキップ・失敗のいずれかの時点で打ち切る（次候補へのスキップは行わない、既知の制約）
- [ ] 生成AIのAPI課金（Claude API/OpenAI APIとも従量課金）の実運用コストを、実際の投稿頻度で見積もる

## 接続設定（Fanvue/Telegram/生成AI、ADR-0016）

- [x] ~~Fanvue APIトークン・ハンドル等を本体UIの設定画面から編集できるようにする~~ → `src/core/env_settings.py`（`.env`の読み書き）と`/settings`ページの「接続設定」セクションとして実装済み。秘密情報は`.env`のみに保持する方針は維持（DBには保存しない）
- [ ] 保存操作は`.env`への書き込みのみで、実際に接続できるかは検証しない。設定画面から`reelmilly doctor`相当の疎通確認を呼び出せるようにするか検討する
- [ ] X（旧Twitter）の接続設定は未実装（X投稿機能自体が申請待ちのため）。実装時に本ページへ追加する
- [ ] Telegram連携（Phase 5）は未実装のため、Botトークン等は保存のみ可能で実際には使われない

## Fanvue投稿機能（Phase 2〜4、ADR-0003）

- [x] ~~Fanvueクライアント実装（multipart upload、post作成）~~ → `src/posting/fanvue.py`(`FanvueClient`)として実装済み。外部HTTPはモックでテスト済み(10件)
- [x] ~~`reelmilly doctor`にFanvue疎通確認を追加~~ → `.env`の`FANVUE_API_TOKEN`が設定されていれば`GET /users/me`を実行
- [ ] レスポンス形式（`uploadId`/`mediaUuid`/`status`等のフィールド名）は一次情報を検証しておらず、CLAUDE_HANDOFF.md 7章からの推測実装。実際のFanvue APIで疎通確認する際に調整が必要になる可能性が高い
- [x] ~~「1アセットをFanvueへ投稿する」一連の処理をまとめるジョブ関数を実装する~~ → `src/posting/jobs.py`(`run_fanvue_drop`)として実装済み。対象選定→ポリシー判定→upload→ready待ち→post作成→DB更新（成功時`status=posted`、失敗時`status=failed_fanvue`）を一通り実装、8件のテストで検証
- [x] ~~`reelmilly run drop`コマンドと同日二重実行防止を実装~~ → `job_runs`テーブル（Asia/Tokyo基準の日付）で管理
- [x] ~~ingest→承認→run drop→タグ付与→二重実行防止→run-due連携の一連の流れを通しで動作確認する~~ → このセッションの開発環境で、実際のingest（実NSFWモデル使用）・実SQLite・実CLIコマンドを使い通しで確認済み（Fanvue API呼び出し部分のみモック。トークン未取得のため実API疎通はまだ未確認、上記参照）。単体（Fanvue投稿のみ）の一連のワークフローとしては動作するレベルに到達
- [ ] `build_post_url`の`FANVUE_POST_URL_TEMPLATE`は実際の投稿URL1本で検証する（上記の未確定事項参照）
- [ ] `wait_for_media_ready`のタイムアウト（現状固定90秒）を`config.yaml`で設定可能にするか検討する
- [ ] X投稿機能の実装後、`drop`ジョブにX紹介投稿のステップを追加する（現状はFanvue投稿のみで完結。`x_ok`フラグは既存スキーマにあるが未使用）
- [ ] `x_teaser`・`x_engagement`ジョブは`posting`モジュールにX投稿機能を追加してから実装する
- [x] ~~自動実行スケジューラ（`run-due`/`watch`）を実装~~ → `config.yaml`の`cadence`設定（`drop: "HH:MM"`）を見て時刻が来ていれば実行する`reelmilly run-due`と、それを一定間隔（既定60秒）で繰り返す常駐コマンド`reelmilly watch`を実装済み。X投稿ジョブ実装時に`cadence`の対応ジョブ名を追加する必要あり（現状`drop`のみ対応）

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
- [x] ~~設定画面（システムプロンプト・生成AIプロバイダー・キャプション生成モード）を追加~~ → `/settings`として実装済み（ADR-0015）
- [x] ~~UIのモダン化（デザイントークン刷新、レスポンシブ対応、ダーク/ライト自動切替、トースト通知・確認ダイアログ）~~ → `style.css`/`app.js`として実装済み。ビルドツール・新規フロントエンドライブラリは導入せず、素のCSS/JSのまま刷新。Playwrightでデスクトップ/モバイル幅・ダーク/ライト双方の見た目を目視確認済み（自動テストの対象外）
- [ ] 一覧画面のページネーション未実装（`limit=200`固定）。アセット数が増えてきたら対応を検討する
- [ ] フロントエンド技術（現状: Flask + Jinja2 + 素のJS/CSS）を見直すか判断する。htmx/Alpine.js等の軽量ライブラリ導入や、コンポーネント分割の要否は今後の規模次第で再検討する（現時点では素のJS/CSSのままでも保守可能と判断）

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
- [ ] 実際のANTHROPIC_API_KEY（またはOPENAI_API_KEY）を設定し、`reelmilly ingest`経由で画像内容説明が実際に取得できるか確認する（このセッションではモックでのみ検証、実API呼び出しは未実施）
- [ ] 実際のcontent_descriptionを使い、`run_fanvue_drop`での投稿文自動生成（auto/draft両モード）を実機で確認する
