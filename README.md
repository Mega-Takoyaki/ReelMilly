# Reelmilly

画像・動画（Grok Imagine生成物）を管理する本体アプリケーションを優先実装し、その上にFanvueへの本編投稿とX（旧Twitter）への紹介投稿を半自動化する機能を論理プラグインとして付加する（[ADR-0007](docs/adr/0007-asset-management-as-core.md)、[ADR-0013](docs/adr/0013-sns-posting-as-logical-plugin.md)）。操作は本体UIを主とし、Telegram Botは通知と簡易操作（承認・スキップ）の補助チャネルとして使う（[ADR-0012](docs/adr/0012-primary-ui-with-telegram-as-secondary.md)）。

## ステータス

本体（画像・動画管理アプリ、Phase 0〜1.5相当）、Fanvue投稿ジョブ（Phase 2・4のFanvue部分）、NSFW自動仕分け（実機動作確認済み）、自動実行スケジューラ（`run-due`/`watch`）、AIによる画像内容説明・Fanvue投稿文の自動生成（[ADR-0015](docs/adr/0015-ai-content-description-and-caption-generation.md)）を実装済み。FanvueのAPI実疎通・OpenAI APIプロバイダーの実疎通は未確認。X投稿（Phase 3）はX開発者アプリの申請待ちで未着手。進捗は [docs/roadmap.md](docs/roadmap.md) を参照。

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

NSFW自動仕分け機能（[ADR-0009](docs/adr/0009-nsfw-classifier-marqo.md)）を使う場合は、追加でtorch/timmをインストールします。GPUが不要であればCPU版を先に指定しておくとダウンロード容量を抑えられます（推奨、動作確認済み）。

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[nsfw]"
```

（GPU版を使いたい場合、または既に`torch`をインストール済みの場合は`pip install -e ".[nsfw]"`のみで構いません）

初回`ingest`実行時、Marqoモデルの重み（`marqo/nsfw-image-detection-384`）がHugging Face Hubから自動ダウンロードされます。

画像内容説明・Fanvue投稿文の自動生成（[ADR-0015](docs/adr/0015-ai-content-description-and-caption-generation.md)）を使う場合は、Claude APIのSDKを追加でインストールします。

```bash
pip install -e ".[ai]"
```

OpenAI APIを使いたい場合は別途 `pip install openai` してください（本体UIの設定画面でプロバイダーを切り替えます。実API疎通は未検証です）。

> **重要**: 一覧・フォルダ・タグ・承認UIなど本体機能はNSFW自動仕分け・画像内容説明のどちらも未設定でも動作しますが、**両方が取得できたアセットのみ`status="ready"`（Fanvue投稿対象）になります**（ADR-0015）。片方でも未設定・失敗の場合は`status="analyzing"`のまま残り、Fanvueへは自動投稿されません。以前のバージョンでは「NSFW仕分けなしでもすぐready」でしたが、投稿文の自動生成を導入したことでこの挙動に変更しました。

### 3. 秘密情報の設定

```bash
cp .env.example .env
```

`FANVUE_API_TOKEN`・`ANTHROPIC_API_KEY`等の値は、直接`.env`を編集する代わりに本体UIの設定画面（`/settings`、後述6.）からも設定できます（[ADR-0016](docs/adr/0016-connection-settings-editable-via-web-ui.md)）。本体のみの動作にはFanvue/Telegram/生成AIの値は不要です。

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

NSFW自動仕分け・画像内容説明の両方が成功したアセットは`status="ready"`になります。どちらか一方でも未設定・失敗の場合は`status="analyzing"`のまま残ります（一覧・タグ・フォルダ操作は可能ですが、Fanvue投稿の対象にはなりません）。`analyzing`のまま残ったアセットは、設定を直してから以下のコマンドで再試行できます。

```bash
reelmilly analyze
```

### 6. 本体UIを起動する

```bash
reelmilly web
```

起動後、ブラウザで `http://127.0.0.1:8420/` を開くと、一覧・フォルダ・タグ管理・投稿承認（`content_rating`確定）操作ができます。既定では他の端末からはアクセスできません（`config.yaml`の`web.host`が`127.0.0.1`固定のため）。停止は `Ctrl+C` です。

画面はOSのライト/ダーク設定に自動追従し、スマートフォン幅でも操作できるようレスポンシブ対応しています（ビルドツールや追加のフロントエンドライブラリは使わず、素のCSS/JSのままモダン化しています）。

画面右上の「設定」（`/settings`）では以下を設定できます。

- **接続設定**（[ADR-0016](docs/adr/0016-connection-settings-editable-via-web-ui.md)）: Fanvue APIトークン・ハンドル・投稿URLテンプレート、Telegram（連携自体は未実装）、Anthropic/OpenAIのAPIキー。実体は`.env`ファイルで、画面はその読み書きを行うだけです。APIトークン等の秘密項目は画面に値を表示せず「設定済み/未設定」のみ表示し、空欄のまま保存すれば既存の値は変更されません
- **画像内容説明・Fanvue投稿文の自動生成**（[ADR-0015](docs/adr/0015-ai-content-description-and-caption-generation.md)）: 生成AIプロバイダー（Claude API/OpenAI API）・モデル名・システムプロンプト・投稿文の生成モード（`auto`/`draft`）

保存できても実際にAPIへ接続できるとは限りません。疎通確認は`reelmilly doctor`で行ってください。

### 7. Fanvueへ投稿する（Phase 2〜4）

`FANVUE_API_TOKEN`・`FANVUE_HANDLE`・`FANVUE_POST_URL_TEMPLATE`を設定した上で（`.env`を直接編集するか、本体UIの設定画面から設定）、承認済み（`content_rating_confirmed`）かつFanvueチャンネル指定のアセットがある状態で実行します。

```bash
reelmilly run drop
reelmilly run drop --count 3               # 1回の実行で最大3件まで投稿する
reelmilly run drop --kind image --rating sfw  # 種別・content_ratingで絞り込む
```

`status="ready"`の対象アセットを最も古いものから（既定1件）Fanvueへ投稿します（`upload → ready待ち → post作成`）。成功すると`status="posted"`になり、失敗すると`status="failed_fanvue"`になります（自動リトライはしません）。同じ日に2回実行すると2回目はスキップされます（`--count`で指定した件数は1回の実行内でまとめて投稿されます）。X（旧Twitter）への紹介投稿は未実装のため、このコマンドはFanvue投稿のみを行います。

投稿文（`fanvue_text`）が未設定のアセットは、内容説明とシステムプロンプトから自動生成されます。設定画面で生成モードを`draft`にしている場合、生成結果は投稿には使わず下書き（本体UIの詳細画面から確認・採用可能）として保存するだけになります。

Fanvue APIのレスポンス形式は一次情報を検証していない実装のため、実行して失敗する場合は[TODO.md](TODO.md)を参照してください。

### 8. 自動実行スケジューラ

`reelmilly run drop`を毎回手動実行する代わりに、`config.yaml`の`cadence`設定で決めた時刻以降に自動実行させることができます。

```yaml
# config.yaml
cadence:
  drop: "21:00"   # 21:00以降・当日未実行ならdropジョブを実行対象にする
  # オプション(枚数・種別・レーティング)を指定する場合は辞書形式にする
  # drop:
  #   time: "21:00"
  #   count: 3
  #   kind: image
  #   rating: sfw
```

時刻が来ているかだけを1回チェックして即終了するコマンド:

```bash
reelmilly run-due
```

これをOSのタスクスケジューラ（Windowsなら「タスクスケジューラ」、Mac/Linuxなら`cron`）で例えば5〜10分おきに実行する運用と、以下の常駐コマンドで動かし続ける運用のどちらかを選べます。

```bash
reelmilly watch              # 60秒間隔でanalyze/run-dueを繰り返す（既定）
reelmilly watch --interval 300  # 間隔を変更する場合
```

`watch`はターミナルを開いたままにする常駐プロセスです。停止は`Ctrl+C`。`run-due`に加えて`analyze`（`analyzing`状態のアセットの再試行）も毎回実行します。同日二重実行防止（`job_runs`テーブル）は`run-due`/`watch`経由でも`run drop`と同様に効きます。`cadence`未設定のジョブ名（`drop`以外）は現状未対応で、指定してもスキップされログに表示されます（X投稿ジョブは未実装のため）。

### テストの実行

```bash
pytest
```

外部I/O（NSFW推論モデル本体、Playwright、Fanvue/X API）は単体テストの対象外とし、モックで検証しています。実際のNSFW判定精度・投稿動作は、実機でのingest/web操作を通じて確認してください。
