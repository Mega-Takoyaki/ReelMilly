# 0019. アセットステータス`ready`を`pending_approval`に改名し、承認後の`ready`を新設する

- ステータス: 決定
- 決定日: 2026-09-27
- 決定者: プロジェクトオーナー

## コンテキストと課題

[ADR-0015](0015-ai-content-description-and-caption-generation.md)以降、`status="ready"`は「NSFW自動仕分け・内容説明の両方をAIが取得済み」であることのみを意味し、人間による承認（`content_rating_confirmed`）は別カラムで管理していた。Fanvueへの自動投稿は`status="ready"`かつ`content_rating_confirmed=1`の両方を満たす場合のみ許可される（[ADR-0008](0008-nsfw-auto-triage-with-human-approval.md)）。

この設計は、プロジェクトオーナーから見て「`ready`という名前なのに、実際にはまだ人間の承認待ちで投稿できない」という誤解を招くものだった。実際、`status`列だけを見ても「投稿準備が本当に整っているか」は判断できず、`content_rating_confirmed`列と突き合わせる必要があった。

## 決定

ステータス名を実態に合わせて変更し、`status`列単独で状態が判断できるようにする。

| 状態 | 変更前 | 変更後 |
|---|---|---|
| NSFW判定・内容説明のいずれか未完了 | `analyzing` | `analyzing`（変更なし） |
| 両方完了・人間の承認待ち | `ready` | `pending_approval`（新設） |
| 人間が承認済み・投稿準備完了 | `ready`＋`content_rating_confirmed=1` | `ready`（意味を変更） |
| 投稿済み／投稿失敗 | `posted`／`failed_fanvue` | 変更なし |

### 遷移ルール

- `ingest_inbox`/`reelmilly analyze`でNSFW自動仕分け・内容説明の両方が成功した場合、新規に取り込まれたアセットは常に未承認（`content_rating_confirmed=0`）なので`status="pending_approval"`にする
- ただし`reelmilly analyze`が対象とする既存の`analyzing`状態のアセットは、分析待ちの間に人間が先に`/assets/<id>/confirm`で承認していた場合がありうる。その場合は`pending_approval`を経由せず直接`status="ready"`にする（`content_rating_confirmed`が既に1のケース）
- `/assets/<id>/confirm`・`/assets/bulk/confirm`（承認操作）は、対象アセットの現在の`status`が`pending_approval`の場合のみ`status="ready"`へ遷移させる。既に`ready`/`posted`/`failed_fanvue`のアセットを再承認しても`status`は変更しない（投稿済みアセットのレーティングを後から訂正するようなケースで、誤って状態を巻き戻さないため）

`content_rating_confirmed`カラム自体は維持する（`analyzing`状態のまま早期承認されるケースがあり、`status`だけでは表現しきれないため）。[ADR-0018](0018-auto-tagging-and-pending-approval-review.md)で追加した一覧画面の「承認状態」フィルタ（`confirmed`引数）もそのまま維持する。

`posting/jobs.py`のFanvue投稿候補選定クエリ（`status="ready"`かつ`confirmed_only=True`）は、意味的には`status="ready"`だけで十分になったが、`content_rating_confirmed`側の不整合が万一発生した場合の保険として両方の条件を残す。

## 結果

### 良い影響

- `status`列の値だけを見れば「分析中／承認待ち／投稿準備完了／投稿済み／投稿失敗」のどの段階かが一目でわかるようになった
- 一覧画面のステータスフィルタで`pending_approval`を選ぶだけで、レビューが必要なアセットを絞り込めるようになった（ADR-0018の「承認状態」フィルタと役割が一部重複するが、後者は`analyzing`のまま早期承認された稀なケースの捕捉に有用なため両方残す）

### 悪い影響・トレードオフ

- ステータス値の名称変更は、DB上の既存データ（本セッションでは実データなし）・UI・CLI出力メッセージ・テストの全てに影響する破壊的変更である
- `status`と`content_rating_confirmed`の二重管理自体は解消していない（`analyzing`中の早期承認という抜け道が残るため）。完全に一本化するにはingest/analyzeのタイミングを問わず承認操作を受け付けない設計に変える必要があるが、運用上の柔軟性を優先し見送った
