"""Offline self-test: runs the whole pipeline on a synthetic league.

Builds a payload with the exact shape of bootstrap-static (20 real 2026/27
clubs, realistic price ladders and stat distributions, seeded so it is
deterministic), pushes it through the model, the optimiser and the brief,
then checks the answer is a legal FPL squad.

Player names are deliberately synthetic. This test proves the machinery is
correct; it does not produce a real recommendation. Run it when you have
changed the model and do not want to wait on the live API.

  python test_fpl_edge.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd
import yaml

import fpl_api
import fpl_brief
import fpl_model
import fpl_optimiser

SEED = 20262027

# 2026/27 Premier League: 17 survivors plus Coventry, Ipswich and Hull.
# Strength ratings follow FPL's own 1000-1400 scale.
CLUBS = [
    ("ARS", "Arsenal", 1360), ("MCI", "Man City", 1345), ("LIV", "Liverpool", 1330),
    ("AVL", "Aston Villa", 1275), ("MUN", "Man Utd", 1265), ("CHE", "Chelsea", 1255),
    ("NEW", "Newcastle", 1245), ("TOT", "Spurs", 1230), ("BHA", "Brighton", 1195),
    ("CRY", "Crystal Palace", 1185), ("NFO", "Nott'm Forest", 1175),
    ("BOU", "Bournemouth", 1165), ("BRE", "Brentford", 1150), ("EVE", "Everton", 1140),
    ("FUL", "Fulham", 1135), ("SUN", "Sunderland", 1105), ("LEE", "Leeds", 1085),
    ("COV", "Coventry", 1045), ("IPS", "Ipswich", 1030), ("HUL", "Hull", 1015),
]

SQUAD_SHAPE = [("GK", 2, 1), ("DEF", 6, 4), ("MID", 7, 5), ("FWD", 4, 2)]
FIRST_NAMES = ["Adeyemi", "Bassey", "Costa", "Duarte", "Eriksen", "Fofana", "Gudmundsson",
               "Hansen", "Ibarra", "Jankovic", "Kovacic", "Larsen", "Moreno", "Nakamura",
               "Oyelaran", "Petrov", "Quintero", "Ricci", "Sorensen", "Takahashi",
               "Urbina", "Vasquez", "Wallace", "Yilmaz", "Zielinski"]


def build_bootstrap(rng: random.Random) -> dict:
    teams, elements = [], []
    element_id = 1
    for team_id, (short, name, strength) in enumerate(CLUBS, start=1):
        quality = (strength - 1000) / 400.0  # 0.04 .. 0.90
        teams.append({
            "id": team_id, "short_name": short, "name": name,
            "strength_attack_home": strength + 30, "strength_attack_away": strength - 20,
            "strength_defence_home": strength + 25, "strength_defence_away": strength - 25,
        })
        for position, count, starters in SQUAD_SHAPE:
            for slot in range(count):
                is_starter = slot < starters
                elements.append(
                    _element(rng, element_id, team_id, short, position, quality,
                             is_starter, slot)
                )
                element_id += 1

    events = [
        {"id": e, "name": f"Gameweek {e}", "finished": False,
         "is_next": e == 1, "is_current": False,
         "deadline_time": f"2026-08-{20 + e:02d}T17:30:00Z"}
        for e in range(1, 9)
    ]
    return {"teams": teams, "elements": elements, "events": events}


def _element(rng, eid, team_id, short, position, quality, is_starter, slot):
    """One player, priced and statted the way FPL actually prices and stats them."""
    base_price = {"GK": 44, "DEF": 42, "MID": 46, "FWD": 47}[position]
    premium = {"GK": 14, "DEF": 26, "MID": 100, "FWD": 105}[position]
    role = max(0.0, 1.0 - slot * (0.30 if position != "MID" else 0.24))
    price = base_price + int(premium * quality * role * rng.uniform(0.55, 1.0))
    price = max(38, min(155, round(price / 5) * 5))

    # Prior-season attacking output per 90, by position and quality.
    attack = quality * role * rng.uniform(0.6, 1.35)
    xg90 = {"GK": 0.0, "DEF": 0.05, "MID": 0.20, "FWD": 0.45}[position] * attack
    xa90 = {"GK": 0.0, "DEF": 0.08, "MID": 0.22, "FWD": 0.16}[position] * attack
    defact90 = {"GK": 0.0, "DEF": 11.5, "MID": 9.0, "FWD": 4.5}[position] * rng.uniform(0.6, 1.3)

    if position == "GK":
        prior_minutes = int((3350 if is_starter else 240) * rng.uniform(0.72, 1.02))
    else:
        prior_minutes = int((2600 if is_starter else 700) * rng.uniform(0.55, 1.05))
    status = "a"
    chance = None
    if rng.random() < 0.06:
        status, chance = "d", rng.choice([50, 75])
    elif rng.random() < 0.03:
        status, chance = "i", 0

    return {
        "id": eid, "web_name": f"{rng.choice(FIRST_NAMES)}{eid % 97:02d}",
        "first_name": "Test", "second_name": f"Player{eid}",
        "team": team_id, "element_type": {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
        "now_cost": price, "status": status, "chance_of_playing_next_round": chance,
        # Gameweek 1: every current-season column is zero. This is the real
        # condition the model has to cope with, so the test reproduces it.
        "minutes": 0, "starts": 0, "total_points": 0, "points_per_game": "0.0",
        "form": "0.0", "goals_scored": 0, "assists": 0, "clean_sheets": 0,
        "goals_conceded": 0, "saves": 0, "bonus": 0, "expected_goals": "0.00",
        "expected_assists": "0.00", "expected_goals_conceded": "0.00",
        "defensive_contribution": 0,
        "ep_next": f"{_fake_ep(position, xg90, xa90, prior_minutes):.1f}",
        "selected_by_percent": f"{max(0.1, quality * role * rng.uniform(1, 40)):.1f}",
        "transfers_in_event": 0, "transfers_out_event": 0,
        "_truth": {"xg90": xg90, "xa90": xa90, "defact90": defact90,
                   "prior_minutes": prior_minutes},
    }


def _fake_ep(position, xg90, xa90, prior_minutes):
    """Stand-in for FPL's ep_next, deliberately a bit different from our model."""
    if prior_minutes < 400:
        return 0.6
    goal_pts = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}[position]
    return 2.0 + xg90 * goal_pts + xa90 * 3 + (1.2 if position in ("GK", "DEF") else 0.3)


def build_fixtures(rng: random.Random, events: int = 8) -> list[dict]:
    fixtures, fid = [], 1
    strengths = {i + 1: c[2] for i, c in enumerate(CLUBS)}
    ranked = sorted(strengths, key=lambda t: -strengths[t])
    fdr_by_team = {t: min(5, max(2, 2 + i // 5)) for i, t in enumerate(ranked)}
    # Strong opponents are hard (FDR 5), weak opponents easy (FDR 2).
    fdr_by_team = {t: {0: 5, 1: 4, 2: 3, 3: 3}.get(i // 5, 2) for i, t in enumerate(ranked)}

    team_ids = list(strengths)
    for event in range(1, events + 1):
        pool = team_ids[:]
        rng.shuffle(pool)
        for i in range(0, len(pool), 2):
            home, away = pool[i], pool[i + 1]
            fixtures.append({
                "id": fid, "event": event, "team_h": home, "team_a": away,
                "team_h_difficulty": fdr_by_team[away],
                "team_a_difficulty": min(5, fdr_by_team[home] + 1),
            })
            fid += 1
    return fixtures


def build_priors(bootstrap: dict) -> dict[int, dict]:
    out = {}
    for el in bootstrap["elements"]:
        truth = el["_truth"]
        minutes = truth["prior_minutes"]
        if minutes < 200:
            continue
        matches = minutes / 90.0
        out[el["id"]] = {
            "season": "2025/26", "minutes": minutes,
            "total_points": 0.0, "goals_scored": truth["xg90"] * matches,
            "assists": truth["xa90"] * matches, "clean_sheets": 0.0,
            "goals_conceded": 0.0, "saves": 55.0 if el["element_type"] == 1 else 0.0,
            "bonus": max(0.0, (truth["xg90"] + truth["xa90"]) * matches * 1.6),
            "expected_goals": truth["xg90"] * matches,
            "expected_assists": truth["xa90"] * matches,
            "expected_goals_conceded": 0.0, "defensive_contribution": 0.0,
        }
    return out


def build_underlying(bootstrap: dict) -> pd.DataFrame:
    rows = []
    for el in bootstrap["elements"]:
        truth = el["_truth"]
        matched = truth["prior_minutes"] >= 200
        rows.append({
            "id": el["id"], "matched": matched,
            "u_minutes": float(truth["prior_minutes"]),
            "u_xg90": truth["xg90"] if matched else float("nan"),
            "u_xa90": truth["xa90"] if matched else float("nan"),
            "u_defact90": truth["defact90"] if matched else float("nan"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- #


def main() -> int:
    rng = random.Random(SEED)
    cfg = yaml.safe_load(Path("fpl_config.yaml").read_text())

    bootstrap = build_bootstrap(rng)
    fixtures = build_fixtures(rng)
    event = fpl_api.current_event(bootstrap)
    players = fpl_api.players_frame(bootstrap)
    fixtures_by_team = fpl_api.upcoming_fixtures(
        fixtures, event, int(cfg["horizon"]["gameweeks"])
    )
    priors = build_priors(bootstrap)
    underlying = build_underlying(bootstrap)

    print(f"synthetic league: {len(players)} players, {len(CLUBS)} clubs, "
          f"gameweek {event}, {len(fixtures)} fixtures")

    scored = fpl_model.build_scores(
        cfg, players, fixtures_by_team, priors, underlying, event
    )
    result = fpl_optimiser.optimise_squad(cfg, scored)
    errors = fpl_optimiser.validate(cfg, result)

    _report(cfg, result)

    extra = _extra_checks(cfg, result, scored)
    for e in errors + extra:
        print(f"  FAIL  {e}")
    if errors or extra:
        return 1

    context = {
        "event": event, "deadline": "2026-08-21T17:30:00Z",
        "horizon": cfg["horizon"]["gameweeks"], "player_count": len(scored),
        "prior_count": len(priors),
        "stats_meta": {"fbref": True, "understat": True, "matched": int(
            underlying["matched"].sum())},
        "captain_shortlist": sorted(
            [p for p in scored if p["expected_minutes"] >= 45],
            key=lambda p: -p["consensus_xp"])[:8],
        "differential_shortlist": sorted(
            [p for p in scored if p["selected_by_percent"] <= 8
             and p["expected_minutes"] >= 45],
            key=lambda p: -p["consensus_xp"])[:8],
    }
    Path("test_brief.html").write_text(fpl_brief.render(cfg, result, context))
    print("\n  wrote test_brief.html")
    print("  ALL CHECKS PASSED")
    return 0


def _extra_checks(cfg, result, scored) -> list[str]:
    """Checks beyond bare legality: is the answer actually sensible?"""
    problems = []
    xi_xp = [p["consensus_xp"] for p in result["xi"]]

    # No bench player should beat a starter he could legally replace. Formation
    # limits mean not every swap is available, so test each swap for legality
    # rather than comparing the raw min and max.
    counts = {pos: sum(1 for p in result["xi"] if p["position"] == pos)
              for pos in ("GK", "DEF", "MID", "FWD")}
    for b in result["bench"]:
        for s_ in result["xi"]:
            if b["consensus_xp"] <= s_["consensus_xp"] + 1e-6:
                continue
            after = dict(counts)
            after[s_["position"]] -= 1
            after[b["position"]] += 1
            legal = all(cfg["rules"]["xi_min"][p] <= after[p] <= cfg["rules"]["xi_max"][p]
                        for p in after)
            if legal:
                problems.append(
                    f"bench {b['position']} {b['name']} ({b['consensus_xp']:.2f} xP) "
                    f"beats starter {s_['name']} ({s_['consensus_xp']:.2f} xP) "
                    "in a legal swap")
    if result["bank"] < -1e-6:
        problems.append(f"negative bank {result['bank']}")
    cap = result["captain"]
    if cap and cap["consensus_xp"] < max(xi_xp) - 1e-6:
        problems.append("captain is not the highest-xP starter")
    if any(p["availability"] == 0 for p in result["squad"]):
        problems.append("squad contains a player flagged unavailable")
    return problems


def _report(cfg, result) -> None:
    print(f"\n  solver           {result['backend']}")
    print(f"  formation        {result['formation']}")
    print(f"  squad cost       {result['cost']:.1f}m of {cfg['rules']['budget']:.1f}m")
    print(f"  bank             {result['bank']:.1f}m")
    print(f"  projected points {result['total_xp']:.2f} over "
          f"{cfg['horizon']['gameweeks']} gameweeks\n")

    from collections import Counter
    clubs = Counter(p["team"] for p in result["squad"])
    print(f"  club spread      {dict(sorted(clubs.items(), key=lambda kv: -kv[1]))}")
    print(f"  max per club     {max(clubs.values())} (limit "
          f"{cfg['rules']['max_per_club']})\n")

    for label, group in (("STARTING XI", result["xi"]), ("BENCH", result["bench"])):
        print(f"  {label}")
        for p in group:
            mark = "  (C)" if p["is_captain"] else ""
            print(f"    {p['position']:<4}{p['name']:<14}{p['team']:<5}"
                  f"{p['price']:>5.1f}m{p['expected_minutes']:>5.0f}min"
                  f"{p['consensus_xp']:>7.2f} xP{mark}")
        print()


if __name__ == "__main__":
    sys.exit(main())
