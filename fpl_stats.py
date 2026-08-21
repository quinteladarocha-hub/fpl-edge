"""Underlying-stats enrichment from FBref and Understat via `soccerdata`.

Both sources are free. FBref rate-limits hard and occasionally blocks cloud
IP ranges, so every call here is wrapped: if the scrape fails, the run
continues on FPL-API data alone and the brief says so. Never let a flaky
scrape kill the gameweek brief.

What we take:
  FBref standard  -> minutes, xG, xAG per 90
  FBref defense   -> tackles, interceptions, blocks, clearances per 90
  FBref misc      -> ball recoveries per 90 (feeds the MID/FWD DefCon threshold)
  Understat       -> xG, xA as an independent cross-check on FBref

Results are cached to disk by soccerdata itself plus a parquet snapshot here,
so a rate-limited rerun still has yesterday's numbers.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import unicodedata
from pathlib import Path

import pandas as pd

log = logging.getLogger("fpl_edge.stats")

CACHE_DIR = Path(".fplcache")
SNAPSHOT = CACHE_DIR / "underlying_stats.parquet"

# FBref and FPL disagree on club naming often enough to be worth a map.
TEAM_ALIASES = {
    "manchester city": "MCI",
    "manchester utd": "MUN",
    "manchester united": "MUN",
    "newcastle utd": "NEW",
    "newcastle united": "NEW",
    "nottingham forest": "NFO",
    "nott'ham forest": "NFO",
    "tottenham": "TOT",
    "tottenham hotspur": "TOT",
    "wolves": "WOL",
    "wolverhampton wanderers": "WOL",
    "brighton": "BHA",
    "brighton and hove albion": "BHA",
    "west ham": "WHU",
    "west ham united": "WHU",
    "leeds united": "LEE",
    "sheffield utd": "SHU",
    "sheffield united": "SHU",
    "crystal palace": "CRY",
    "aston villa": "AVL",
    "leicester city": "LEI",
    "ipswich town": "IPS",
    "luton town": "LUT",
    "afc bournemouth": "BOU",
    "bournemouth": "BOU",
}


def load_underlying(cfg: dict, players: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Return a per-FPL-player frame of prior-season per-90 rates.

    Returns (frame, meta). `meta` records which sources succeeded so the brief
    can be honest about what fed the numbers.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SOCCERDATA_DIR", str(CACHE_DIR / "soccerdata"))

    prior = cfg["season"]["prior"]
    meta = {"fbref": False, "understat": False, "matched": 0, "total": len(players)}

    fbref = _safe(lambda: _fbref_frame(prior), "FBref")
    if fbref is not None and not fbref.empty:
        meta["fbref"] = True

    understat = _safe(lambda: _understat_frame(prior), "Understat")
    if understat is not None and not understat.empty:
        meta["understat"] = True

    if fbref is None and understat is None:
        snapshot = _load_snapshot()
        if snapshot is not None:
            log.warning("both scrapes failed, using cached snapshot")
            meta["from_cache"] = True
            return snapshot, meta
        log.warning("no underlying stats available, model runs on FPL data only")
        return pd.DataFrame(), meta

    combined = _combine(fbref, understat)
    matched = _match_to_fpl(combined, players)
    meta["matched"] = int(matched["matched"].sum()) if not matched.empty else 0

    try:
        matched.to_parquet(SNAPSHOT)
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot write failed: %s", exc)

    log.info("underlying stats matched %s/%s players", meta["matched"], meta["total"])
    return matched, meta


# ---------------------------------------------------------------------- #


def _safe(fn, label):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - scrapers fail in many ways
        log.warning("%s unavailable (%s: %s)", label, type(exc).__name__, exc)
        return None


def _fbref_frame(season: str) -> pd.DataFrame:
    import soccerdata as sd

    fb = sd.FBref(leagues="ENG-Premier League", seasons=season)

    standard = fb.read_player_season_stats(stat_type="standard")
    defense = fb.read_player_season_stats(stat_type="defense")
    misc = _safe(lambda: fb.read_player_season_stats(stat_type="misc"), "FBref misc")

    std = _flatten(standard)
    dfn = _flatten(defense)
    frames = [std, dfn]
    if misc is not None:
        frames.append(_flatten(misc))

    base = frames[0]
    for extra in frames[1:]:
        cols = [c for c in extra.columns if c not in base.columns or c in ("player", "team")]
        base = base.merge(extra[cols], on=["player", "team"], how="outer")

    minutes = _pick(base, ["playing_time_min", "min", "minutes"])
    out = pd.DataFrame(
        {
            "player": base["player"],
            "team": base["team"],
            "minutes": minutes,
            "xg": _pick(base, ["expected_xg", "xg"]),
            "xa": _pick(base, ["expected_xag", "expected_xa", "xag", "xa"]),
            "tackles": _pick(base, ["tackles_tkl", "tkl"]),
            "interceptions": _pick(base, ["int", "interceptions"]),
            "blocks": _pick(base, ["blocks_blocks", "blocks"]),
            "clearances": _pick(base, ["clr", "clearances"]),
            "recoveries": _pick(base, ["performance_recov", "recov", "recoveries"]),
        }
    )
    out["source"] = "fbref"
    return out.dropna(subset=["player"])


def _understat_frame(season: str) -> pd.DataFrame:
    import soccerdata as sd

    us = sd.Understat(leagues="ENG-Premier League", seasons=season)
    raw = _flatten(us.read_player_season_stats())
    out = pd.DataFrame(
        {
            "player": raw["player"],
            "team": raw.get("team", pd.Series(["" ] * len(raw))),
            "us_minutes": _pick(raw, ["time", "minutes"]),
            "us_xg": _pick(raw, ["xg"]),
            "us_xa": _pick(raw, ["xa"]),
        }
    )
    return out.dropna(subset=["player"])


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """soccerdata returns MultiIndex columns and index. Flatten both."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join(str(p) for p in col if p and not str(p).startswith("Unnamed"))
            .strip("_")
            .lower()
            .replace(" ", "_")
            .replace("%", "pct")
            .replace("+", "plus")
            .replace("-", "_")
            for col in df.columns
        ]
    else:
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df = df.reset_index()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "player" not in df.columns:
        for candidate in ("index", "level_2", "name"):
            if candidate in df.columns:
                df = df.rename(columns={candidate: "player"})
                break
    if "team" not in df.columns:
        for candidate in ("squad", "level_1"):
            if candidate in df.columns:
                df = df.rename(columns={candidate: "team"})
                break
    if "team" not in df.columns:
        df["team"] = ""
    return df


def _pick(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    for name in candidates:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index)


def _combine(fbref: pd.DataFrame | None, understat: pd.DataFrame | None) -> pd.DataFrame:
    if fbref is None or fbref.empty:
        us = understat.copy()
        us["minutes"] = us["us_minutes"]
        us["xg"] = us["us_xg"]
        us["xa"] = us["us_xa"]
        for col in ("tackles", "interceptions", "blocks", "clearances", "recoveries"):
            us[col] = float("nan")
        return us
    if understat is None or understat.empty:
        fbref["us_xg"] = float("nan")
        fbref["us_xa"] = float("nan")
        return fbref

    fbref = fbref.copy()
    fbref["_key"] = fbref["player"].map(_normalise)
    understat = understat.copy()
    understat["_key"] = understat["player"].map(_normalise)
    merged = fbref.merge(
        understat[["_key", "us_xg", "us_xa", "us_minutes"]], on="_key", how="left"
    )
    # Average the two xG sources where both exist. They use different models,
    # so the mean is a genuinely better estimate than either alone.
    merged["xg"] = merged[["xg", "us_xg"]].mean(axis=1, skipna=True)
    merged["xa"] = merged[["xa", "us_xa"]].mean(axis=1, skipna=True)
    return merged.drop(columns=["_key"])


def _match_to_fpl(stats: pd.DataFrame, players: list[dict]) -> pd.DataFrame:
    """Join scraped rows onto FPL element ids.

    Two passes: exact normalised full-name match, then a difflib fallback
    restricted to the same club so we never match a Spurs winger to a Burnley
    centre-back.
    """
    stats = stats.copy()
    stats["_key"] = stats["player"].map(_normalise)
    stats["_club"] = stats["team"].map(_club_code)

    by_key: dict[str, pd.Series] = {}
    for _, row in stats.iterrows():
        by_key.setdefault(row["_key"], row)

    keys_by_club: dict[str, list[str]] = {}
    for key, row in by_key.items():
        keys_by_club.setdefault(row["_club"], []).append(key)

    rows = []
    for p in players:
        candidates = [_normalise(p["full_name"]), _normalise(p["name"])]
        hit = None
        for cand in candidates:
            if cand in by_key:
                hit = by_key[cand]
                break
        if hit is None:
            pool = keys_by_club.get(p["team"], [])
            for cand in candidates:
                close = difflib.get_close_matches(cand, pool, n=1, cutoff=0.86)
                if close:
                    hit = by_key[close[0]]
                    break
        rows.append(_stat_row(p, hit))

    return pd.DataFrame(rows)


def _stat_row(player: dict, hit) -> dict:
    if hit is None:
        return {
            "id": player["id"],
            "matched": False,
            "u_minutes": 0.0,
            "u_xg90": float("nan"),
            "u_xa90": float("nan"),
            "u_defact90": float("nan"),
        }
    minutes = float(hit.get("minutes") or 0.0)
    per90 = (lambda v: (float(v) / minutes * 90.0) if minutes > 0 and pd.notna(v) else float("nan"))
    defensive_cols = ["tackles", "interceptions", "blocks", "clearances"]
    if player["position"] in ("MID", "FWD"):
        defensive_cols.append("recoveries")
    actions = sum(
        float(hit.get(c) or 0.0) for c in defensive_cols if pd.notna(hit.get(c))
    )
    has_def = any(pd.notna(hit.get(c)) for c in defensive_cols)
    return {
        "id": player["id"],
        "matched": True,
        "u_minutes": minutes,
        "u_xg90": per90(hit.get("xg")),
        "u_xa90": per90(hit.get("xa")),
        "u_defact90": per90(actions) if has_def else float("nan"),
    }


def _load_snapshot() -> pd.DataFrame | None:
    if SNAPSHOT.exists():
        try:
            return pd.read_parquet(SNAPSHOT)
        except Exception:  # noqa: BLE001
            return None
    return None


def _normalise(name: str) -> str:
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("-", " ").replace("'", "")
    text = re.sub(r"[^a-z ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _club_code(name: str) -> str:
    if not isinstance(name, str):
        return ""
    key = name.strip().lower()
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    return name.strip()[:3].upper()
