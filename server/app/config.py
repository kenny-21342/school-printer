"""Configuration via environment / .env file."""
from __future__ import annotations

import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SP_", extra="ignore")

    # --- server ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # --- paths ---
    base_dir: Path = Path("/opt/school-printer")
    data_dir: Path = Path("/opt/school-printer/data")
    spool_dir: Path = Path("/opt/school-printer/spool")
    db_path: Path = Path("/opt/school-printer/data/printers.db")

    # --- auth ---
    # Auto-generated on first run if unset; persisted to data_dir/secret_key
    secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days

    # --- printing ---
    cups_queue: str = "bw"
    # Override the lp binary path (for tests). Empty = auto-discover.
    lp_binary: str = ""
    # Max upload size in MB. Keep in sync with deploy/env.example.
    max_upload_mb: int = 50
    # Max scaling percentage accepted for the `scaling` print option.
    max_scaling_percent: int = 400
    # Allowed MIME types (empty list = allow all).
    #
    # Office formats are deliberately absent: the Pi cannot convert them and the
    # iMac conversion relay does not exist yet. They are rejected up front with
    # a clear message rather than being accepted and silently never printed.
    # See OFFICE_MIMES in pdftools.py and the 415 branch in main.py.
    allowed_mimes: list[str] = [
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/plain",
    ]

    # --- spool retention ---
    # Delete spool files this many hours after their job reaches a terminal
    # state. Without this the Pi's SD card fills up. 0 disables the reaper.
    spool_retention_hours: int = 24
    # How often the background reaper runs, in seconds.
    spool_sweep_interval_s: int = 900

    # --- admin bootstrap ---
    # On first run, create this admin user if it doesn't exist
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_pass: str = ""  # MUST be set via env on first boot

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.spool_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Persist secret key
        key_file = self.data_dir / "secret_key"
        if not self.secret_key:
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)
                key_file.chmod(0o600)


settings = Settings()  # type: ignore[call-arg]
