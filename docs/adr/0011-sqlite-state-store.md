# 0011. 状態管理をJSONからSQLiteへ移行する

- ステータス: 決定（[ADR-0004](0004-json-based-state-store.md)を置き換える）
- 決定日: 2026-09-25
- 決定者: プロジェクトオーナー

## コンテキストと課題

[ADR-0004](0004-json-based-state-store.md)では、単一ユーザー・低頻度ジョブという想定規模のもと、状態管理をJSONファイル（`state.json`）とし、DBを使わないと決定した。

[ADR-0010](0010-custom-asset-management-over-eagle.md)で画像・動画管理をReelMilly自身の機能として実装することを決定したが、実際の運用では数万件規模の画像・動画を扱う見込みであることが判明した。またUIの初期スコープとして、フォルダ・タグ管理（多対多の関係）を含むことが決まっている（[ADR-0010](0010-custom-asset-management-over-eagle.md)）。

単一のJSONファイルに数万件のアイテムを保持する設計は、読み書きのたびに全体をパース・書き換える必要があり、性能・排他制御の両面で現実的でない。

## 検討した選択肢

- JSONファイルのまま継続する（[ADR-0004](0004-json-based-state-store.md)の維持）
- ドキュメントDB（MongoDB等）を導入する
- SQLiteを導入する

## 決定

SQLiteを採用する。ローカルPC単体運用・単一ユーザーという前提（[ADR-0005](0005-deployment-environment.md)検討中）では、サーバープロセスの常駐が必要なドキュメントDBはオーバースペックであり、運用コスト（起動・監視・バックアップ手順）に見合わない。SQLiteはファイルベースで追加インフラが不要、Pythonの標準ライブラリ`sqlite3`で完結し、フォルダ・タグのような多対多関係もリレーショナル設計で自然に表現できる。可変フィールド（`nsfw_auto_confidence`等）が必要な場合はSQLiteのJSON1拡張で対応する。

**メタデータの正（source of truth）はSQLiteとする。** 既存の`meta.yaml`（1アイテム1フォルダにメタデータを書く方式）は廃止する。画像・動画の実ファイル本体は引き続きファイルシステム（`data/library/`配下）に置き、SQLiteはファイルパスを含むメタデータ・状態・タグ・フォルダ構造を保持する。

監査ログ（`events.jsonl`）は対象外とする。追記のみ・低頻度参照というログの性質上、JSONLのままで問題ない。

### スキーマ概要（Phase 1で確定）

```sql
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,              -- ready | approved | posted | failed_fanvue
    kind TEXT NOT NULL,                -- image | video
    file_path TEXT NOT NULL,
    caption TEXT,
    x_caption TEXT,
    fanvue_text TEXT,
    audience TEXT,
    price_cents INTEGER,
    fanvue_url TEXT,
    fanvue_uuid TEXT,
    x_ok INTEGER DEFAULT 0,
    content_rating TEXT,               -- sfw | suggestive | explicit | null
    content_rating_confirmed INTEGER DEFAULT 0,
    nsfw_auto_rating TEXT,
    nsfw_auto_confidence REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE channels (                -- 投稿先(fanvue/x/将来instagram等)を多対多で保持
    asset_id TEXT NOT NULL REFERENCES assets(id),
    channel TEXT NOT NULL,
    PRIMARY KEY (asset_id, channel)
);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE asset_tags (
    asset_id TEXT NOT NULL REFERENCES assets(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES folders(id)
);

CREATE TABLE asset_folders (
    asset_id TEXT NOT NULL REFERENCES assets(id),
    folder_id INTEGER NOT NULL REFERENCES folders(id),
    PRIMARY KEY (asset_id, folder_id)
);
```

`status` / `content_rating` / `created_at`にはインデックスを張り、ジョブ実行時の絞り込み（例: `content_rating_confirmed=1 AND status='ready'`）を高速化する。

## 結果

### 良い影響

- 数万件規模でも実用的な読み書き性能・検索性能を確保できる
- フォルダ・タグの多対多関係を標準的なリレーショナル設計で表現でき、UI実装（一覧・絞り込み）が素直になる
- 追加インフラ不要で、[ADR-0005](0005-deployment-environment.md)のローカルPC運用方針と両立する

### 悪い影響・トレードオフ

- `meta.yaml`という「フォルダを見れば内容が分かる」目視確認のしやすさは失われる。確認にはDBビューアまたはReelMilly自身のUI/CLIが必要になる
- スキーマ変更（マイグレーション）の管理が必要になる。ツール（`alembic`等）の導入要否は実装時に検討する
- 既存の引き継ぎ文書（[CLAUDE_HANDOFF.md](../../CLAUDE_HANDOFF.md)）の`meta.yaml`前提の記述とは齟齬が生じるため、実装時はこのADRとADR-0010を正とする
