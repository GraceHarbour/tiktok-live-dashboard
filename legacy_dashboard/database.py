from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ALIASES = {
    "creator_id": ("creator id", "creator_id", "id", "uid"),
    "handle": ("tiktok username", "username", "handle", "creator handle", "tiktok handle"),
    "creator_name": ("creator name", "name", "full name", "display name"),
    "manager_id": ("manager id", "manager_id"),
    "manager_name": ("manager", "manager name", "assigned manager", "applied under"),
    "manager_email": ("manager email", "email"),
    "goal_name": ("goal", "goal name", "goal type"),
    "goal_value": ("goal value", "target", "target value"),
    "application_status": ("application status", "status"),
    "application_date": ("application date", "applied date", "date applied"),
}


class CreatorNetworkDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS managers (
                manager_id TEXT PRIMARY KEY, manager_name TEXT NOT NULL, manager_email TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS creators (
                creator_id TEXT PRIMARY KEY, handle TEXT, creator_name TEXT, manager_id TEXT,
                manager_name TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY, creator_id TEXT, handle TEXT, manager_id TEXT,
                manager_name TEXT, goal_name TEXT, goal_value TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY, creator_id TEXT, handle TEXT, creator_name TEXT,
                manager_id TEXT, manager_name TEXT, application_status TEXT,
                application_date TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS business_essentials (
                id INTEGER PRIMARY KEY, creator_id TEXT, handle TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_snapshots (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, view_name TEXT,
                month TEXT, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dashboard_users (
                email TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member')),
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    def ensure_initial_owner(self, email: str | None) -> None:
        """Create the first owner once, from a deployment secret rather than source code."""
        if not email:
            return
        normalized = self._email(email)
        if self.connection.execute("SELECT 1 FROM dashboard_users LIMIT 1").fetchone():
            return
        now = self._now()
        self.connection.execute(
            "INSERT INTO dashboard_users (email, role, active, created_at, updated_at) VALUES (?, 'owner', 1, ?, ?)",
            (normalized, now, now),
        )
        self.connection.commit()

    def access_user(self, email: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM dashboard_users WHERE email = ?", (self._email(email),)).fetchone()
        return dict(row) if row else None

    def access_users(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM dashboard_users ORDER BY active DESC, role, email"
        )]

    def add_access_user(self, email: str, role: str = "member") -> dict[str, Any]:
        normalized = self._email(email)
        if role not in {"owner", "admin", "member"}:
            raise ValueError("Choose owner, administrator, or member access.")
        now = self._now()
        self.connection.execute(
            """INSERT INTO dashboard_users (email, role, active, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(email) DO UPDATE SET role=excluded.role, active=1, updated_at=excluded.updated_at""",
            (normalized, role, now, now),
        )
        self.connection.commit()
        return self.access_user(normalized) or {}

    def deactivate_access_user(self, email: str) -> None:
        normalized = self._email(email)
        target = self.access_user(normalized)
        if target is None:
            raise ValueError("That email does not have dashboard access.")
        if target["role"] == "owner" and target["active"]:
            active_owners = self.connection.execute(
                "SELECT count(*) FROM dashboard_users WHERE role = 'owner' AND active = 1"
            ).fetchone()[0]
            if active_owners <= 1:
                raise ValueError("Add another owner before removing the last owner.")
        self.connection.execute(
            "UPDATE dashboard_users SET active = 0, updated_at = ? WHERE email = ?", (self._now(), normalized)
        )
        self.connection.commit()

    def import_csv(self, kind: str, csv_file: Path) -> int:
        if not csv_file.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_file}")
        with csv_file.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        for row in rows:
            values = {field: self._value(row, field) for field in ALIASES}
            raw = json.dumps(row, ensure_ascii=False)
            if kind == "managers":
                manager_id = values["manager_id"] or values["manager_name"]
                if not manager_id or not values["manager_name"]:
                    raise ValueError("Managers CSV needs a Manager ID or Manager Name column.")
                self.connection.execute(
                    "INSERT OR REPLACE INTO managers VALUES (?, ?, ?, ?)",
                    (manager_id, values["manager_name"], values["manager_email"], raw),
                )
            elif kind == "creators":
                creator_id = self._creator_key(values)
                self.connection.execute(
                    "INSERT OR REPLACE INTO creators VALUES (?, ?, ?, ?, ?, ?)",
                    (creator_id, values["handle"], values["creator_name"], values["manager_id"], values["manager_name"], raw),
                )
            elif kind == "goals":
                self._insert_related("goals", values, raw)
            elif kind == "applications":
                self.connection.execute(
                    "INSERT INTO applications (creator_id, handle, creator_name, manager_id, manager_name, application_status, application_date, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (values["creator_id"], values["handle"], values["creator_name"], values["manager_id"], values["manager_name"], values["application_status"], values["application_date"], raw),
                )
            else:
                self.connection.execute(
                    "INSERT INTO business_essentials (creator_id, handle, raw_json) VALUES (?, ?, ?)",
                    (values["creator_id"], values["handle"], raw),
                )
        self.connection.commit()
        return len(rows)

    def get_creator(self, *, creator_id: str | None, handle: str | None) -> dict[str, Any] | None:
        creator = self.connection.execute(
            "SELECT * FROM creators WHERE creator_id = ? OR lower(handle) = lower(?) LIMIT 1",
            (creator_id or "", handle or ""),
        ).fetchone()
        if creator is None:
            return None
        manager = self.connection.execute(
            "SELECT * FROM managers WHERE manager_id = ? OR lower(manager_name) = lower(?) LIMIT 1",
            (creator["manager_id"] or "", creator["manager_name"] or ""),
        ).fetchone()
        selector = (creator["creator_id"], creator["handle"] or "")
        return {
            "creator": dict(creator),
            "manager": dict(manager) if manager else None,
            "goals": self._related_rows("goals", selector),
            "applications": self._related_rows("applications", selector),
            "business_essentials": self._related_rows("business_essentials", selector),
        }

    def get_manager(self, *, manager_id: str | None, name: str | None) -> dict[str, Any] | None:
        manager = self.connection.execute(
            "SELECT * FROM managers WHERE manager_id = ? OR lower(manager_name) = lower(?) LIMIT 1",
            (manager_id or "", name or ""),
        ).fetchone()
        if manager is None:
            return None
        selector = (manager["manager_id"], manager["manager_name"])
        return {
            "manager": dict(manager),
            "creators": self._manager_rows("creators", selector),
            "applications": self._manager_rows("applications", selector),
        }

    def dashboard(self) -> dict[str, Any]:
        """Return safe, high-level data for the local dashboard."""
        return {
            "counts": {
                "managers": self.connection.execute("SELECT count(*) FROM managers").fetchone()[0],
                "creators": self.connection.execute("SELECT count(*) FROM creators").fetchone()[0],
                "applications": self.connection.execute("SELECT count(*) FROM applications").fetchone()[0],
            },
            "managers": [dict(row) for row in self.connection.execute(
                "SELECT * FROM managers ORDER BY manager_name COLLATE NOCASE"
            )],
            "creators": [dict(row) for row in self.connection.execute(
                "SELECT * FROM creators ORDER BY creator_name COLLATE NOCASE, handle COLLATE NOCASE LIMIT 250"
            )],
            "snapshots": [dict(row) for row in self.connection.execute(
                "SELECT source, view_name, month, captured_at FROM source_snapshots ORDER BY captured_at DESC LIMIT 10"
            )],
        }

    def import_snapshot(self, json_file: Path) -> dict[str, object]:
        payload = json.loads(json_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("source") or not payload.get("captured_at"):
            raise ValueError("This is not a structured Backstage snapshot.")
        self.connection.execute(
            "INSERT INTO source_snapshots (source, view_name, month, captured_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (payload["source"], payload.get("view"), payload.get("month"), payload["captured_at"], json.dumps(payload)),
        )
        self.connection.commit()
        return payload

    def latest_snapshot(self, source: str, view_name: str | None = None) -> dict[str, object] | None:
        if view_name is None:
            row = self.connection.execute(
                "SELECT payload_json FROM source_snapshots WHERE source = ? ORDER BY captured_at DESC LIMIT 1", (source,)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT payload_json FROM source_snapshots WHERE source = ? AND view_name = ? ORDER BY captured_at DESC LIMIT 1",
                (source, view_name),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    @staticmethod
    def format_profile(profile: dict[str, Any]) -> str:
        creator = profile["creator"]
        lines = ["CREATOR", json.dumps(json.loads(creator["raw_json"]), indent=2)]
        if profile["manager"]:
            lines += ["\nMANAGER", json.dumps(json.loads(profile["manager"]["raw_json"]), indent=2)]
        for section in ("goals", "applications", "business_essentials"):
            rows = [json.loads(row["raw_json"]) for row in profile[section]]
            lines += [f"\n{section.upper().replace('_', ' ')}", json.dumps(rows, indent=2)]
        return "\n".join(lines)

    @staticmethod
    def format_manager(profile: dict[str, Any]) -> str:
        manager = json.loads(profile["manager"]["raw_json"])
        creators = [json.loads(row["raw_json"]) for row in profile["creators"]]
        applications = [json.loads(row["raw_json"]) for row in profile["applications"]]
        return "\n".join([
            "MANAGER", json.dumps(manager, indent=2),
            "\nASSIGNED CREATORS", json.dumps(creators, indent=2),
            "\nSCOUT CREATOR APPLICATIONS", json.dumps(applications, indent=2),
        ])

    def _insert_related(self, table: str, values: dict[str, str | None], raw: str) -> None:
        self.connection.execute(
            f"INSERT INTO {table} (creator_id, handle, manager_id, manager_name, goal_name, goal_value, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (values["creator_id"], values["handle"], values["manager_id"], values["manager_name"], values["goal_name"], values["goal_value"], raw),
        )

    def _related_rows(self, table: str, selector: tuple[str, str]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            f"SELECT * FROM {table} WHERE creator_id = ? OR lower(handle) = lower(?)", selector
        )]

    def _manager_rows(self, table: str, selector: tuple[str, str]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            f"SELECT * FROM {table} WHERE manager_id = ? OR lower(manager_name) = lower(?)", selector
        )]

    @staticmethod
    def _creator_key(values: dict[str, str | None]) -> str:
        key = values["creator_id"] or values["handle"]
        if not key:
            raise ValueError("Creator CSV needs a Creator ID or TikTok Username column.")
        return key

    @staticmethod
    def _value(row: dict[str, str], field: str) -> str | None:
        normalized = {key.strip().lower(): (value or "").strip() for key, value in row.items() if key}
        for alias in ALIASES[field]:
            if normalized.get(alias):
                return normalized[alias]
        return None

    @staticmethod
    def _email(email: str) -> str:
        value = email.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@") or " " in value:
            raise ValueError("Enter a valid email address.")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
