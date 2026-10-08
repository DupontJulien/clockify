"""Minimal Clockify REST client with get-or-create caches."""
import time

import requests

API = "https://api.clockify.me/api/v1"


class ClockifyError(RuntimeError):
    pass


class Clockify:
    def __init__(self, api_key: str, workspace_id: str = "", dry_run: bool = False, log=print):
        self.dry, self.log, self._fake = dry_run, log, 0
        self.s = requests.Session()
        self.s.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
        self.ws = workspace_id
        self.clients: dict[str, str] = {}
        self.projects: dict[tuple[str, str], str] = {}
        self.tags: dict[str, str] = {}
        self.tasks: dict[str, dict[str, str]] = {}

    # -- low level ---------------------------------------------------------
    def req(self, method: str, path: str, **kw):
        for attempt in range(7):
            r = self.s.request(method, API + path, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code == 401:
                raise ClockifyError("Clé API refusée (401). Vérifie api_key dans config.toml.")
            if not r.ok:
                raise ClockifyError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            time.sleep(0.03)  # API limit: 50 req/s
            return r.json() if r.text else None
        raise ClockifyError(f"{method} {path}: trop de tentatives")

    def get_all(self, path: str, params: dict | None = None) -> list:
        out, page = [], 1
        while True:
            batch = self.req("GET", path, params={**(params or {}), "page": page, "page-size": 200})
            out += batch
            if len(batch) < 200:
                return out
            page += 1

    def post(self, path: str, body: dict) -> dict:
        if self.dry:
            self._fake += 1
            return {"id": f"dry-{self._fake}"}
        return self.req("POST", path, json=body)

    # -- account -----------------------------------------------------------
    def user(self) -> dict:
        return self.req("GET", "/user")

    def workspaces(self) -> list:
        return self.req("GET", "/workspaces")

    def load(self):
        """Resolve workspace and load existing clients/projects/tags."""
        if self.dry:
            self.ws = self.ws or "dry-run"
            return
        if not self.ws:
            self.ws = self.user()["activeWorkspace"]
        w = f"/workspaces/{self.ws}"
        self.clients = {c["name"]: c["id"] for c in self.get_all(f"{w}/clients")}
        id2c = {v: k for k, v in self.clients.items()}
        self.projects = {(id2c.get(p.get("clientId"), ""), p["name"]): p["id"]
                         for p in self.get_all(f"{w}/projects")}
        self.tags = {t["name"]: t["id"] for t in self.get_all(f"{w}/tags")}

    # -- get or create -----------------------------------------------------
    def client_id(self, name: str):
        if not name:
            return None
        if name not in self.clients:
            self.clients[name] = self.post(f"/workspaces/{self.ws}/clients", {"name": name})["id"]
            self.log(f"[green]+ client[/]  {name}")
        return self.clients[name]

    def project_id(self, name: str, client: str):
        if not name:
            return None
        key = (client or "", name)
        if key not in self.projects:
            body = {"name": name, "isPublic": True}  # private projects are a paid feature
            if cid := self.client_id(client):
                body["clientId"] = cid
            self.projects[key] = self.post(f"/workspaces/{self.ws}/projects", body)["id"]
            self.log(f"[green]+ projet[/]  {name}" + (f" [dim]({client})[/]" if client else ""))
        return self.projects[key]

    def task_id(self, name: str, project_id):
        if not name or not project_id:
            return None
        if project_id not in self.tasks:
            self.tasks[project_id] = {} if project_id.startswith("dry-") else {
                t["name"]: t["id"]
                for t in self.get_all(f"/workspaces/{self.ws}/projects/{project_id}/tasks")}
        if name not in self.tasks[project_id]:
            self.tasks[project_id][name] = self.post(
                f"/workspaces/{self.ws}/projects/{project_id}/tasks", {"name": name})["id"]
            self.log(f"[green]+ tâche[/]   {name}")
        return self.tasks[project_id][name]

    def tag_ids(self, names: list[str]) -> list[str]:
        ids = []
        for name in names:
            if name not in self.tags:
                self.tags[name] = self.post(f"/workspaces/{self.ws}/tags", {"name": name})["id"]
                self.log(f"[green]+ tag[/]     {name}")
            ids.append(self.tags[name])
        return ids

    def time_entries(self, start_utc: str | None = None, end_utc: str | None = None) -> list:
        """All time entries of the current user in the workspace (optionally within a range)."""
        uid = self.user()["id"]
        params = {}
        if start_utc:
            params["start"] = start_utc
        if end_utc:
            params["end"] = end_utc
        return self.get_all(f"/workspaces/{self.ws}/user/{uid}/time-entries", params)

    def add_time_entry(self, body: dict) -> dict:
        return self.post(f"/workspaces/{self.ws}/time-entries", body)
