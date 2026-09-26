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
