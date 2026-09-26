# Reelmilly

画像・動画（Grok Imagine生成物）を管理する本体アプリケーションを優先実装し、その上にFanvueへの本編投稿とX（旧Twitter）への紹介投稿を半自動化する機能を論理プラグインとして付加する（[ADR-0007](docs/adr/0007-asset-management-as-core.md)、[ADR-0013](docs/adr/0013-sns-posting-as-logical-plugin.md)）。操作は本体UIを主とし、Telegram Botは通知と簡易操作（承認・スキップ）の補助チャネルとして使う（[ADR-0012](docs/adr/0012-primary-ui-with-telegram-as-secondary.md)）。

## ステータス

本体（画像・動画管理アプリ、Phase 0〜1.5相当）とFanvue投稿ジョブ（Phase 2・4のFanvue部分）を実装済み。実API疎通は未確認。X投稿（Phase 3）はX開発者アプリの申請待ちで未着手。進捗は [docs/roadmap.md](docs/roadmap.md) を参照。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md) | 要件・仕様の一次情報（開発引き継ぎ文書） |
| [docs/roadmap.md](docs/roadmap.md) | 実装フェーズ（Phase 0〜6）と進捗、受け入れ基準 |
| [docs/architecture.md](docs/architecture.md) | アーキテクチャ設計（arc42テーラリング版） |
| [docs/adr/](docs/adr) | アーキテクチャ決定記録（ADR、MADR形式） |
| [TODO.md](TODO.md) | 小粒タスクの一覧。大きめのタスクは GitHub Issues で管理 |
| [docs/templates/ai-project-governance-checklist.md](docs/templates/ai-project-governance-checklist.md) | AI開発プロジェクト立ち上げ時の汎用チェックリスト（他プロジェクトへの再利用向け） |

## 開発ルール（概要）

- **ADR**: MADR形式、`docs/adr/` に手動管理
- **アーキテクチャ文書**: arc42テーラリング版（`docs/architecture.md`）
- **タスク管理**: 大タスク＝GitHub Issues、小タスク＝`TODO.md`
- **コミットメッセージ**: [Conventional Commits](https://www.conventionalcommits.org/)
- **テスト**: pytestで主要ロジックを単体テスト、外部I/O（Playwright / Telegram / Fanvue API）はモック
- **CI**: GitHub Actionsで自動テスト（`.github/workflows/ci.yml`）。静的解析ツールは未導入（`TODO.md`参照）
- **秘密情報**: `.env` のみに置く。リポジトリにコミットしない（`.env.example`参照）

各決定の理由は対応するADRを参照。

## X投稿の実装方針

X投稿は公式API（申請ファースト）を採用する（[ADR-0014](docs/adr/0014-x-official-api-application-first.md)）。開発者アプリを先に申請し、承認され次第、公式API（`POST /2/tweets`等）で実装する。承認されない場合のみPlaywright非公式操作にフォールバックする。

## デプロイ環境

未確定。本体（画像管理アプリ）とSNS投稿モジュールは論理的に分離されており（[ADR-0013](docs/adr/0013-sns-posting-as-logical-plugin.md)）、デプロイ先も別々に検討できる。本体はローカルWebアプリが現時点の基本方針。SNS投稿モジュールはX公式APIが承認されれば本体と同じ環境で足り、Playwrightにフォールバックする場合のみGUI常時起動環境等の追加検討が必要になる。判断軸は [docs/adr/0005-deployment-environment.md](docs/adr/0005-deployment-environment.md) を参照。

## セットアップ（ローカルPC）

本体（画像・動画管理アプリ）はローカルPC上で動くWebアプリとして動作します。Windows/Mac/Linuxいずれでも同じ手順です（コマンドはPowerShell/bashどちらでも読み替え可能）。

### 1. 前提

- Python 3.11以上
- `git`

### 2. 取得とセットアップ

```bash
git clone https://github.com/Mega-Takoyaki/ReelMilly.git
cd ReelMilly
python -m venv .venv

# 有効化: Windows(PowerShell)は .venv\Scripts\Activate.ps1、Mac/Linuxは以下
source .venv/bin/activate

pip install -e ".[dev]"
```

NSFW自動仕分け機能（[ADR-0009](docs/adr/0009-nsfw-classifier-marqo.md)）を使う場合は、追加でtorch/timmをインストールします（数百MB〜のダウンロードが発生します）。

```bash
pip install -e ".[nsfw]"
```

これを入れなくても本体（一覧・フォルダ・タグ・承認UI）は動作します。ingest時にNSFW自動仕分けがスキップされる旨のメッセージが出るだけです。

### 3. 秘密情報の設定

```bash
cp .env.example .env
# 本体のみの動作にはFanvue/Telegramの値は不要。Fanvue疎通確認をしたい場合のみ
# FANVUE_API_TOKEN 等を設定する（下記4.参照）
```

### 4. 初期化と疎通確認

```bash
reelmilly init     # ディレクトリとSQLiteデータベースを作成
reelmilly doctor   # ディレクトリ・DB・(設定していれば)Fanvue APIの疎通を確認
```

`.env`に`FANVUE_API_TOKEN`を設定していれば、`doctor`が`GET /users/me`でFanvue APIの疎通も確認します（未設定ならスキップされ、失敗にはなりません）。Fanvue APIレスポンスの形式は一次情報での検証を行っていない実装のため、実行して失敗する場合は[TODO.md](TODO.md)を参照してください。

### 5. 画像・動画を取り込む

Grok Imagine等で生成した画像・動画を `data/library/inbox/` に置き、取り込みます。

```bash
reelmilly ingest
```

同名の`.yaml`（または`.json`）サイドカーファイルを置くと、`caption`/`x_caption`/`channels`/`tags`等を指定して取り込めます。例（`sample.jpg`に対する`sample.yaml`）:

```yaml
caption: "紹介文"
channels: [fanvue, x]
tags: [推し, 夏]
```

### 6. 本体UIを起動する

```bash
reelmilly web
```

起動後、ブラウザで `http://127.0.0.1:8420/` を開くと、一覧・フォルダ・タグ管理・投稿承認（`content_rating`確定）操作ができます。既定では他の端末からはアクセスできません（`config.yaml`の`web.host`が`127.0.0.1`固定のため）。停止は `Ctrl+C` です。

### 7. Fanvueへ投稿する（Phase 2〜4）

`.env`に`FANVUE_API_TOKEN`・`FANVUE_HANDLE`・`FANVUE_POST_URL_TEMPLATE`を設定した上で、承認済み（`content_rating_confirmed`）かつFanvueチャンネル指定のアセットがある状態で実行します。

```bash
reelmilly run drop
```

`status="ready"`で最も古い対象アセットを1件、Fanvueへ投稿します（`upload → ready待ち → post作成`）。成功すると`status="posted"`になり、失敗すると`status="failed_fanvue"`になります（自動リトライはしません）。同じ日に2回実行すると2回目はスキップされます。X（旧Twitter）への紹介投稿は未実装のため、このコマンドはFanvue投稿のみを行います。

Fanvue APIのレスポンス形式は一次情報を検証していない実装のため、実行して失敗する場合は[TODO.md](TODO.md)を参照してください。

### テストの実行

```bash
pytest
```

外部I/O（NSFW推論モデル本体、Playwright、Fanvue/X API）は単体テストの対象外とし、モックで検証しています。実際のNSFW判定精度・投稿動作は、実機でのingest/web操作を通じて確認してください。
