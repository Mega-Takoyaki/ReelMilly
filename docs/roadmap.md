# ロードマップ

Phase構成は [CLAUDE_HANDOFF.md](../CLAUDE_HANDOFF.md) 11章に基づく。各Phaseの完了条件は「そのPhase単体で手動1回成功し、失敗がTelegramまたはログに残ること」。

ReelMillyはアセット（画像・動画）管理を中核に据え、SNS投稿はその上に付加する機能という位置づけ（[ADR-0007](adr/0007-asset-management-as-core.md)）。この位置づけに基づき、Phase 1にNSFW自動仕分け、Phase 5にその人間承認ステップを追加する（[ADR-0008](adr/0008-nsfw-auto-triage-with-human-approval.md)）。

画像・動画管理は外部ツール（Eagle）連携ではなくReelMilly自身に実装し（[ADR-0010](adr/0010-custom-asset-management-over-eagle.md)）、数万件規模を見据えてメタデータ管理を`meta.yaml`からSQLiteに変更した（[ADR-0011](adr/0011-sqlite-state-store.md)）。これに伴いPhase 1の内容を「ライブラリ（SQLite）」に更新し、フォルダ・タグ管理を含むアセット管理UIをPhase 1.5として追加する。

本体（画像管理アプリ）を優先実装し、SNS投稿機能は論理プラグインとして分離する（[ADR-0013](adr/0013-sns-posting-as-logical-plugin.md)）。操作・通知の主役もTelegramから本体UIに移り、Telegramは通知＋簡易操作（承認・スキップ）の補助チャネルとなる（[ADR-0012](adr/0012-primary-ui-with-telegram-as-secondary.md)）。これに伴い、Phase 1.5に「承認・`content_rating`確定」操作を含め、Phase 5は「Telegram通知＋簡易操作」に役割を縮小する。

## 全体像

```mermaid
flowchart TD
    P0["Phase 0：土台（設定読込・ディレクトリ・doctor）"]
    P1["Phase 1：ライブラリ（inbox ingest・SQLite）"]
    P1_5["Phase 1.5：本体UI（一覧・フォルダ・タグ・承認）"]
    P2["Phase 2：Fanvueクライアント（upload・投稿）"]
    P3["Phase 3：X Playwright（login・投稿・失敗時スクショ）"]
    P4["Phase 4：ジョブ結合（drop・teaser・engage）"]
    P5["Phase 5：Telegram補助チャネル（通知・簡易操作）"]
    P6["Phase 6：運用磨き（テンプレ複数化・token refresh等）"]

    P0 --> P1 --> P1_5 --> P2 --> P3 --> P4 --> P5 --> P6
```

## Phase一覧

| Phase | 内容 | 状態 |
|---|---|---|
| Phase 0 | 設定読込、ディレクトリ作成、events.jsonl、doctor | 未着手 |
| Phase 1 | inbox ingest、sidecarマージ、ready/posted移動、**SQLiteへのメタデータ記録**（[ADR-0011](adr/0011-sqlite-state-store.md)）、**NSFW自動仕分け（`nsfw_auto_rating`記録）** | 未着手 |
| Phase 1.5 | 本体UI（一覧・サムネイル表示・フォルダ管理・タグ管理・**投稿承認/`content_rating`確定操作**。全文検索は対象外）（[ADR-0010](adr/0010-custom-asset-management-over-eagle.md)、[ADR-0012](adr/0012-primary-ui-with-telegram-as-secondary.md)） | 未着手 |
| Phase 2 | Fanvue multipart upload、ready待ち、create post、URL組み立て（`posting`モジュール、[ADR-0013](adr/0013-sns-posting-as-logical-plugin.md)） | 未着手 |
| Phase 3 | X Playwright login、テキスト投稿、任意メディア、失敗スクショ（`posting`モジュール） | 未着手（デプロイ環境の決定が前提、TODO.md参照） |
| Phase 4 | drop/teaser/engageジョブ結合、部分失敗ルール、last_run管理、**`content_rating_confirmed`未承認アセットの投稿対象外フィルタ** | 未着手 |
| Phase 5 | Telegram allowlist、通知、**簡易操作（承認/スキップのみ、詳細操作は本体UIへ）**（`telegram`モジュール、[ADR-0012](adr/0012-primary-ui-with-telegram-as-secondary.md)） | 未着手 |
| Phase 6 | 文面テンプレ複数化、サムネオプション、token refresh、ファイル受信 | 未着手 |

## 受け入れ基準（全体）

CLAUDE_HANDOFF.md 14章より:

- inboxにmp4を置く → ingest → Telegramにidが来る
- Approve後、Fanvueに有料/無料投稿が1本できる
- 続けてXにFanvue URL入りの紹介文が投稿される
- X未ログイン時は投稿せずTelegramで止まる
- Fanvue成功・X失敗でFanvueに二重投稿しない
- 月木以外にengagementが走らない
- `.env`以外に秘密が出ない
