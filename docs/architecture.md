# アーキテクチャ設計（arc42テーラリング版）

本プロジェクトの規模（個人開発・単一ユーザー運用）に合わせ、arc42の12章のうち関係する章のみを採用する。

## 1. 序論と目標

画像・動画（Grok Imagine生成物）を管理する本体アプリケーションを優先実装し、その上にFanvueへの投稿とXへの紹介投稿を半自動化する機能を論理プラグインとして付加する（[ADR-0007](adr/0007-asset-management-as-core.md)、[ADR-0013](adr/0013-sns-posting-as-logical-plugin.md)）。本体は単体（SNS投稿機能なし）でも画像管理アプリとして成立するように設計する。詳細は [CLAUDE_HANDOFF.md](../CLAUDE_HANDOFF.md) 1章を参照（引き継ぎ文書は投稿ワークフローを主目的として書かれているが、位置づけの整理はADR-0007/0013を正とする）。

## 2. 制約

- Imagine APIは使用しない
- X公式APIの開発者アプリが承認されない場合、Playwright非公式操作へフォールバックする（[ADR-0014](adr/0014-x-official-api-application-first.md)）。フォールバック時はヘッドレスでのX初回ログイン不可という制約が生じる
- 失敗ジョブの自動無限リトライは行わない
- 秘密情報は `.env` のみ

詳細: CLAUDE_HANDOFF.md 2章・3章。

## 3. コンテキストと範囲

アクター: ユーザー、reelmillyワーカー、Fanvue、X。詳細はCLAUDE_HANDOFF.md 3章・4章のシステム概要図を参照。

## 4. 解決戦略

| 決定 | 採用内容 | 根拠 |
|---|---|---|
| 状態管理 | SQLite（メタデータ・タグ・フォルダの正）＋`events.jsonl`（監査ログ）。DBなし方針(ADR-0004)は非推奨化 | [ADR-0011](adr/0011-sqlite-state-store.md) |
| 操作・通知インターフェース | 本体UIを主、Telegramは通知＋簡易操作の補助チャネル。Telegram一本化(ADR-0001)は非推奨化 | [ADR-0012](adr/0012-primary-ui-with-telegram-as-secondary.md) |
| X投稿 | 公式API（申請ファースト）。承認されない場合のみPlaywrightへフォールバック | [ADR-0014](adr/0014-x-official-api-application-first.md)（旧[ADR-0002](adr/0002-x-posting-via-playwright.md)は非推奨） |
| Fanvue投稿 | 公式REST API | [ADR-0003](adr/0003-fanvue-official-api.md) |
| プラットフォーム別コンテンツルール強制 | `content_rating` + `platform_content_rules`（別プロジェクトCREAMからの部分移植、多プラットフォーム対応の布石） | [ADR-0006](adr/0006-platform-content-rules-from-cream.md) |
| アセット管理を中核ドメインに据える | 投稿ワークフローはアセットのライフサイクル上の一操作として実装する | [ADR-0007](adr/0007-asset-management-as-core.md) |
| NSFW自動仕分け＋人間承認ゲート | `nsfw_auto_rating`（自動・参考値）と`content_rating_confirmed`（人間承認）を分離し、承認済みのみ自動投稿の対象にする | [ADR-0008](adr/0008-nsfw-auto-triage-with-human-approval.md) |
| NSFW分類モデル | Marqo/nsfw-image-detection-384（timm）。動画は2秒間隔フレームサンプリング＋最大値採用。X向けは`sfw`のみ自動投稿対象 | [ADR-0009](adr/0009-nsfw-classifier-marqo.md) |
| 画像・動画管理の自作（Eagle連携は不採用） | 数万件規模のアセットをフォルダ・タグ管理込みでReelMilly自身に実装する | [ADR-0010](adr/0010-custom-asset-management-over-eagle.md) |
| SNS投稿機能のモジュール分離 | 本体（画像管理）とSNS投稿を論理的に分離したパッケージとして実装。動的プラグイン機構は導入しない | [ADR-0013](adr/0013-sns-posting-as-logical-plugin.md) |

## 5. 構成要素の視点

```
reelmilly/
  data/library/{inbox,ready,posted,x_only}/   # メディア実ファイル本体
  data/profiles/x/
  data/screenshots/
  data/state/{reelmilly.db,events.jsonl}      # メタデータはSQLite、監査ログはJSONL
  templates/
  src/
    core/        # 本体: 画像・動画管理(ingest, SQLiteアクセス, NSFW自動仕分け, UI)
    posting/      # SNS投稿モジュール: Fanvue API, X公式API(フォールバック時はPlaywright), ジョブスケジューリング
    telegram/     # Telegram連携: 通知 + 簡易操作(承認/スキップ)
```

ファイル本体はディレクトリ契約（CLAUDE_HANDOFF.md 5章）のまま配置するが、メタデータ（`status`/`content_rating`/タグ/フォルダ等）は`meta.yaml`ではなくSQLite（`reelmilly.db`）で管理する（[ADR-0011](adr/0011-sqlite-state-store.md)）。`core`/`posting`/`telegram`のモジュール分離は[ADR-0013](adr/0013-sns-posting-as-logical-plugin.md)に基づく。`posting`と`telegram`は`core`が提供するデータアクセス層（`core.db`）を通じてのみ連携し、`core`のドメインロジック（ingest/NSFW仕分け/Web UI等）は他モジュールへ依存しない。ただしCLIエントリポイント（`src/core/cli.py`）は例外で、`doctor`（Fanvue疎通確認）・`run drop`コマンドの実装上`posting`を遅延importする（本体と連携先を横断する統合エントリポイントという役割のため）。

## 6. ランタイムビュー

主要フロー（drop、x_teaser、x_engagement）と部分失敗時の挙動はCLAUDE_HANDOFF.md 4章・6章を参照。

## 7. 配置ビュー

**未確定**。[ADR-0013](adr/0013-sns-posting-as-logical-plugin.md)で本体とSNS投稿モジュールを論理分離したため、デプロイ先も別々に検討できる。

- 本体（画像管理アプリ）: ローカルWebアプリが現時点の基本方針だが未確定
- SNS投稿モジュール: X公式API採用（[ADR-0014](adr/0014-x-official-api-application-first.md)）が承認されれば、ブラウザ実行環境が不要になり本体と同じ環境で足りる。Playwrightにフォールバックする場合のみ、GUI環境の常時起動等の制約が生じる

判断軸は [ADR-0005](adr/0005-deployment-environment.md) を参照。

## 8. 横断的関心事

- タイムゾーン: Asia/Tokyo固定
- 秘密情報管理: `.env`のみ、リポジトリにコミットしない
- ログ: `events.jsonl`への監査ログ、失敗時は`data/screenshots/`にスクリーンショット保存
- コンテンツルール: プラットフォームごとの許可コンテンツ区分を`config.yaml`で強制（[ADR-0006](adr/0006-platform-content-rules-from-cream.md)）
- コンプライアンス確認（AI生成の明示・年齢表現）: システムでの自動判定・記録は行わず、投稿前に運用者が毎回目視確認する運用ルールとする（[TODO.md](../TODO.md)参照、将来再検討の余地あり）
- NSFW区分の確定: 自動仕分けは参考値にとどめ、人間の明示的承認を経たアセットのみ自動投稿の対象とする（[ADR-0008](adr/0008-nsfw-auto-triage-with-human-approval.md)）

## 9. アーキテクチャ決定

[docs/adr/](adr) を参照。

## 10. 品質要求

- 部分失敗時にFanvueへの二重投稿が発生しないこと
- X未ログイン時に投稿せず安全に停止すること
- 同一ジョブが同一カレンダー日に二重実行されないこと

## 11. リスクと技術的負債

CLAUDE_HANDOFF.md 13章を参照（Fanvueメディア処理遅延、公開URLテンプレ誤り、同一文面連投によるX制限等）。X DOM変更リスクは[ADR-0014](adr/0014-x-official-api-application-first.md)の公式API採用が承認された場合は解消される（Playwrightフォールバック時のみ残存）。

## 12. 用語集

| 用語 | 意味 |
|---|---|
| drop | Fanvueへの本編投稿＋X紹介投稿のジョブ |
| x_teaser | Fanvueを通さないXのみのティーザー投稿 |
| x_engagement | テキストのみのXエンゲージメント投稿 |
| ready | ingest済み・投稿待ちの素材状態 |
| content_rating | アセットのコンテンツ露出度区分（`sfw`/`suggestive`/`explicit`）の確定値。人間承認後に設定される |
| nsfw_auto_rating | NSFW自動仕分けによる一次判定結果（参考値、確定値ではない） |
