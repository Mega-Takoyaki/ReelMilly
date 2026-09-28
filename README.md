# Reelmilly

画像・動画（Grok Imagine生成物）を管理する本体アプリケーションを優先実装し、その上にFanvueへの本編投稿とX（旧Twitter）への紹介投稿を半自動化する機能を論理プラグインとして付加する（[ADR-0007](docs/adr/0007-asset-management-as-core.md)、[ADR-0013](docs/adr/0013-sns-posting-as-logical-plugin.md)）。操作は本体UIを主とし、Telegram Botは通知と簡易操作（承認・スキップ）の補助チャネルとして使う（[ADR-0012](docs/adr/0012-primary-ui-with-telegram-as-secondary.md)）。

## ステータス

本体（画像・動画管理アプリ、Phase 0〜1.5相当）、Fanvue投稿ジョブ（Phase 2・4のFanvue部分）、NSFW自動仕分け（実機動作確認済み）、自動実行スケジューラ（`run-due`/`watch`）、AIによる画像内容説明・Fanvue投稿文の自動生成（Claude API/OpenAI API/自前ホスト型VLM、[ADR-0015](docs/adr/0015-ai-content-description-and-caption-generation.md)・[ADR-0017](docs/adr/0017-local-vlm-for-explicit-content.md)）を実装済み。FanvueのAPI実疎通・OpenAI APIプロバイダー・自前ホスト型VLMの実疎通は未確認。X投稿（Phase 3）はX開発者アプリの申請待ちで未着手。進捗は [docs/roadmap.md](docs/roadmap.md) を参照。

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

> **注意（重要）**: Claude API/OpenAI APIは、利用ポリシー上「性的に露骨なコンテンツ」の生成・説明を拒否する可能性が高いです（[ADR-0017](docs/adr/0017-local-vlm-for-explicit-content.md)）。`content_rating=explicit`のアセットには、次段落の「自前ホスト型VLM」を使ってください。AWS Bedrock経由でも同じ制約（+AWS自身の利用規約）がかかるため回避策にはなりません。

`explicit`判定のアセットにも対応する場合は、自前ホスト型VLM（Vision-Language Model）を使います（[ADR-0017](docs/adr/0017-local-vlm-for-explicit-content.md)）。外部サービスの利用ポリシーに縛られず、ローカルで推論するためAPI課金も発生しません。

CPUのみの環境（ダウンロード容量を抑える場合、動作確認済みのNSFW自動仕分けと同様）:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[vlm]"
```

GPU（NVIDIA/CUDA）を搭載した環境、または将来GPUマシンに移行する場合は、CPU専用ビルドを指定せず通常どおりインストールしてください（GPU対応ビルドが入ります）。

```bash
pip install -e ".[vlm]"
```

コード側は`torch.cuda.is_available()`でGPUの有無を実行時に自動判定するため、どちらの手順でインストールしても設定変更は不要です。ただし**CPU専用ビルドを一度インストールすると、後からそのマシンにGPUを追加してもCUDAは使えません**（ビルド自体にCUDA対応コードが含まれないため）。GPU環境に移行する際は、CPU専用の`--index-url`を付けずに入れ直してください。

本体UIの設定画面で生成AIプロバイダーを「自前ホスト型VLM」に切り替えてください。既定モデル（`prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it`）の重み（数GB）は初回利用時にHugging Face Hubから自動ダウンロードされます。GPUがあれば自動的に使われ高速化されます。GPUが無い環境ではCPU推論となり、1枚あたり数秒〜数十秒かかる見込みです（実機未検証）。`reelmilly analyze`はバックグラウンドのバッチ処理として設計されているため、即日投稿が不要な運用であれば実用上問題ない想定です。

> **重要**: 一覧・フォルダ・タグ・承認UIなど本体機能はNSFW自動仕分け・画像内容説明のどちらも未設定でも動作しますが、**両方が取得できたアセットのみ`status="pending_approval"`（人間の承認待ち）になります**（ADR-0015、ADR-0019）。片方でも未設定・失敗の場合は`status="analyzing"`のまま残り、Fanvueへは自動投稿されません。以前のバージョンでは「NSFW仕分けなしでもすぐready」でしたが、投稿文の自動生成を導入したことでこの挙動に変更しました。

### 3. 秘密情報の設定

```bash
cp .env.example .env
```

`FANVUE_OAUTH_CLIENT_ID`・`ANTHROPIC_API_KEY`等の値は、直接`.env`を編集する代わりに本体UIの設定画面（`/settings`、後述6.）からも設定できます（[ADR-0016](docs/adr/0016-connection-settings-editable-via-web-ui.md)）。本体のみの動作にはFanvue/Telegram/生成AIの値は不要です。

Fanvueへの投稿にはFanvue側での事前準備（クリエイター登録・KYC完了・Developer領域でのOAuthアプリ作成）が必要です。Fanvue APIは静的なAPIトークンではなくOAuth 2.0認証専用のため、`.env`にはトークンではなくOAuthアプリの`FANVUE_OAUTH_CLIENT_ID`・`FANVUE_OAUTH_CLIENT_SECRET`を設定します（[ADR-0021](docs/adr/0021-fanvue-oauth2-authentication.md)）。実際の連携（アクセストークンの取得）は本体UIの設定画面から行います（後述6.）。

### 4. 初期化と疎通確認

```bash
reelmilly init     # ディレクトリとSQLiteデータベースを作成
reelmilly doctor   # ディレクトリ・DB・(連携済みであれば)Fanvue APIの疎通を確認
```

Fanvueと連携済み（後述6.で連携）であれば、`doctor`が`GET /users/me`でFanvue APIの疎通も確認します（未連携ならスキップされ、失敗にはなりません）。Fanvue APIのトークンエンドポイントのレスポンス形式は一次情報での検証を行っていない実装のため、実行して失敗する場合は[TODO.md](TODO.md)を参照してください。

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

NSFW自動仕分け・画像内容説明の両方が成功したアセットは`status="pending_approval"`（人間の承認待ち）になります。どちらか一方でも未設定・失敗の場合は`status="analyzing"`のまま残ります（一覧・タグ・フォルダ操作は可能ですが、Fanvue投稿の対象にはなりません）。`analyzing`のまま残ったアセットは、設定を直してから以下のコマンドで再試行できます。

```bash
reelmilly analyze
```

`reelmilly watch`実行中は上記の分析が定期的に非同期実行されるため、`status="pending_approval"`のアセットが承認待ちのまま溜まっていきます（[ADR-0018](docs/adr/0018-auto-tagging-and-pending-approval-review.md)）。一覧画面の「ステータス」フィルタで「pending_approval」を選び、「表示中をすべて選択」＋一括承認（`content_rating`確定）を組み合わせることで、後でまとめてレビューできます。承認すると`status="ready"`（文字通り投稿準備完了）に自動的に遷移します（[ADR-0019](docs/adr/0019-status-rename-pending-approval.md)）。また、内容説明の生成時にはAIがタグを2〜3個自動で提案し、NSFW自動仕分けの結果（`sfw`/`nsfw`）もタグとして自動付与されます。

アセットのステータスは次の順に遷移します。

| ステータス | 意味 |
|---|---|
| `analyzing` | NSFW自動仕分け・内容説明のいずれかが未完了（分析中） |
| `pending_approval` | 両方完了、人間の承認待ち |
| `ready` | 人間が承認済み、投稿準備完了 |
| `posted` | Fanvueへ投稿済み |
| `failed_fanvue` | 投稿試行に失敗（自動リトライなし） |

### 6. 本体UIを起動する

```bash
reelmilly web
```

起動後、ブラウザで `http://127.0.0.1:8420/` を開くと、一覧・フォルダ・タグ管理・投稿承認（`content_rating`確定）操作ができます。既定では他の端末からはアクセスできません（`config.yaml`の`web.host`が`127.0.0.1`固定のため）。停止は `Ctrl+C` です。

画面はOSのライト/ダーク設定に自動追従し、スマートフォン幅でも操作できるようレスポンシブ対応しています（ビルドツールや追加のフロントエンドライブラリは使わず、素のCSS/JSのままモダン化しています）。

画面右上の「設定」（`/settings`）では以下を設定できます。

- **接続設定**（[ADR-0016](docs/adr/0016-connection-settings-editable-via-web-ui.md)）: Fanvue OAuthアプリのClient ID/Secret・ハンドル・投稿URLテンプレート、Telegram（連携自体は未実装）、Anthropic/OpenAIのAPIキー。実体は`.env`ファイルで、画面はその読み書きを行うだけです。Client Secret等の秘密項目は画面に値を表示せず「設定済み/未設定」のみ表示し、空欄のまま保存すれば既存の値は変更されません
- **Fanvue連携**（[ADR-0021](docs/adr/0021-fanvue-oauth2-authentication.md)）: 上記の接続設定でClient ID/Secretを保存した後、「Fanvueと連携する」ボタンからOAuth 2.0認可フローを開始できます。連携が成功するとアクセストークン・リフレッシュトークンが`data/state/fanvue_oauth_tokens.json`に保存され、以降は期限切れ前に自動更新されます（トークンそのものは`.env`には保存されません）。連携解除ボタンでこのファイルを削除できます
- **画像内容説明・Fanvue投稿文の自動生成**（[ADR-0015](docs/adr/0015-ai-content-description-and-caption-generation.md)・[ADR-0017](docs/adr/0017-local-vlm-for-explicit-content.md)）: 生成AIプロバイダー（Claude API/OpenAI API/自前ホスト型VLM）・モデル名・システムプロンプト・投稿文の生成モード（`auto`/`draft`）。`explicit`判定のアセットには自前ホスト型VLMを使ってください（Claude API/OpenAI APIは拒否する可能性が高いため）

保存できても実際にAPIへ接続できるとは限りません。疎通確認は`reelmilly doctor`で行ってください。

### 7. Fanvueへ投稿する（Phase 2〜4）

本体UIの設定画面でFanvueと連携し（上記6.）、`FANVUE_HANDLE`・`FANVUE_POST_URL_TEMPLATE`を設定した上で、承認済み（`content_rating_confirmed`）かつFanvueチャンネル指定のアセットがある状態で実行します。

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

設定画面（`/settings`）の「inboxフォルダの自動取り込み」をオンにすると、`watch`は毎回`ingest`も実行するようになり、`inbox`フォルダに画像・動画を置くだけで自動的に取り込まれます（[ADR-0020](docs/adr/0020-watch-auto-ingest-setting.md)）。既定はオフで、その場合は従来通り`reelmilly ingest`の手動実行かWeb UIのドラッグ&ドロップで取り込んでください。取り込みは**コピーではなく移動**です。`inbox`に置いたファイルは、取り込み後は本体管理下の`data/library/ready/<アセットID>/`フォルダへ移動され、複製による容量増大は起きません。ただし元の置き場所からは無くなるため、自動取り込みをオンにする場合は`inbox`に意図しないファイルを置かないよう注意してください。

### テストの実行

```bash
pytest
```

外部I/O（NSFW推論モデル本体、Playwright、Fanvue/X API）は単体テストの対象外とし、モックで検証しています。実際のNSFW判定精度・投稿動作は、実機でのingest/web操作を通じて確認してください。
