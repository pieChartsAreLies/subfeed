"""Settings, read from the environment. No secret has a default."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_path: str = "/var/lib/subfeed/subfeed.db"
    token: str = ""
    scheduler: bool = True
    poll_limit: int = 15
    saved_dir: str = "/srv/saved"
    saved_quota_bytes: int = 250 * 1024**3
    save_worker: bool = True       # runs the save queue in this process
    canary: bool = True            # hourly canary extraction
    google_client_id: str = ""
    google_client_secret: str = ""
    google_token_path: str = "/var/lib/subfeed/google-token.json"  # CT-local, mode 0600, never in the DB/NAS backup
    save_backoff_s: tuple = (60.0, 300.0, 900.0)   # waits before bot-check retries 1..3

    @classmethod
    def from_env(cls) -> "Settings":
        def on(k: str) -> bool:
            return os.environ.get(k, "1") not in ("0", "false", "no")

        return cls(
            db_path=os.environ.get("SUBFEED_DB", "/var/lib/subfeed/subfeed.db"),
            token=os.environ.get("SUBFEED_API_TOKEN", ""),
            scheduler=on("SUBFEED_SCHEDULER"),
            saved_dir=os.environ.get("SUBFEED_SAVED_DIR", "/srv/saved"),
            save_worker=on("SUBFEED_SAVE_WORKER"),
            canary=on("SUBFEED_CANARY"),
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            google_token_path=os.environ.get("SUBFEED_GOOGLE_TOKEN", "/var/lib/subfeed/google-token.json"),
        )
