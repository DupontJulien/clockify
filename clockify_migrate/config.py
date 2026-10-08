import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = "COLLE_TA_CLE_API_ICI"


@dataclass
class Config:
    api_key: str
    workspace_id: str
    timezone: str
    old_email: str
    date_order: str
    send_billable: bool
    state_file: Path
    default_project: str


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"{path} introuvable (copie config.example.toml en config.toml)")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cf, imp = data.get("clockify", {}), data.get("import", {})
    key = os.getenv("CLOCKIFY_API_KEY") or cf.get("api_key", "")
    return Config(
        api_key="" if key == PLACEHOLDER else key,
        workspace_id=cf.get("workspace_id", ""),
        timezone=imp.get("timezone", "Europe/Zurich"),
        old_email=imp.get("old_email", "").lower(),
        date_order=imp.get("date_order", "auto"),
        send_billable=imp.get("send_billable", True),
        state_file=path.parent / imp.get("state_file", "imported.log"),
        default_project=imp.get("default_project", ""),
    )
