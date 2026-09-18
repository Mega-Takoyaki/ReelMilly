# Reelmilly 開発引き継ぎ: Imagine手置き → Fanvue → X / Telegram運用

この文書だけ読めば実装を始められること。実装コードは参考スケルトンがあるが、**この計画を正**とする。ユーザー操作と通知はすべて Telegram。

作成日: 2026-09-15
改訂日: 2026-09-17（プロジェクト名・アプリ名を **Reelmilly** に確定）
タイムゾーン: Asia/Tokyo

---

## 0. 命名

- **プロジェクト名（リポジトリ／ディレクトリ名）**: `reelmilly`
- **アプリ名（Telegram通知・CLI表示等でユーザーに見える名前）**: `Reelmilly`
- Telegram Bot をBotFatherで作成する際は `ReelmillyBot`（または空き状況に応じた近い名称）を推奨。実際に取得できるユーザー名は実装時に確認する。
- 本文中の `project/` というディレクトリ表記は、以降すべて `reelmilly/` を指すものとする。

---

## 1. 目的

Grok Imagine で生成した画像・動画を、ユーザーがローカルへ手動保存したあと、

1. ライブラリ化する
2. Fanvue に本編投稿する（公式API）
3. 投稿された Fanvue URL を紹介文とともに X へ投稿する（Playwright、ログインはユーザーが手動）
4. 本編以外の X 投稿も別頻度で出す
5. 承認・失敗・ログイン切れなどのやり取りは Telegram ボット（`Reelmilly`）で行う

Imagine API は呼ばない。生成とダウンロードは人手。

---

## 2. やらないこと

- Imagine の自動生成・自動ダウンロード
- X 公式APIでの投稿（課金と申請を避ける方針）
- X のパスワード / 2FA の自動入力
- Fanvue ダッシュボードの Playwright 操作（公式APIを使う）
- 失敗ジョブの無限自動リトライ
- ヘッドレスでの初回 X ログイン
- Discord / Slack / メール通知（Telegram に一本化）

---

## 3. アクターと信頼境界

| アクター | 役割 |
|---|---|
| ユーザー | Imagine出力をinboxへ置く。X初回ログイン。Telegramで承認・キャプション修正 |
| ワーカー（`reelmilly` 常駐プロセス） | ingest、スケジュール、Fanvue API、Playwright、Telegram Bot |
| Fanvue | OAuthトークンでメディアupload + 投稿作成 |
| X | 永続ブラウザプロファイル。セッション切れ時は投稿せずTelegramで止める |

秘密情報は `.env` のみ。リポジトリに置かない。

---

## 4. システム概要

```
[ユーザー PC]
  Imagine手動DL → data/library/inbox/

[reelmilly ワーカー]
  ingest → ready/{id}/ + meta.yaml
       ↓
  Telegram（Reelmilly）: 「この素材を drop してよいか」承認（任意だが初期は必須推奨）
       ↓
  Job runner (Asia/Tokyo)
       ├ drop        Fanvue投稿 → URL取得 → X紹介投稿
       ├ x_teaser    Xのみ（x_only フォルダ or channels:[x]）
       └ x_engagement テキストのみ
       ↓
  Telegram 通知（成功 / 失敗 / 要ログイン）
```

部分失敗ルール（重要）:

- Fanvue 成功・X 失敗: 素材は posted 扱い（二重Fanvue防止）。Telegramに Fanvue URL を載せ「Xだけ再送」ボタンを出す
- Fanvue 失敗: ready のまま。Telegramに理由。自動リトライしない
- X セッション切れ: どのジョブも投稿せず停止。`login-x` 相当をローカルでユーザーが実行するよう案内

---

## 5. ディレクトリ契約

```
reelmilly/
  data/library/inbox/      ユーザーが落とす場所
  data/library/ready/      ingest後。1アイテム1フォルダ
  data/library/posted/     完了
  data/library/x_only/     Fanvueを通さないティーザー素材
  data/profiles/x/         Playwright user_data_dir
  data/screenshots/        失敗時PNG
  data/state/state.json    カタログと last_run
  data/state/events.jsonl  監査ログ
  templates/               X文面
  src/                     実装
```

ready アイテム:

```
ready/20260915-ab12cd34/
  look-a.mp4
  meta.yaml
```

`meta.yaml` スキーマ:

```yaml
id: 20260915-ab12cd34
status: ready | approved | posted | failed_fanvue
created_at: ISO-8601
media:
  - data/library/ready/20260915-ab12cd34/look-a.mp4
kind: image | video
caption: ""
x_caption: ""
fanvue_text: ""
audience: subscribers | followers-and-subscribers
price_cents: 499        # null で無料
channels: [fanvue, x]   # [x] なら teaser 候補
fanvue_url: ""          # 投稿後
fanvue_uuid: ""
x_ok: false
```

inbox の sidecar（同名 yaml/json）があれば ingest 時にマージ。その後 sidecar は削除。

---

## 6. ジョブと既定頻度

`config.yaml` の `cadence` が単一の真実。

| job | 内容 | 既定 |
|---|---|---|
| drop | ready先頭（channelsにfanvue）をFanvue→X紹介 | 毎日 21:00 JST |
| x_teaser | x_only または channels=[x] | 毎日 12:00 JST |
| x_engagement | templates/x_engagement.txt | 月・木 18:30 JST |

同じカレンダー日に同じ job を二度走らせない（`state.last_run[job]`）。

文面テンプレ変数: `{caption}` `{fanvue_url}` `{x_caption}`

drop の X 側は既定でメディアなし（本編誘導に集中）。後で「サムネ1枚」をオプション化してよい。

---

## 7. Fanvue

公式REST `https://api.fanvue.com`
ヘッダ: `Authorization: Bearer` + `X-Fanvue-API-Version: 2025-06-26`

必要スコープ: `write:media` `write:post`（点検用に `read:post`）

アップロード:

1. `POST /media/uploads` `{name, filename, mediaType: image|video, sizeBytes}`
2. `GET /media/uploads/{uploadId}/parts/{n}/url` → 署名URLへ PUT
3. `PATCH /media/uploads/{uploadId}` `{parts:[{ETag, PartNumber}]}`
4. `GET /media/{uuid}` を poll。`ready` / `finalised` まで待つ。timeout は設定値（既定90s）
5. `POST /posts` `{audience, text, mediaUuids, price?, mediaPreviewUuid?}`

公開URLはAPIが安定して返さない想定。`.env` のテンプレで組み立てる。

```
FANVUE_POST_URL_TEMPLATE=https://www.fanvue.com/{handle}
```

`{handle}` `{uuid}` を置換。実装前にユーザーが実URLを1本確認してテンプレを確定する。

トークン更新: 初期は長期accessを手置き。refreshが必要なら別issue。期限切れはTelegramで止める。

---

## 8. X (Playwright)

- `chromium.launchPersistentContext(data/profiles/x)`
- 初回は headed。コマンドまたはTelegram指示で「ログイン用ブラウザを開け」
- 投稿先: `https://x.com/compose/post`
- セレクタは配列でフォールバック
  - テキスト: `[data-testid="tweetTextarea_0"]`, `div[contenteditable="true"][role="textbox"]`
  - ボタン: `[data-testid="tweetButton"]`, `[data-testid="tweetButtonInline"]`
  - ファイル: `input[type="file"][data-testid="fileInput"]`
- Reactエディタ対策: click → `keyboard.insert_text`。ボタン無効なら Control/Meta+Enter
- ログイン判定: URLに login / i/flow、または username 入力が出たら未ログイン
- 失敗時は必ず screenshot を保存し、Telegramにパスか内容説明を送る
- X利用規約上、非公式自動化は制限対象になり得る。低頻度（1日数本）を守る

---

## 9. Telegram（操作面の正）

Bot API。ロングポーリングでよい（単一ユーザー）。Bot表示名は `Reelmilly`。

環境変数:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=     # 自分の chat id 以外無視
```

### ボットが送るイベント

| 種別 | 内容 | ボタン例 |
|---|---|---|
| ingest_ok | 取り込んだ id, kind, ファイル名 | Approve drop / Edit caption / Move to x_only |
| drop_preview | 予定文面（Fanvue / X） | Post now / Skip today / Hold |
| drop_ok | Fanvue URL | — |
| x_ok | 投稿した本文先頭 | — |
| fanvue_failed | APIエラー要約 | Retry later |
| x_failed_after_fanvue | Fanvue URL + 理由 | Retry X only |
| x_auth_required | ローカルで login-x してと案内 | — |
| daily_plan | 今日の予定ジョブ | — |

### ユーザーが送れるコマンド

```
/status          ready件数, last_run, Xログイン見込み
/inbox           ingestを今実行
/preview <id>
/approve <id>
/drop            承認済み先頭を今すぐ
/teaser
/engage
/skip drop|teaser|engage
/caption <id> 文章
/hold <id>
```

チャットに画像や動画を直接投げてinbox相当にするのは Phase 2。Phase 1 はローカルinboxのみ。

通知は成功も失敗も送る。同じ障害を1分に何通も送らない（ジョブ単位で1通）。

---

## 10. CLI（Telegram以外の足場）

ローカルデバッグ用に残す。

```
python -m src.cli ingest
python -m src.cli login-x
python -m src.cli doctor
python -m src.cli run drop|x_teaser|x_engagement
python -m src.cli run-due
python -m src.cli watch      # 60s + Telegram polling を同じプロセスで
```

本番は `watch` 1本。

---

## 11. 実装フェーズ（Claude Codeはこの順）

### Phase 0 — 土台
- 設定読込（yaml + dotenv）
- ディレクトリ作成
- events.jsonl
- doctor（Fanvue /users/me と Xホームのログイン判定）

### Phase 1 — ライブラリ
- inbox ingest
- sidecarマージ
- ready/posted移動
- meta.yaml

### Phase 2 — Fanvueクライアント
- multipart upload
- ready待ち
- create post
- URL組み立て
- 単体で1ファイルを上げるCLI

### Phase 3 — X Playwright
- login-x
- テキスト投稿
- 任意メディア
- 失敗スクショ
- セレクタ切れに耐える

### Phase 4 — ジョブ結合
- drop / teaser / engage
- 部分失敗ルール
- last_run と曜日

### Phase 5 — Telegram
- allowlist
- 通知
- 承認フロー（Phase 1では drop 前承認をデフォルトON）
- コマンド

### Phase 6 — 運用磨き
- 文面テンプレ複数からランダム
- dropのXにサムネ1枚オプション
- Fanvueトークンrefresh
- Telegramへのファイル受信

各Phaseの完了条件: そのPhase単体で手動1回成功し、失敗がTelegramまたはログに残ること。

---

## 12. 技術選定

- Python 3.11+
- playwright
- requests
- pyyaml
- python-dotenv
- python-telegram-bot v21 または素の Bot API
- 追加フレームワーク不要。DBは JSON で足りる

参考スケルトン（通知がwebhookだった旧版）が同梱されている場合、Telegram面は作り直す。ジョブ分離とライブラリ契約は流用可。

---

## 13. リスク

- X DOM変更 → セレクタ配列を更新。テストは login-x + 下書きまで
- Fanvueメディア処理遅延 → poll延長を設定化
- 公開URLテンプレ誤り → 最初の1本はユーザー確認
- PlaywrightとTelegramを同じプロセスで回すとGUIマシンが必要。VPSのヘッドレスはセッション維持が弱いので、X投稿はユーザーの常時起動PCを前提にする
- 同一文面連投でX制限 → テンプレに日付や差分を入れる

---

## 14. 受け入れ基準

- inboxにmp4を置く → ingest → Telegramにidが来る
- Approve後、Fanvueに有料/無料投稿が1本できる
- 続けてXに Fanvue URL 入りの紹介文が投稿される
- X未ログイン時は投稿せずTelegramで止まる
- Fanvue成功・X失敗でFanvueに二重投稿しない
- 月木以外に engagement が走らない
- `.env` 以外に秘密が出ない

---

## 15. ユーザーが先に用意するもの

1. Fanvue creator + KYC済み + OAuthアプリ + access token
2. Fanvue handle と実際の投稿URL例 1本
3. Telegram BotFather のトークンと自分の chat id（Bot名候補: `ReelmillyBot`）
4. 投稿用Xアカウント（Playwrightを回すMac/PC）
5. Imagine出力の置き場（inboxパス）

未確定で実装時に聞けること:
- drop前承認を必須にするか（推奨: 最初は必須）
- X紹介文にサムネを付けるか
- 価格の既定値
- Fanvue audience 既定（subscribers）
- GitHub公開時のリポジトリ名（`reelmilly` を仮押さえ済みか、実装着手前に確認）
