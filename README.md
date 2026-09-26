# Reelmilly

画像・動画（Grok Imagine生成物）を管理する本体アプリケーションを優先実装し、その上にFanvueへの本編投稿とX（旧Twitter）への紹介投稿を半自動化する機能を論理プラグインとして付加する（[ADR-0007](docs/adr/0007-asset-management-as-core.md)、[ADR-0013](docs/adr/0013-sns-posting-as-logical-plugin.md)）。操作は本体UIを主とし、Telegram Botは通知と簡易操作（承認・スキップ）の補助チャネルとして使う（[ADR-0012](docs/adr/0012-primary-ui-with-telegram-as-secondary.md)）。

## ステータス

要件整理・アーキテクチャ設計フェーズ（実装未着手）。進捗は [docs/roadmap.md](docs/roadmap.md) を参照。

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

## セットアップ

Phase 0実装後に追記予定。
