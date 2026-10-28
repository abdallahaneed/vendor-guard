"""Settings from environment variables. Defaults work for a local demo, except the two secrets."""
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
    # Signs approval links. No default: a known key would let anyone forge a link.
    link_secret: str = field(default_factory=lambda: os.environ.get("LINK_SECRET", ""))
    # Signs the audit log. Keep it away from the database, e.g. in a secret store.
    audit_key: str = field(default_factory=lambda: os.environ.get("AUDIT_KEY", ""))
    approvers: list = field(default_factory=lambda: _list(
        "APPROVERS", "leitung.buchhaltung@example.com,controlling@example.com,einkauf.leitung@example.com"))
    link_hours: int = field(default_factory=lambda: int(os.environ.get("LINK_HOURS", "48")))
    # Payment runs (F110) on these weekdays, 0 = Monday
    payment_run_weekdays: list = field(default_factory=lambda: [int(x) for x in _list("PAYMENT_RUN_WEEKDAYS", "1,4")])
    payment_run_warning_days: int = field(default_factory=lambda: int(os.environ.get("PAYMENT_RUN_WARNING_DAYS", "2")))
    sanctions_file: str = field(default_factory=lambda: os.environ.get("SANCTIONS_FILE", ""))
    vies: bool = field(default_factory=lambda: os.environ.get("VIES", "off").lower() == "on")

    def check_secrets(self):
        """Refuse to run with a missing, short or example secret."""
        bad = [name for name, value in (("LINK_SECRET", self.link_secret), ("AUDIT_KEY", self.audit_key))
               if len(value) < 32 or value.startswith("change-me")]
        if bad:
            raise SystemExit(f"{' and '.join(bad)} must be set to at least 32 random characters. "
                             "Create one with: openssl rand -hex 32")
