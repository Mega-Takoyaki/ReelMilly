# アーキテクチャ設計（arc42テーラリング版）

本プロジェクトの規模（個人開発・単一ユーザー運用）に合わせ、arc42の12章のうち関係する章のみを採用する。

## 1. 序論と目標

Grok Imagineの生成物を手動取り込みし、Fanvueへの投稿とXへの紹介投稿を半自動化する。詳細は [CLAUDE_HANDOFF.md](../CLAUDE_HANDOFF.md) 1章を参照。

## 2. 制約

- Imagine API・X公式APIは使用しない
- ヘッドレスでのX初回ログインは不可
- 失敗ジョブの自動無限リトライは行わない
- 秘密情報は `.env` のみ

詳細: CLAUDE_HANDOFF.md 2章・3章。

## 3. コンテキストと範囲

アクター: ユーザー、reelmillyワーカー、Fanvue、X。詳細はCLAUDE_HANDOFF.md 3章・4章のシステム概要図を参照。

## 4. 解決戦略

| 決定 | 採用内容 | 根拠 |
|---|---|---|
| 状態管理 | JSONファイル（`state.json` / `events.jsonl`）、DBは使わない | [ADR-0004](adr/0004-json-based-state-store.md) |
| 通知・操作 | Telegram Botに一本化 | [ADR-0001](adr/0001-notification-channel-telegram.md) |
| X投稿 | Playwright非公式操作 | [ADR-0002](adr/0002-x-posting-via-playwright.md) |
| Fanvue投稿 | 公式REST API | [ADR-0003](adr/0003-fanvue-official-api.md) |
| プラットフォーム別コンテンツルール強制 | `content_rating` + `platform_content_rules`（別プロジェクトCREAMからの部分移植、多プラットフォーム対応の布石） | [ADR-0006](adr/0006-platform-content-rules-from-cream.md) |

## 5. 構成要素の視点

```
reelmilly/
  data/library/{inbox,ready,posted,x_only}/
  data/profiles/x/
  data/screenshots/
  data/state/{state.json,events.jsonl}
  templates/
  src/
```

詳細はCLAUDE_HANDOFF.md 5章のディレクトリ契約を参照。

## 6. ランタイムビュー

主要フロー（drop、x_teaser、x_engagement）と部分失敗時の挙動はCLAUDE_HANDOFF.md 4章・6章を参照。

## 7. 配置ビュー

**未確定**。デプロイ先（AWS EC2 / ローカルPC）は [ADR-0005](adr/0005-deployment-environment.md) で判断中。Playwrightの永続ログインセッション維持という制約上、GUI常時起動環境が前提になる可能性が高い。

## 8. 横断的関心事

- タイムゾーン: Asia/Tokyo固定
- 秘密情報管理: `.env`のみ、リポジトリにコミットしない
- ログ: `events.jsonl`への監査ログ、失敗時は`data/screenshots/`にスクリーンショット保存
- コンテンツルール: プラットフォームごとの許可コンテンツ区分を`config.yaml`で強制（[ADR-0006](adr/0006-platform-content-rules-from-cream.md)）
- コンプライアンス確認（AI生成の明示・年齢表現）: システムでの自動判定・記録は行わず、投稿前に運用者が毎回目視確認する運用ルールとする（[TODO.md](../TODO.md)参照、将来再検討の余地あり）

## 9. アーキテクチャ決定

[docs/adr/](adr) を参照。

## 10. 品質要求

- 部分失敗時にFanvueへの二重投稿が発生しないこと
- X未ログイン時に投稿せず安全に停止すること
- 同一ジョブが同一カレンダー日に二重実行されないこと

## 11. リスクと技術的負債

CLAUDE_HANDOFF.md 13章を参照（X DOM変更、Fanvueメディア処理遅延、公開URLテンプレ誤り、同一文面連投によるX制限等）。

## 12. 用語集

| 用語 | 意味 |
|---|---|
| drop | Fanvueへの本編投稿＋X紹介投稿のジョブ |
| x_teaser | Fanvueを通さないXのみのティーザー投稿 |
| x_engagement | テキストのみのXエンゲージメント投稿 |
| ready | ingest済み・投稿待ちの素材状態 |
