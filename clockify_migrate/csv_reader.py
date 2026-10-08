"""Read Clockify 'Detailed report' CSV exports (EN / FR / DE headers)."""
import csv
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ALIASES = {
    "project":     ["project", "projet", "projekt"],
    "client":      ["client", "kunde"],
    "description": ["description", "beschreibung"],
    "task":        ["task", "tache", "aufgabe"],
    "email":       ["email", "e-mail", "courriel"],
    "tags":        ["tags", "tag", "etiquettes", "etiquette"],
    "billable":    ["billable", "facturable", "abrechenbar"],
    "start_date":  ["start date", "date de debut", "startdatum"],
    "start_time":  ["start time", "heure de debut", "startzeit"],
    "end_date":    ["end date", "date de fin", "enddatum"],
    "end_time":    ["end time", "heure de fin", "endzeit"],
    "duration":    ["duration (h)", "duree (h)", "dauer (h)"],
}
YES = {"yes", "oui", "ja", "true", "1"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.strip().lower().split())


@dataclass
class Entry:
    start: datetime          # local, naive
    end: datetime            # local, naive
    project: str
    client: str
    task: str
    description: str
    tags: list[str]
    billable: bool | None
    email: str

    def key(self) -> str:
        raw = "|".join([self.start.isoformat(), self.end.isoformat(), self.client,
                        self.project, self.task, self.description])
        return hashlib.sha1(raw.encode()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:5000]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    colmap: dict[str, str] = {}
    for header in reader.fieldnames or []:
        h = norm(header)
        for key, names in ALIASES.items():
            if h in names and key not in colmap:
                colmap[key] = header
    missing = [k for k in ("start_date", "start_time") if k not in colmap]
    if missing or ("end_time" not in colmap and "duration" not in colmap):
        raise ValueError(f"{path.name}: colonnes manquantes. En-têtes trouvés : {reader.fieldnames}")
    return [{k: (r.get(col) or "").strip() for k, col in colmap.items()} for r in reader]


def detect_date_order(dates) -> str:
    for d in dates:
        p = d.replace(".", "/").replace("-", "/").split("/")
        if len(p) != 3:
            continue
        if len(p[0]) == 4:
            return "YMD"
        if int(p[0]) > 12:
            return "DMY"
        if int(p[1]) > 12:
            return "MDY"
    return "DMY"


def parse_date(s: str, order: str) -> datetime:
    p = s.replace(".", "/").replace("-", "/").split("/")
    y, m, d = {"YMD": (p[0], p[1], p[2]), "MDY": (p[2], p[0], p[1])}.get(order, (p[2], p[1], p[0]))
    y = int(y) + (2000 if int(y) < 100 else 0)
    return datetime(y, int(m), int(d))


def parse_time(s: str):
    s = s.strip().upper()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"format d'heure inconnu : {s!r}")


def parse_duration(s: str) -> timedelta:
    h, m, sec = (s.split(":") + ["0", "0"])[:3]
    return timedelta(hours=int(h), minutes=int(m), seconds=int(sec or 0))


def to_entry(r: dict, order: str) -> Entry:
    start = datetime.combine(parse_date(r["start_date"], order), parse_time(r["start_time"]))
    if r.get("end_time"):
        day = parse_date(r["end_date"], order) if r.get("end_date") else start
        end = datetime.combine(day, parse_time(r["end_time"]))
    else:
        end = start + parse_duration(r["duration"])
    if end < start:
        end += timedelta(days=1)
    b = r.get("billable")
    return Entry(
        start=start, end=end,
        project=r.get("project", ""), client=r.get("client", ""), task=r.get("task", ""),
        description=r.get("description", ""),
        tags=[t.strip() for t in r.get("tags", "").split(",") if t.strip()],
        billable=(norm(b) in YES) if b else None,
        email=r.get("email", "").lower(),
    )
