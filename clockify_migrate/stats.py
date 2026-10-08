"""Aggregate hours by period, from CSV entries or from the Clockify API."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc."]
GROUPS = ["total", "annee", "mois", "semaine", "jour", "projet"]


@dataclass
class Span:
    start: datetime   # local, naive
    end: datetime     # local, naive
    project: str
    description: str = ""

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def from_api(items: list, tz: ZoneInfo, project_names: dict[str, str]) -> list[Span]:
    spans = []
    for it in items:
        ti = it.get("timeInterval") or {}
        if not ti.get("end"):          # running timer
            continue
        s = datetime.fromisoformat(ti["start"].replace("Z", "+00:00")).astimezone(tz).replace(tzinfo=None)
        e = datetime.fromisoformat(ti["end"].replace("Z", "+00:00")).astimezone(tz).replace(tzinfo=None)
        spans.append(Span(s, e, project_names.get(it.get("projectId") or "", ""), it.get("description") or ""))
    return spans


def in_range(spans: list[Span], d_from: date | None, d_to: date | None) -> list[Span]:
    """Keep entries whose start day is within [d_from, d_to] (both inclusive)."""
    return [s for s in spans
            if (d_from is None or s.start.date() >= d_from)
            and (d_to is None or s.start.date() <= d_to)]


def group_key(s: Span, by: str):
    d = s.start
    if by == "annee":
        return (d.year,), f"{d.year}"
    if by == "mois":
        return (d.year, d.month), f"{MOIS[d.month - 1]} {d.year}"
    if by == "semaine":
        y, w, _ = d.isocalendar()
        monday = (d - timedelta(days=d.weekday())).date()
        return (y, w), f"{y}-S{w:02d} (lun. {monday:%d.%m})"
    if by == "jour":
        return (d.date(),), f"{d:%a %d.%m.%Y}"
    if by == "projet":
        name = s.project or "(sans projet)"
        return (name.lower(),), name
    return (0,), "Total"


def aggregate(spans: list[Span], by: str):
    """Return [(label, hours, count)] sorted by period (or by hours for 'projet')."""
    acc: dict = defaultdict(lambda: [None, 0.0, 0])
    for s in spans:
        k, label = group_key(s, by)
        acc[k][0] = label
        acc[k][1] += s.hours
        acc[k][2] += 1
    rows = [tuple(v) for _, v in sorted(acc.items())]
    if by == "projet":
        rows.sort(key=lambda r: -r[1])
    return rows


def hhmm(h: float) -> str:
    m = round(h * 60)
    return f"{m // 60}:{m % 60:02d}"
