# TODO

大きめのタスク（Phase単位の実装、仕様確定が必要なもの）は GitHub Issues で管理する。ここには小粒タスク・決め忘れ防止用のメモを置く。

## 未確定事項（着手前に確定が必要）

- [ ] デプロイ環境（AWS EC2 vs ローカルPC）の決定 → `docs/adr/0005-deployment-environment.md`
- [ ] Fanvue handle と実際の投稿URL例1本の取得（`FANVUE_POST_URL_TEMPLATE`確定用）
- [ ] Telegram BotFatherでのBot作成（`ReelmillyBot`または空き名称）とtoken取得
- [ ] Fanvue API疎通確認（`GET /users/me`を実トークンで1回叩く）

## CREAM由来の検討事項

- [ ] `meta.yaml`に`content_rating`、`config.yaml`に`platform_content_rules`を追加（[ADR-0006](docs/adr/0006-platform-content-rules-from-cream.md)、Phase 1着手時）
- [ ] drop/x_teaser実行前にプラットフォーム別コンテンツルールを検証するロジックを追加（Phase 4着手時）
- [ ] **運用ルール（要順守）**: 投稿前のコンプライアンス確認（AI生成であることの明示・ペルソナが18歳未満に見えないことの確認）は、システム実装を見送り運用者が毎回目視で確認する。この運用ルールは省略しないこと
- [ ] 将来的にコンプライアンス確認の記録用ゲート（判定はしない、確認済みフラグの記録のみ）をシステム化するか、運用実績を見て再検討する

## アセット管理の中核化・NSFW自動仕分け（ADR-0007/0008）

- [ ] NSFW自動仕分けの実装方式の選定（ローカルモデル vs 外部API。精度・コスト・プライバシー・実行環境の制約を比較）
- [ ] `meta.yaml`に`content_rating_confirmed`・`nsfw_auto_rating`・`nsfw_auto_confidence`を追加（[ADR-0008](docs/adr/0008-nsfw-auto-triage-with-human-approval.md)、Phase 1着手時）
- [ ] Telegram承認フローに、自動仕分け結果の確認・補正コマンド（例: `/rate <id> explicit`）を追加（Phase 5着手時）
- [ ] drop/x_teaser実行前に`content_rating_confirmed == true`を検証するフィルタを追加（Phase 4着手時）

## 確定済みだが実装時に再確認するデフォルト値

- [ ] drop前承認の要否（デフォルト: 必須）
- [ ] X紹介文へのサムネ添付要否（デフォルト: なし）
- [ ] Fanvue投稿の価格デフォルト値（デフォルト: 499セント）
- [ ] Fanvue audience デフォルト（デフォルト: subscribers）

## 開発環境・品質まわり

- [ ] 静的解析ツール（ruff、mypy）の導入 ← 当初は導入見送り。忘れず後日対応する
- [ ] 静的解析導入後、`.github/workflows/ci.yml` にlintジョブを追加する
- [ ] `pyproject.toml` / `requirements.txt` の整備（Phase 0着手時）

## ドキュメント

- [ ] `docs/adr/0005-deployment-environment.md` をデプロイ環境確定後に更新する
- [ ] Phase 0完了後、README.mdにセットアップ手順を追記する
