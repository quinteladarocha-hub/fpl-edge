"""Official Fantasy Premier League API client.

Free, no key required. Endpoints used:
  bootstrap-static/      players, teams, positions, current gameweek
  fixtures/              every fixture with FDR for both sides
  element-summary/<id>/  per-player gameweek history and prior-season totals
  entry/<id>/            manager metadata (used from Phase 3 onward)

Everything is cached to disk so repeated runs and local debugging do not
hammer the API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("fpl_edge.api")

USER_AGENT = (
    "FPLEdge/1.0 (personal fantasy analysis; contact via GitHub) "
    "python-requests"
)

POSITION_BY_ELEMENT_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


class FPLClient:
    def __init__(self, cfg: dict):
        api = cfg["api"]
        self.base = api["base"].rstrip("/")
        self.cache_dir = Path(api["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_seconds = float(api["cache_hours"]) * 3600.0
        self.delay = float(api["request_delay_seconds"])
        self.timeout = float(api["timeout_seconds"])
        self.retries = int(api["retries"])
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_call = 0.0

    # ------------------------------------------------------------------ #

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").strip("_")
        return self.cache_dir / f"{safe}.json"

    def _read_cache(self, key: str) -> Any | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_seconds:
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _write_cache(self, key: str, payload: Any) -> None:
        try:
            self._cache_path(key).write_text(json.dumps(payload))
        except OSError as exc:
            log.warning("could not write cache for %s: %s", key, exc)

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.time()

    def get(self, path: str, use_cache: bool = True) -> Any:
        key = path.strip("/")
        if use_cache:
            cached = self._read_cache(key)
            if cached is not None:
                return cached

        url = f"{self.base}/{key}/"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = 2.0 * (attempt + 1)
                    log.warning("rate limited on %s, waiting %.0fs", key, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                self._write_cache(key, payload)
                return payload
            except Exception as exc:  # noqa: BLE001 - retry on anything transient
                last_error = exc
                time.sleep(1.0 + attempt)
        raise RuntimeError(f"FPL API failed for {url}: {last_error}")

    # ------------------------------------------------------------------ #

    def bootstrap(self) -> dict:
        return self.get("bootstrap-static")

    def fixtures(self) -> list[dict]:
        return self.get("fixtures")

    def element_summary(self, element_id: int) -> dict:
        return self.get(f"element-summary/{element_id}")

    def entry(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}")

    def entry_picks(self, entry_id: int, event: int) -> dict:
        return self.get(f"entry/{entry_id}/event/{event}/picks")


# ---------------------------------------------------------------------- #
# Shaping helpers
# ---------------------------------------------------------------------- #


def current_event(bootstrap: dict) -> int:
    """The gameweek we are building for.

    Before the season starts every event is unfinished, so `is_next` is the
    reliable marker. Falls back to the first unfinished event, then to 1.
    """
    events = bootstrap.get("events", [])
    for ev in events:
        if ev.get("is_next"):
            return int(ev["id"])
    for ev in events:
        if not ev.get("finished"):
            return int(ev["id"])
    return 1


def deadline_for_event(bootstrap: dict, event: int) -> str:
    for ev in bootstrap.get("events", []):
        if int(ev["id"]) == event:
            return ev.get("deadline_time", "")
    return ""


def teams_frame(bootstrap: dict) -> dict[int, dict]:
    return {int(t["id"]): t for t in bootstrap["teams"]}


def players_frame(bootstrap: dict) -> list[dict]:
    """Flatten bootstrap elements into the fields the model actually uses."""
    teams = teams_frame(bootstrap)
    out: list[dict] = []
    for el in bootstrap["elements"]:
        team = teams.get(int(el["team"]), {})
        out.append(
            {
                "id": int(el["id"]),
                "name": el.get("web_name", ""),
                "full_name": f"{el.get('first_name','')} {el.get('second_name','')}".strip(),
                "team_id": int(el["team"]),
                "team": team.get("short_name", "???"),
                "team_name": team.get("name", "Unknown"),
                "position": POSITION_BY_ELEMENT_TYPE.get(int(el["element_type"]), "MID"),
                "price": int(el["now_cost"]) / 10.0,
                "price_tenths": int(el["now_cost"]),
                "status": el.get("status", "a"),
                "chance_next": el.get("chance_of_playing_next_round"),
                "minutes": _num(el.get("minutes")),
                "starts": _num(el.get("starts")),
                "total_points": _num(el.get("total_points")),
                "points_per_game": _num(el.get("points_per_game")),
                "form": _num(el.get("form")),
                "ep_next": _num(el.get("ep_next")),
                "selected_by_percent": _num(el.get("selected_by_percent")),
                "transfers_in_event": _num(el.get("transfers_in_event")),
                "transfers_out_event": _num(el.get("transfers_out_event")),
                "goals_scored": _num(el.get("goals_scored")),
                "assists": _num(el.get("assists")),
                "clean_sheets": _num(el.get("clean_sheets")),
                "goals_conceded": _num(el.get("goals_conceded")),
                "saves": _num(el.get("saves")),
                "bonus": _num(el.get("bonus")),
                "expected_goals": _num(el.get("expected_goals")),
                "expected_assists": _num(el.get("expected_assists")),
                "expected_goals_conceded": _num(el.get("expected_goals_conceded")),
                "defensive_contribution": _num(el.get("defensive_contribution")),
                "team_strength_attack_home": _num(team.get("strength_attack_home"), 1100),
                "team_strength_attack_away": _num(team.get("strength_attack_away"), 1100),
                "team_strength_defence_home": _num(team.get("strength_defence_home"), 1100),
                "team_strength_defence_away": _num(team.get("strength_defence_away"), 1100),
            }
        )
    return out


def upcoming_fixtures(
    fixtures: list[dict], from_event: int, horizon: int
) -> dict[int, list[dict]]:
    """Map team_id -> list of its fixtures in the horizon window.

    Handles blanks (a team simply has fewer entries) and doubles (more than
    one entry for the same event) without special-casing.
    """
    window = set(range(from_event, from_event + horizon))
    by_team: dict[int, list[dict]] = {}
    for fx in fixtures:
        ev = fx.get("event")
        if ev is None or int(ev) not in window:
            continue
        home, away = int(fx["team_h"]), int(fx["team_a"])
        by_team.setdefault(home, []).append(
            {
                "event": int(ev),
                "opponent": away,
                "is_home": True,
                "fdr": int(fx.get("team_h_difficulty") or 3),
            }
        )
        by_team.setdefault(away, []).append(
            {
                "event": int(ev),
                "opponent": home,
                "is_home": False,
                "fdr": int(fx.get("team_a_difficulty") or 3),
            }
        )
    for team_fixtures in by_team.values():
        team_fixtures.sort(key=lambda f: f["event"])
    return by_team


def prior_season_totals(
    client: FPLClient, player_ids: list[int], prior_label: str | None = None
) -> dict[int, dict]:
    """Prior-season FPL totals from element-summary history_past.

    This is the free, official source of last-season points, minutes and bonus.
    It is what lets the model produce a sensible squad in Gameweek 1, when every
    current-season column is still zero.
    """
    out: dict[int, dict] = {}
    total = len(player_ids)
    for i, pid in enumerate(player_ids, start=1):
        try:
            summary = client.element_summary(pid)
        except RuntimeError as exc:
            log.warning("element-summary failed for %s: %s", pid, exc)
            continue
        history = summary.get("history_past") or []
        if not history:
            continue
        row = history[-1]
        if prior_label and row.get("season_name") != prior_label:
            match = [h for h in history if h.get("season_name") == prior_label]
            if not match:
                continue
            row = match[0]
        out[pid] = {
            "season": row.get("season_name", ""),
            "minutes": _num(row.get("minutes")),
            "total_points": _num(row.get("total_points")),
            "goals_scored": _num(row.get("goals_scored")),
            "assists": _num(row.get("assists")),
            "clean_sheets": _num(row.get("clean_sheets")),
            "goals_conceded": _num(row.get("goals_conceded")),
            "saves": _num(row.get("saves")),
            "bonus": _num(row.get("bonus")),
            "expected_goals": _num(row.get("expected_goals")),
            "expected_assists": _num(row.get("expected_assists")),
            "expected_goals_conceded": _num(row.get("expected_goals_conceded")),
            "defensive_contribution": _num(row.get("defensive_contribution")),
        }
        if i % 50 == 0:
            log.info("prior-season history: %s/%s", i, total)
    return out


def shortlist_for_prior(players: list[dict], limit: int) -> list[int]:
    """Which players are worth an element-summary call.

    Cheap bench fodder does not need a prior - it scores near zero either way
    and is only ever bought as budget filler. Spending 400 requests on the
    players who might actually be selected keeps the run under two minutes.
    """
    ranked = sorted(
        players,
        key=lambda p: (p["selected_by_percent"] * 2.0 + p["price"]),
        reverse=True,
    )
    return [p["id"] for p in ranked[:limit]]


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
