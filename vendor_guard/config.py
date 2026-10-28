"""Settings from environment variables. Defaults work for a local demo."""
import os
from dataclasses import dataclass, field


def _list(name, default):
    raw = os.environ.get(name, default)
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    home: str = field(default_factory=lambda: os.environ.get("VENDOR_GUARD_HOME", "var"))
    # Base URL people click in approval emails
    public_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_URL", "http://localhost:8100"))
    stammdaten_url: str = field(default_factory=lambda: os.environ.get("STAMMDATEN_URL", "http://localhost:8000"))
    stammdaten_key: str = field(default_factory=lambda: os.environ.get("STAMMDATEN_API_KEY", ""))
    # Key for n8n calling this service. Empty means no check (local only).
    api_key: str = field(default_factory=lambda: os.environ.get("VENDOR_GUARD_API_KEY", ""))
    # Signs approval links. Must be set to something long and random outside a demo.
    link_secret: str = field(default_factory=lambda: os.environ.get("LINK_SECRET", "change-me-link-secret"))
    # Signs the audit log. Keep it away from the database, e.g. in a secret store.
    audit_key: str = field(default_factory=lambda: os.environ.get("AUDIT_KEY", "change-me-audit-key"))
    approvers: list = field(default_factory=lambda: _list(
        "APPROVERS", "leitung.buchhaltung@example.com,controlling@example.com,einkauf.leitung@example.com"))
    link_hours: int = field(default_factory=lambda: int(os.environ.get("LINK_HOURS", "48")))
    # Payment runs (F110) on these weekdays, 0 = Monday
    payment_run_weekdays: list = field(default_factory=lambda: [int(x) for x in _list("PAYMENT_RUN_WEEKDAYS", "1,4")])
    payment_run_warning_days: int = field(default_factory=lambda: int(os.environ.get("PAYMENT_RUN_WARNING_DAYS", "2")))
    sanctions_file: str = field(default_factory=lambda: os.environ.get("SANCTIONS_FILE", ""))
    vies: bool = field(default_factory=lambda: os.environ.get("VIES", "off").lower() == "on")
