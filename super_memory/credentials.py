import json
import os
from dataclasses import dataclass
from pathlib import Path

CREDS_DIR = Path(__file__).resolve().parent.parent / "data" / "credentials"
CREDS_FILE = CREDS_DIR / "oauth.json"


@dataclass
class Session:
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None


def save(session: Session) -> None:
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_raw()
    data["anthropic"] = {
        "access": session.access_token,
        "refresh": session.refresh_token,
        "expires": session.expires_at,
    }
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CREDS_FILE, 0o600)


def load() -> Session | None:
    data = _load_raw()
    entry = data.get("anthropic")
    if not entry or not entry.get("access"):
        return None
    return Session(
        access_token=entry["access"],
        refresh_token=entry.get("refresh"),
        expires_at=entry.get("expires"),
    )


def clear() -> None:
    try:
        CREDS_FILE.unlink()
    except FileNotFoundError:
        pass


def _load_raw() -> dict:
    try:
        return json.loads(CREDS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
