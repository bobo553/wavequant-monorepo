"""Durable single-user watchlists for the loopback research server."""

from __future__ import annotations

from contextlib import closing
from datetime import date
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import cast
from uuid import uuid4


DEFAULT_GROUP: dict[str, object] = {"id": "default", "name": "我的自选", "position": 0, "protected": True}
MAX_BODY = 4 * 1024 * 1024


class WatchlistConflict(Exception):
    """Another client has saved a newer revision."""


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("自选数据格式无效")
    return cast(dict[str, object], value)


def text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("自选字段格式无效")
    return value.strip()


def snapshot(value: object) -> dict[str, object]:
    data = record(value)
    groups, members = data.get("groups"), data.get("memberships")
    if data.get("schemaVersion") != 1 or not isinstance(groups, list) or not isinstance(members, list):
        raise ValueError("自选数据版本无效")
    if not 1 <= len(groups) <= 20 or len(members) > 20_000:
        raise ValueError("自选数量超出限制")
    ids: set[str] = set()
    names: set[str] = set()
    normalized_groups: list[dict[str, object]] = []
    for index, item in enumerate(groups):
        group = record(item)
        group_id, name = text(group.get("id"), 64), text(group.get("name"), 24)
        if group_id in ids or name.casefold() in names:
            raise ValueError("自选分类重复")
        if index == 0 and (group_id != "default" or name != DEFAULT_GROUP["name"]):
            raise ValueError("默认分类不能修改")
        ids.add(group_id)
        names.add(name.casefold())
        normalized_groups.append({"id": group_id, "name": name, "position": index, "protected": index == 0})
    keys: set[tuple[str, str]] = set()
    positions = dict.fromkeys(ids, 0)
    normalized_members: list[dict[str, object]] = []
    for item in members:
        member = record(item)
        group_id, symbol = text(member.get("groupId"), 64), text(member.get("symbol"), 9)
        member_name = member.get("name", "")
        if group_id not in ids or re.fullmatch(r"(?:sh|sz|bj)\.\d{6}", symbol) is None:
            raise ValueError("自选股票代码或分类无效")
        if not isinstance(member_name, str) or len(member_name) > 80 or (group_id, symbol) in keys:
            raise ValueError("自选股票名称无效或重复")
        keys.add((group_id, symbol))
        if positions[group_id] >= 1000:
            raise ValueError("每个分类最多保存1000只股票")
        normalized_members.append({"groupId": group_id, "symbol": symbol, "name": member_name,
                                   "position": positions[group_id]})
        positions[group_id] += 1
    return {"schemaVersion": 1, "groups": normalized_groups, "memberships": normalized_members}


def settings(value: object) -> dict[str, object]:
    data = record(value)
    if type(data.get("enabled")) is not bool:
        raise ValueError("自动回测开关无效")
    context = record(data.get("context"))
    normalized: dict[str, str] = {}
    for key in ("run", "variant", "scenario", "source", "start", "volume_filter", "net_reward_risk_filter",
                "shallow_base_breakout_enabled", "initial_capital", "max_position_weight"):
        normalized[key] = text(context.get(key), 128)
    n_target = context.get("n_target_trend_confirmation_enabled", "false")
    if not isinstance(n_target, str) or n_target not in {"true", "false"}:
        raise ValueError("N达标趋势开关无效")
    normalized["n_target_trend_confirmation_enabled"] = n_target
    if normalized["source"] not in {"akshare", "tdx"}:
        raise ValueError("回测数据源无效")
    date.fromisoformat(normalized["start"])
    for key in ("volume_filter", "net_reward_risk_filter", "shallow_base_breakout_enabled"):
        if normalized[key] not in {"true", "false"}:
            raise ValueError("回测开关无效")
    for key, limit in (("initial_capital", 1_000_000_000), ("max_position_weight", 1)):
        number = float(normalized[key])
        if not math.isfinite(number) or not 0 < number <= limit:
            raise ValueError("回测仓位或本金无效")
        normalized[key] = str(int(number)) if number.is_integer() else str(number)
    return {"enabled": data["enabled"], "context": normalized}


class WatchlistStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS watchlist_document "
                               "(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS watchlist_imports (token TEXT PRIMARY KEY)")
            connection.execute("CREATE TABLE IF NOT EXISTS watchlist_runs "
                               "(key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, retry_at REAL NOT NULL, "
                               "completed INTEGER NOT NULL, payload TEXT NOT NULL)")
            payload = {"snapshot": snapshot({"schemaVersion": 1, "groups": [DEFAULT_GROUP], "memberships": []}),
                       "settings": None}
            connection.execute("INSERT OR IGNORE INTO watchlist_document VALUES (1, 0, ?)",
                               (json.dumps(payload, ensure_ascii=False),))

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def load(self) -> dict[str, object]:
        with closing(self.connect()) as connection:
            revision, payload = connection.execute("SELECT revision, payload FROM watchlist_document WHERE id=1").fetchone()
        document = record(json.loads(payload))
        if document.get("settings") is not None:
            document["settings"] = settings(document["settings"])
        return {"revision": revision, **document}

    def save(self, revision: object, value: object, *, field: str = "snapshot") -> dict[str, object]:
        normalized = snapshot(value) if field == "snapshot" else settings(value)
        if type(revision) is not int or revision < 0:
            raise ValueError("自选版本无效")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current_revision, payload = connection.execute(
                "SELECT revision, payload FROM watchlist_document WHERE id=1").fetchone()
            if current_revision != revision:
                raise WatchlistConflict("自选股已在其他页面更新，请同步后重试")
            current = record(json.loads(payload))
            if current[field] != normalized:
                current[field] = normalized
                current_revision += 1
                connection.execute("UPDATE watchlist_document SET revision=?, payload=? WHERE id=1",
                                   (current_revision, json.dumps(current, ensure_ascii=False)))
        return {"revision": current_revision, **current}

    def import_legacy(self, token: object, value: object) -> dict[str, object]:
        import_id = text(token, 64)
        if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", import_id) is None:
            raise ValueError("迁移标识无效")
        legacy = snapshot(value)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            revision, payload = connection.execute("SELECT revision, payload FROM watchlist_document WHERE id=1").fetchone()
            current = record(json.loads(payload))
            if connection.execute("SELECT 1 FROM watchlist_imports WHERE token=?", (import_id,)).fetchone():
                return {"revision": revision, **current}
            merged = record(current["snapshot"])
            groups = [record(group) for group in cast(list[object], merged["groups"])]
            members = [record(member) for member in cast(list[object], merged["memberships"])]
            mapping = {"default": "default"}
            for raw_group in cast(list[object], legacy["groups"])[1:]:
                group = record(raw_group)
                same = next((item for item in groups if str(item["name"]).casefold() == str(group["name"]).casefold()), None)
                if same is None:
                    group_id = str(group["id"])
                    if any(item["id"] == group_id for item in groups):
                        group_id = str(uuid4())
                    same = {**group, "id": group_id, "position": len(groups)}
                    groups.append(same)
                mapping[str(group["id"])] = str(same["id"])
            keys = {(member["groupId"], member["symbol"]) for member in members}
            for raw_member in cast(list[object], legacy["memberships"]):
                member = record(raw_member)
                group_id = mapping[str(member["groupId"])]
                if (group_id, member["symbol"]) not in keys:
                    keys.add((group_id, member["symbol"]))
                    members.append({**member, "groupId": group_id})
            current["snapshot"] = snapshot({"schemaVersion": 1, "groups": groups, "memberships": members})
            revision += 1
            connection.execute("UPDATE watchlist_document SET revision=?, payload=? WHERE id=1",
                               (revision, json.dumps(current, ensure_ascii=False)))
            connection.execute("INSERT INTO watchlist_imports VALUES (?)", (import_id,))
        return {"revision": revision, **current}

    def run_state(self, key: str) -> tuple[int, float, bool]:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT attempts,retry_at,completed FROM watchlist_runs WHERE key=?", (key,)).fetchone()
        return (int(row[0]), float(row[1]), bool(row[2])) if row else (0, 0, False)

    def run_states(self) -> dict[str, tuple[int, float, bool]]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT key,attempts,retry_at,completed FROM watchlist_runs").fetchall()
        return {str(key): (int(attempts), float(retry_at), bool(completed)) for key, attempts, retry_at, completed in rows}

    def finish(self, key: str, attempts: int, retry_at: float, completed: bool,
               summary: dict[str, object] | None = None) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO watchlist_runs VALUES (?, ?, ?, ?, ?)",
                               (key, attempts, retry_at, int(completed), json.dumps(summary or {}, ensure_ascii=False)))

    def summaries(self) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT payload FROM watchlist_runs WHERE completed=1").fetchall()
        return [record(json.loads(row[0])) for row in rows]

    def prune_runs(self, desired: set[str]) -> None:
        with closing(self.connect()) as connection, connection:
            for (key,) in connection.execute("SELECT key FROM watchlist_runs").fetchall():
                if key not in desired:
                    connection.execute("DELETE FROM watchlist_runs WHERE key=?", (key,))

    def retry(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM watchlist_runs WHERE completed=0")
