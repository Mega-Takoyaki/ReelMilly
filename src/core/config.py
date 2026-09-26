"""設定読込: config.yaml と .env をマージしてConfigを構築する。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Paths:
    root: Path
    inbox: Path
    ready: Path
    posted: Path
    x_only: Path
    state_dir: Path
    db_path: Path
    events_path: Path
    screenshots_dir: Path

    @classmethod
    def from_base(
        cls, base_dir: Path, library_root: str, state_dir: str, screenshots_dir: str
    ) -> "Paths":
        library = base_dir / library_root
        state = base_dir / state_dir
        return cls(
            root=library,
            inbox=library / "inbox",
            ready=library / "ready",
            posted=library / "posted",
            x_only=library / "x_only",
            state_dir=state,
            db_path=state / "reelmilly.db",
            events_path=state / "events.jsonl",
            screenshots_dir=base_dir / screenshots_dir,
        )

    def all_dirs(self) -> list[Path]:
        return [self.inbox, self.ready, self.posted, self.x_only, self.state_dir, self.screenshots_dir]


@dataclass(frozen=True)
class NsfwConfig:
    model: str
    threshold: float
    video_frame_interval_seconds: int


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int


@dataclass(frozen=True)
class Config:
    timezone: str
    paths: Paths
    nsfw: NsfwConfig
    platform_content_rules: dict[str, list[str]]
    platform_auto_post_ratings: dict[str, list[str]]
    web: WebConfig
    cadence: dict[str, str]
    env_path: Path


def load_config(base_dir: Path | None = None, config_filename: str = "config.yaml") -> Config:
    """base_dir/config.yaml と base_dir/.env を読み込みConfigを返す。"""
    base_dir = base_dir or Path.cwd()
    config_path = base_dir / config_filename
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    env_path = base_dir / ".env"
    load_dotenv(env_path)

    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    paths_raw = raw.get("paths", {})
    nsfw_raw = raw.get("nsfw", {})
    web_raw = raw.get("web", {})

    return Config(
        timezone=raw.get("timezone", "Asia/Tokyo"),
        paths=Paths.from_base(
            base_dir,
            paths_raw.get("library_root", "data/library"),
            paths_raw.get("state_dir", "data/state"),
            paths_raw.get("screenshots_dir", "data/screenshots"),
        ),
        nsfw=NsfwConfig(
            model=nsfw_raw.get("model", "marqo/nsfw-image-detection-384"),
            threshold=float(nsfw_raw.get("threshold", 0.5)),
            video_frame_interval_seconds=int(nsfw_raw.get("video_frame_interval_seconds", 2)),
        ),
        platform_content_rules=raw.get("platform_content_rules", {}),
        platform_auto_post_ratings=raw.get("platform_auto_post_ratings", {}),
        web=WebConfig(
            host=web_raw.get("host", "127.0.0.1"),
            port=int(web_raw.get("port", 8420)),
        ),
        cadence=raw.get("cadence", {}),
        env_path=env_path,
    )


def ensure_directories(config: Config) -> list[Path]:
    """ディレクトリ契約に従いディレクトリを作成する。新規作成したパスの一覧を返す。"""
    created = []
    for path in config.paths.all_dirs():
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return created
