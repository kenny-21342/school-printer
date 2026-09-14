"""Configuration via environment / .env file."""
from __future__ import annotations

import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from .filetypes import CONVERTIBLE_MIMES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SP_", extra="ignore")

    # --- server ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # --- paths ---
    # The iMac is the only host now; these match deploy/com.school.printer.plist.
    base_dir: Path = Path("/usr/local/school-printer")
    data_dir: Path = Path("/usr/local/school-printer/data")
    spool_dir: Path = Path("/usr/local/school-printer/spool")
    db_path: Path = Path("/usr/local/school-printer/data/printers.db")

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
    # Word, Excel and PowerPoint are in the list because the API now runs on the
    # iMac that has Office installed: they are accepted, converted to PDF
    # locally (office.py) and then printed. Formats that cannot be converted --
    # Pages, Numbers, Keynote -- stay out, and main.py rejects them with a
    # message telling the student to export a PDF.
    allowed_mimes: list[str] = [
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/plain",
        *sorted(CONVERTIBLE_MIMES),
    ]

    # --- Office -> PDF conversion ---
    # Turn off to refuse Office uploads up front (e.g. while Office is being
    # reinstalled) instead of accepting jobs that will fail one by one.
    office_conversion_enabled: bool = True
    # Override the osascript binary (for tests). Empty = auto-discover.
    office_convert_binary: str = ""
    # Per-document ceiling. Word can take tens of seconds on a large document;
    # anything past this is a hung GUI app and the job is failed.
    office_convert_timeout_s: int = 180
    # How often the conversion worker looks for new work.
    conversion_poll_interval_s: int = 5
    # A claim older than this is treated as abandoned (the process crashed
    # mid-conversion) and the job is picked up again.
    conversion_claim_stale_s: int = 600

    # --- spool retention ---
    # Delete spool files this many hours after their job reaches a terminal
    # state. Without this the spool directory grows without bound. 0 disables
    # the reaper.
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
