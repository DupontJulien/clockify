"""clockify-migrate CLI."""
import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from .api import Clockify, ClockifyError
from .config import load_config
from .csv_reader import detect_date_order, read_rows, to_entry
from .stats import GROUPS, Span, aggregate, from_api, hhmm, in_range

app = typer.Typer(help="Migre des exports CSV Clockify vers un autre compte via l'API.",
                  no_args_is_help=True, add_completion=False)
console = Console()


def utc(dt: datetime, tz: ZoneInfo) -> str:
    return dt.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise typer.BadParameter("aucun fichier CSV trouvé")
    return files


@app.command()
def check(config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Fichier de config")):
    """Teste la clé API et affiche le compte et ses workspaces."""
    cfg = load_config(config)
    if not cfg.api_key:
        console.print("[red]Pas de clé API dans config.toml[/]")
        raise typer.Exit(1)
    cf = Clockify(cfg.api_key)
    try:
        user, wss = cf.user(), cf.workspaces()
    except ClockifyError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"Connecté en tant que [bold]{user['name']}[/] <{user['email']}>")
    t = Table("Workspace", "ID", "Actif")
    for w in wss:
        t.add_row(w["name"], w["id"], "✓" if w["id"] == user["activeWorkspace"] else "")
    console.print(t)


@app.command()
def upload(
    paths: list[Path] = typer.Argument(..., exists=True, help="Fichier(s) CSV ou dossier(s) de CSV"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Fichier de config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Tout lire et simuler, sans rien envoyer"),
    resync: bool = typer.Option(False, "--resync",
                                help="Ignorer imported.log : comparer avec le compte et n'envoyer que ce qui manque"),
):
    """Importe les entrées de temps des CSV dans le compte configuré."""
    cfg = load_config(config)
    if not cfg.api_key and not dry_run:
        console.print("[red]Pas de clé API dans config.toml (voir README)[/]")
        raise typer.Exit(1)
    tz = ZoneInfo(cfg.timezone)
    Path("errors.csv").unlink(missing_ok=True)  # never leave errors from a previous run

    # 1. read everything first, so a broken file fails before anything is sent
    rows_by_file = {}
    for f in collect_files(paths):
        rows_by_file[f] = read_rows(f)
        console.print(f"[cyan]{f.name}[/] : {len(rows_by_file[f])} lignes")
    order = cfg.date_order
    if order == "auto":
        order = detect_date_order(r["start_date"] for rows in rows_by_file.values() for r in rows)
    console.print(f"Dates : {order} · Fuseau : {cfg.timezone} · Filtre e-mail : {cfg.old_email or '-'}"
                  + (" · [yellow]DRY RUN[/]" if dry_run else ""))

    # 2. connect and load what already exists in the target workspace
    cf = Clockify(cfg.api_key, cfg.workspace_id, dry_run=dry_run, log=console.print)
    try:
        cf.load()
    except ClockifyError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"Workspace cible : {cf.ws}\n")

    # 3. upload
    if resync:
        # trust the account, not the log: skip entries whose start/end already exist there
        remote = Counter((sp.start, sp.end) for sp in api_spans(cfg)[0])
        console.print(f"Resync : {sum(remote.values())} entrées déjà dans le compte\n")
        done = set()
    else:
        remote = Counter()
        done = set(cfg.state_file.read_text().split()) if cfg.state_file.exists() else set()
    stats = dict(imported=0, already=0, other_user=0)
    errors = []
    state = None if dry_run else open(cfg.state_file, "a")
    total = sum(len(r) for r in rows_by_file.values())
    with Progress(console=console) as progress:
        bar = progress.add_task("Import", total=total)
        for f, rows in rows_by_file.items():
            for line, r in enumerate(rows, start=2):
                progress.advance(bar)
                try:
                    e = to_entry(r, order)
                    if cfg.old_email and e.email and e.email != cfg.old_email:
                        stats["other_user"] += 1
                        continue
                    k = e.key()
                    if k in done:
                        stats["already"] += 1
                        continue
                    if remote[(e.start, e.end)] > 0:
                        remote[(e.start, e.end)] -= 1
                        done.add(k)
                        if state:
                            state.write(k + "\n")
                        stats["already"] += 1
                        continue
                    body = {"start": utc(e.start, tz), "end": utc(e.end, tz),
                            "description": e.description}
                    if not e.project and cfg.default_project:
                        e.project = cfg.default_project  # workspace requires a project
                    if pid := cf.project_id(e.project, e.client):
                        body["projectId"] = pid
                    if tid := cf.task_id(e.task, pid):
                        body["taskId"] = tid
                    if tags := cf.tag_ids(e.tags):
                        body["tagIds"] = tags
                    if cfg.send_billable and e.billable is not None:
                        body["billable"] = e.billable
                    cf.add_time_entry(body)
                    done.add(k)
                    if state:
                        state.write(k + "\n")
                        state.flush()
                    stats["imported"] += 1
                except Exception as ex:  # keep going, report at the end
                    errors.append([f.name, line, str(ex), r])
    if state:
        state.close()

    if errors:
        with open("errors.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["fichier", "ligne", "erreur", "donnees"])
            w.writerows(errors)
    verb = "à importer" if dry_run else "importées"
    console.print(f"\n[bold]{stats['imported']}[/] entrées {verb} · {stats['already']} déjà importées · "
                  f"{stats['other_user']} d'autres utilisateurs ignorées · "
                  + (f"[red]{len(errors)} erreurs (voir errors.csv)[/]" if errors else "0 erreur"))


DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]


def csv_spans(paths: list[Path], cfg) -> tuple[list[Span], int]:
    """Entries from CSV exports (email filter + duplicates across exports removed)."""
    rows_by_file = {f: read_rows(f) for f in collect_files(paths)}
    order = cfg.date_order
    if order == "auto":
        order = detect_date_order(r["start_date"] for rows in rows_by_file.values() for r in rows)
    spans, seen = [], set()
    for rows in rows_by_file.values():
        for r in rows:
            e = to_entry(r, order)
            if cfg.old_email and e.email and e.email != cfg.old_email:
                continue
            if (k := e.key()) in seen:      # same entry in overlapping exports
                continue
            seen.add(k)
            spans.append(Span(e.start, e.end, e.project, e.description))
    return spans, len(rows_by_file)


def api_spans(cfg, du: datetime | None = None, au: datetime | None = None) -> tuple[list[Span], str]:
    """Entries of the current user in the configured Clockify workspace."""
    if not cfg.api_key:
        console.print("[red]Pas de clé API dans config.toml[/]")
        raise typer.Exit(1)
    tz = ZoneInfo(cfg.timezone)
    cf = Clockify(cfg.api_key, cfg.workspace_id)
    try:
        cf.load()
        # widen by a day on each side, exact filtering is done locally
        s_utc = utc(du - timedelta(days=1), tz) if du else None
        e_utc = utc(au + timedelta(days=2), tz) if au else None
        items = cf.time_entries(s_utc, e_utc)
    except ClockifyError as ex:
        console.print(f"[red]{ex}[/]")
        raise typer.Exit(1)
    names = {pid: name for (_, name), pid in cf.projects.items()}
    return from_api(items, tz, names), cf.ws


@app.command()
def hours(
    paths: list[Path] = typer.Argument(None, exists=True,
                                       help="CSV ou dossier(s). Sans argument : lit le compte Clockify"),
    par: str = typer.Option("mois", "--par", "-p", help=f"Regroupement : {', '.join(GROUPS)}"),
    du: datetime = typer.Option(None, "--du", "--from", formats=DATE_FORMATS, help="Date de début (incluse)"),
    au: datetime = typer.Option(None, "--au", "--to", formats=DATE_FORMATS, help="Date de fin (incluse)"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Fichier de config"),
):
    """Total des heures : total, par année, mois, semaine, jour ou projet, sur une période."""
    if par not in GROUPS:
        raise typer.BadParameter(f"--par doit être parmi : {', '.join(GROUPS)}")
    cfg = load_config(config)
    d_from, d_to = (du.date() if du else None), (au.date() if au else None)

    if paths:  # ---- from CSV exports
        spans, n_files = csv_spans(paths, cfg)
        source = f"{n_files} fichier(s) CSV"
    else:      # ---- from the Clockify account
        if not cfg.api_key:
            console.print("[red]Pas de clé API dans config.toml[/]")
            raise typer.Exit(1)
        spans, ws = api_spans(cfg, du, au)
        source = f"compte Clockify (workspace {ws})"

    spans = in_range(spans, d_from, d_to)
    if not spans:
        console.print("Aucune entrée sur cette période.")
        raise typer.Exit()
    first, last = min(s.start for s in spans), max(s.start for s in spans)
    title = (f"Heures par {par} · {source}\n"
             f"du {(d_from or first.date()):%d.%m.%Y} au {(d_to or last.date()):%d.%m.%Y}")
    t = Table(title=title, title_justify="left")
    t.add_column({"total": "Période", "projet": "Projet"}.get(par, par.capitalize()))
    t.add_column("Heures", justify="right")
    t.add_column("h:mm", justify="right")
    t.add_column("Entrées", justify="right")
    rows = aggregate(spans, par)
    for label, h, n in rows:
        t.add_row(label, f"{h:,.2f}".replace(",", "'"), hhmm(h), str(n))
    if par != "total":
        tot = sum(r[1] for r in rows)
        t.add_section()
        t.add_row("[bold]Total[/]", f"[bold]{tot:,.2f}[/]".replace(",", "'"), f"[bold]{hhmm(tot)}[/]",
                  f"[bold]{len(spans)}[/]")
    console.print(t)


@app.command()
def compare(
    paths: list[Path] = typer.Argument(..., exists=True, help="CSV ou dossier(s) de référence"),
    du: datetime = typer.Option(None, "--du", "--from", formats=DATE_FORMATS, help="Date de début (incluse)"),
    au: datetime = typer.Option(None, "--au", "--to", formats=DATE_FORMATS, help="Date de fin (incluse)"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Fichier de config"),
):
    """Liste les entrées présentes dans les CSV mais absentes du compte (et l'inverse)."""
    cfg = load_config(config)
    d_from, d_to = (du.date() if du else None), (au.date() if au else None)
    local = in_range(csv_spans(paths, cfg)[0], d_from, d_to)
    remote = in_range(api_spans(cfg, du, au)[0], d_from, d_to)

    def key(s: Span):
        return (s.start, s.end)

    missing, extra = [], []
    pool = Counter(map(key, remote))
    for s in local:
        if pool[key(s)] > 0:
            pool[key(s)] -= 1
        else:
            missing.append(s)
    pool = Counter(map(key, local))
    for s in remote:
        if pool[key(s)] > 0:
            pool[key(s)] -= 1
        else:
            extra.append(s)

    def show(title, items, color):
        t = Table(title=f"{title} : {len(items)} entrée(s), {sum(i.hours for i in items):.2f} h",
                  title_justify="left", title_style=color)
        for c in ("Date", "Début", "Fin", "Heures", "Projet", "Description"):
            t.add_column(c)
        for i in sorted(items, key=lambda x: x.start):
            t.add_row(f"{i.start:%d.%m.%Y}", f"{i.start:%H:%M}", f"{i.end:%H:%M}", f"{i.hours:.2f}",
                      i.project or "-", (i.description or "")[:50])
        console.print(t)

    console.print(f"CSV : {len(local)} entrées · Compte : {len(remote)} entrées\n")
    if not missing and not extra:
        console.print("[green]Identique : toutes les entrées des CSV sont dans le compte.[/]")
        return
    if missing:
        show("Manquantes dans le compte", missing, "red")
    if extra:
        show("En plus dans le compte (absentes des CSV)", extra, "yellow")


if __name__ == "__main__":
    app()
