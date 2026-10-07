-- ADR-0011: 状態管理はSQLiteを正とする

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_path TEXT NOT NULL,
    caption TEXT,
    x_caption TEXT,
    fanvue_text TEXT,
    audience TEXT,
    price_cents INTEGER,
    fanvue_url TEXT,
    fanvue_uuid TEXT,
    x_ok INTEGER NOT NULL DEFAULT 0,
    content_rating TEXT,
    content_rating_confirmed INTEGER NOT NULL DEFAULT 0,
    nsfw_auto_rating TEXT,
    nsfw_auto_confidence REAL,
    content_description TEXT,
    fanvue_caption_draft TEXT,
    analysis_error TEXT,
    width INTEGER,   -- 画像・動画の幅と高さ(一覧の「フル」表示で、縦横比どおりの枠を先に確保する)
    height INTEGER,
    is_broken INTEGER NOT NULL DEFAULT 0,  -- 破綻画像(AI生成特有の崩れ。キメラ)。既定の一覧・投稿から除外する
    original_name TEXT,  -- 取り込み時の元のファイル名(表示・検索・ダウンロード用)。ディスク上は<ID>.<拡張子>
    wm_path TEXT,      -- 透かし入りファイルのパス(NULL=透かし無し)。元のfile_pathは変えない
    wm_text TEXT,
    wm_position TEXT,
    deleted_at TEXT,  -- ごみ箱に入れた日時(NULL=通常)。ファイルは移動せず、一覧に出さないだけ
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_content_rating ON assets(content_rating);
CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at);

CREATE TABLE IF NOT EXISTS channels (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    PRIMARY KEY (asset_id, channel)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS asset_folders (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    PRIMARY KEY (asset_id, folder_id)
);

-- CLAUDE_HANDOFF.md 6章: 同じカレンダー日に同じjobを二度走らせないためのlast_run記録
CREATE TABLE IF NOT EXISTS job_runs (
    job_name TEXT PRIMARY KEY,
    last_run_date TEXT NOT NULL  -- YYYY-MM-DD (Asia/Tokyo基準)
);

-- ADR-0015: システムプロンプト等、本体UIの設定画面から調整可能な値を保持する
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 投稿状態(作品の準備状態assets.statusとは別の軸)。作品 x 投稿先(channel)ごとに1行。
-- 投稿先が増えても行が増えるだけで済む。行が無い=未投稿
CREATE TABLE IF NOT EXISTS posts (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,       -- 'fanvue' | 'x' | ...
    status TEXT NOT NULL,        -- 'posted' | 'failed'
    url TEXT,
    external_id TEXT,
    error TEXT,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_posts_channel_status ON posts(channel, status);

-- AI処理(sfw/nsfw判定・説明文/タグ生成)の非同期キュー。ワーカーが順に処理する
CREATE TABLE IF NOT EXISTS ai_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,      -- 'nsfw' | 'describe'
    status TEXT NOT NULL,    -- 'queued' | 'running' | 'done' | 'failed'
    error TEXT,
    params TEXT,             -- タスクの設定(JSON)。透かしの文字・位置など
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_tasks_status ON ai_tasks(status);
CREATE INDEX IF NOT EXISTS idx_ai_tasks_asset ON ai_tasks(asset_id, kind);
