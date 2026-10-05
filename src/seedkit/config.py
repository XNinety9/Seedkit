from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qbit_url: str = Field(description="qBittorrent WebUI URL, e.g. http://seedbox:8080")
    qbit_api_key: str | None = None
    qbit_username: str | None = None
    qbit_password: str | None = None
    qbit_verify_tls: bool = True

    seedkit_db: Path = Path("data/seedkit.db")
    seedkit_cleanup_rules: Path = Path("data/cleanup-rules.yaml")
    seedkit_interval_minutes: int = 10
    seedkit_host: str = "0.0.0.0"
    seedkit_port: int = 8337
    # Interface language: "fr", "en", or empty to follow the browser (web) / the system locale (CLI, TUI)
    seedkit_lang: str | None = None

    # Safety locks: everything that writes to qBittorrent is off unless explicitly enabled.
    seedkit_allow_actions: bool = False  # reannounce, recheck, tags
    seedkit_allow_delete: bool = False  # torrent deletion from the cleanup screen
    seedkit_auto_tags: bool = False  # periodically tag torrents with their H&R status
    seedkit_tag_prefix: str = "sk:"

    # Snapshot retention: full resolution, then hourly, then daily.
    seedkit_retention_full_days: int = 7
    seedkit_retention_hourly_days: int = 90

    # Notifications (disabled when empty).
    seedkit_ntfy_url: str | None = None  # e.g. https://ntfy.sh/my-seedbox
    seedkit_ntfy_token: str | None = None
    seedkit_discord_webhook: str | None = None
    seedkit_summary_hour: int | None = 9  # local hour of the daily summary, empty to disable

    # Optional SMB access to the seedbox files (read-only).
    smb_server: str | None = None
    smb_port: int = 445
    smb_username: str | None = None
    smb_password: str | None = None
    # qBittorrent path → SMB path, separated by ";" e.g. "/data/torrents=//seedbox/torrents"
    seedkit_path_map: str = ""
    seedkit_smb_ignore: str = ".DS_Store,Thumbs.db,desktop.ini,@eaDir,#recycle,.Trash*"

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.seedkit_ntfy_url or self.seedkit_discord_webhook)

    @property
    def smb_enabled(self) -> bool:
        return bool(self.smb_server and self.seedkit_path_map)


@lru_cache
def get_settings() -> Settings:
    return Settings()
