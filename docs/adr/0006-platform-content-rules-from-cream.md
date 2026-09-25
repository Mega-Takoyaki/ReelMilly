# 0006. プラットフォーム別コンテンツルール強制ロジックの導入（CREAM由来）

- ステータス: 決定
- 決定日: 2026-09-25
- 決定者: プロジェクトオーナー

## コンテキストと課題

別プロジェクト「CREAM」（`docs/content-manager-design.md`、コンテンツ管理アプリの概要設計）を検討する過程で、投稿先プラットフォームごとに許可するコンテンツ区分を制約する仕組み（例: Instagramは`SFW`のみ許可）が有用と判断した。CREAM自体はReelMillyとは統合せず、ReelMilly側の設計思想（ADR/arc42運用、DBなしJSON状態管理、自動化重視）を正として、この一機能のみを移植する。

現時点でReelMilly は Fanvue と X のみを対象としており、Instagram等への投稿はスコープ外（[CLAUDE_HANDOFF.md](../../CLAUDE_HANDOFF.md) 参照）。今回導入するルール強制ロジックは、将来Instagram等を追加した際にすぐ使える布石として先行導入する。

なお、CREAM設計にある「コンプライアンス確認ゲート」（AI生成であることの明示確認・ペルソナが18歳未満に見えないことの確認）は今回の移植対象に**含めない**。システムによる自動判定はそもそも採用しない方針（人間が必ず確認する）で、記録用のゲート機構も現時点では実装せず、運用上の人的確認に委ねる。この点は [TODO.md](../../TODO.md) に運用ルールとして明記し、将来的にシステム的なゲート（記録のみ、判定はしない）を追加するかどうかは別途検討事項として残す。

## 検討した選択肢

- CREAMをそのまま独立プロジェクトとして残し、ReelMillyとはファイル連携のみ行う
- CREAMの機能（Asset管理・コンプライアンスゲート・KPI・収益シミュレーター一式）をReelMillyにまるごと統合する
- CREAMの設計のうち、プラットフォーム別ルール強制ロジックのみをReelMillyの`meta.yaml`/`config.yaml`に移植する

## 決定

3番目の選択肢を採用する。`meta.yaml`に`content_rating`フィールドを追加し、`config.yaml`に`platform_content_rules`としてプラットフォームごとの許可区分を定義する。ジョブ実行前（`drop` / `x_teaser`）にこのルールを検証し、違反時は投稿せずTelegramで理由を通知する。

### meta.yaml拡張案

```yaml
content_rating: sfw | suggestive | explicit   # CREAMのcontent_type相当を一般化
channels: [fanvue, x]                          # 将来 instagram 等を追加可能
```

### config.yaml拡張案

```yaml
platform_content_rules:
  fanvue: [sfw, suggestive, explicit]
  x: [sfw, suggestive, explicit]
  instagram: [sfw]     # 将来追加時のためのデフォルト値
```

## 結果

### 良い影響

- 将来Instagram等を追加する際、検証ロジックをそのまま流用できる
- 現時点ではFanvue/Xともに全区分許可のため、既存の投稿フローに実質的な制約は発生しない

### 悪い影響・トレードオフ

- 現時点で使われないルールを先行実装するため、多少のオーバーエンジニアリングになる（ただし`config.yaml`の設定変更のみで有効化できる範囲に留め、コードの複雑化は最小限にする）
- コンプライアンス確認ゲートを移植しないため、AI明示・年齢表現確認は引き続き完全に運用者の裁量に依存する。将来的にリスクが顕在化した場合は再検討する（[TODO.md](../../TODO.md)参照）
