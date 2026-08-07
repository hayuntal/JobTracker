"""Loading of config/*.yaml and environment settings."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_dotenv(path: Optional[Path] = None) -> None:
    """Minimal .env loader — avoids a dependency for one small job.

    Real environment variables always win, so GitHub Actions secrets override
    whatever happens to be in a local .env file.
    """
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class Company:
    name: str
    provider: str
    token: str = ""
    uid: str = ""            # Comeet needs a uid + token pair
    site: str = ""           # Workday needs tenant + site
    extra: Optional[Dict[str, Any]] = None


@dataclass
class Settings:
    callmebot_phone: str
    callmebot_apikey: str
    facebook_cookie: str
    digest_max_jobs: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            callmebot_phone=os.getenv("CALLMEBOT_PHONE", "").strip(),
            callmebot_apikey=os.getenv("CALLMEBOT_APIKEY", "").strip(),
            facebook_cookie=os.getenv("FACEBOOK_COOKIE", "").strip(),
            digest_max_jobs=int(os.getenv("DIGEST_MAX_JOBS", "12")),
        )

    @property
    def whatsapp_ready(self) -> bool:
        return bool(self.callmebot_phone and self.callmebot_apikey)


def load_companies() -> List[Company]:
    data = _load_yaml(CONFIG_DIR / "companies.yaml")
    out = []
    for entry in data.get("companies") or []:
        out.append(Company(
            name=entry["name"],
            provider=entry["provider"].lower(),
            token=str(entry.get("token", "")),
            uid=str(entry.get("uid", "")),
            site=str(entry.get("site", "")),
            extra=entry,
        ))
    return out


def load_search() -> Dict[str, Any]:
    return _load_yaml(CONFIG_DIR / "search.yaml")
