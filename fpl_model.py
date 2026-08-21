"""The expected-points model.

Design notes that matter for FPL specifically:

1. Shrinkage, not switching. Current-season rates are weighted
   w = mins / (mins + k) against prior-season rates. At Gameweek 1 w is zero,
   so the model runs entirely on last season's underlying numbers. By about
   Gameweek 10 a nailed starter has w near 0.65. There is no arbitrary
   "wait five gameweeks before trusting form" cliff.

2. Points are built from components, not fitted to past points. Goals come
   from xG, assists from xA, clean sheets from a fixture-adjusted probability,
   defensive contributions from a Poisson tail on defensive actions per 90.
   That is what lets the model say a player is underperforming his underlying
   numbers rather than just extrapolating his returns.

3. Minutes gate everything. Every rate is per 90 and then multiplied by
   expected minutes. Without this the optimiser buys 4.0m squad players whose
   per-90 rates are pure small-sample noise. The Phase 1 minutes estimate is
   deliberately crude and is the single biggest thing Phase 2 improves.

4. The ensemble blends at the per-gameweek level. Our model owns the fixture
   shape across the horizon; FPL's own `ep_next` tugs the overall level for the
   next gameweek only. Blending horizon totals against a one-week number would
   be a units error.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

log = logging.getLogger("fpl_edge.model")


def build_scores(
    cfg: dict,
    players: list[dict],
    fixtures_by_team: dict[int, list[dict]],
    priors: dict[int, dict],
    underlying: pd.DataFrame,
    current_event: int,
) -> list[dict]:
    """Attach expected points and a per-component breakdown to every player."""
    under = _underlying_lookup(underlying)
    team_factors = _team_strength_factors(players)
    horizon = int(cfg["horizon"]["gameweeks"])
    decay = float(cfg["horizon"]["decay"])

    scored: list[dict] = []
    for p in players:
        row = dict(p)
        prior = priors.get(p["id"], {})
        stats = under.get(p["id"], {})

        row["expected_minutes"] = _expected_minutes(cfg, p, prior)
        row["availability"] = _availability(cfg, p)
        row["expected_minutes"] *= row["availability"]

        rates = _blended_rates(cfg, p, prior, stats)
        row.update({f"rate_{k}": v for k, v in rates.items()})

        gw_scores, breakdown = _score_horizon(
            cfg, row, rates, fixtures_by_team.get(p["team_id"], []),
            team_factors, current_event, horizon,
        )
        row["xp_by_gw"] = gw_scores
        row["xp_next"] = gw_scores[0] if gw_scores else 0.0
        row["own_xp_horizon"] = sum(
            g * (decay ** i) for i, g in enumerate(gw_scores)
        )
        row["breakdown"] = breakdown
        row["fixture_count"] = len(fixtures_by_team.get(p["team_id"], []))
        row["fixtures"] = fixtures_by_team.get(p["team_id"], [])
        row["stats_matched"] = bool(stats.get("matched", False))
        row["has_prior"] = bool(prior)
        scored.append(row)

    _apply_ensemble(cfg, scored)
    return scored


# ---------------------------------------------------------------------- #
# Minutes and availability
# ---------------------------------------------------------------------- #


def _expected_minutes(cfg: dict, p: dict, prior: dict) -> float:
    """PHASE 1 PLACEHOLDER minutes estimate.

    Uses current-season minutes per fixture where any exist, otherwise falls
    back to prior-season load, otherwise to a price-implied guess for new
    signings and promoted players. Phase 2 replaces this entirely.
    """
    m = cfg["minutes"]
    current_minutes = p.get("minutes", 0.0)
    starts = p.get("starts", 0.0)

    if p["position"] == "GK":
        return _gk_expected_minutes(m, p, prior)

    if current_minutes > 0 and starts > 0:
        per_appearance = current_minutes / max(starts, 1.0)
        return float(min(90.0, max(10.0, per_appearance)))

    prior_minutes = prior.get("minutes", 0.0)
    if prior_minutes > 0:
        if prior_minutes >= m["nailed_threshold_minutes"]:
            return float(m["nailed_minutes"])
        if prior_minutes >= m["fringe_threshold_minutes"]:
            span = m["nailed_threshold_minutes"] - m["fringe_threshold_minutes"]
            frac = (prior_minutes - m["fringe_threshold_minutes"]) / span
            return float(
                m["fringe_minutes"] + frac * (m["nailed_minutes"] - m["fringe_minutes"])
            )
        return float(m["bench_minutes"] + (prior_minutes / m["fringe_threshold_minutes"])
                     * (m["fringe_minutes"] - m["bench_minutes"]))

    # No Premier League history at all: lean on what the club paid for, which
    # FPL price is a decent proxy for.
    buckets = sorted(
        ((float(k), float(v)) for k, v in m["unknown_minutes_by_price"].items()),
        reverse=True,
    )
    for threshold, minutes in buckets:
        if p["price"] >= threshold:
            return minutes
    return float(buckets[-1][1])


def _gk_expected_minutes(m: dict, p: dict, prior: dict) -> float:
    """A keeper plays 90 or nothing, so model the probability he starts.

    Expected minutes are 90 x P(start). Treating a keeper who played half the
    season as a 45-minute-per-match player would make him look like a viable
    starter for FPL purposes, when in reality he is a coin flip on any given
    weekend and belongs on the bench at 4.0m.
    """
    full = float(m["gk_full_season_minutes"])
    current, starts = p.get("minutes", 0.0), p.get("starts", 0.0)
    if current > 0 and starts >= 3:
        # Current-season evidence: matches started as a share of matches played
        # by the squad so far, inferred from his own minutes.
        matches_so_far = max(starts, round(current / 90.0))
        p_start = min(1.0, current / (90.0 * matches_so_far))
    elif prior.get("minutes", 0.0) > 0:
        share = min(1.0, prior["minutes"] / full)
        # Sharpen: keepers cluster at nailed or reserve, not in the middle.
        p_start = share ** 1.6
    else:
        p_start = 0.35 if p["price"] >= 4.5 else 0.15
    return float(90.0 * max(0.0, min(1.0, p_start)))


def _availability(cfg: dict, p: dict) -> float:
    av = cfg["availability"]
    if p.get("status") in av["excluded_statuses"]:
        return 0.0
    chance = p.get("chance_next")
    if chance is not None:
        table = {float(k): float(v) for k, v in av["chance_multiplier"].items()}
        nearest = min(table, key=lambda k: abs(k - float(chance)))
        return table[nearest]
    if p.get("status") == "d":
        return float(av["doubtful_default"])
    return 1.0


# ---------------------------------------------------------------------- #
# Rates
# ---------------------------------------------------------------------- #


def _blended_rates(cfg: dict, p: dict, prior: dict, stats: dict) -> dict[str, float]:
    """Per-90 rates, current season shrunk toward prior season."""
    k = float(cfg["prior"]["shrinkage_k_minutes"])
    cur_min = float(p.get("minutes", 0.0))
    w_cur = cur_min / (cur_min + k) if (cur_min + k) > 0 else 0.0

    cur = _per90(p, cur_min)
    pri = _prior_rates(prior, stats)

    out: dict[str, float] = {}
    for key in ("xg90", "xa90", "saves90", "bonus90", "defact90", "defcon_rate"):
        c, r = cur.get(key), pri.get(key)
        if c is None and r is None:
            out[key] = 0.0
        elif c is None:
            out[key] = r
        elif r is None:
            out[key] = c
        else:
            out[key] = w_cur * c + (1.0 - w_cur) * r
    out["shrinkage_weight_current"] = w_cur
    return out


def _per90(p: dict, minutes: float) -> dict[str, float | None]:
    if minutes < 90:
        return {}
    f = 90.0 / minutes
    xg = p.get("expected_goals") or p.get("goals_scored", 0.0)
    xa = p.get("expected_assists") or p.get("assists", 0.0)
    return {
        "xg90": xg * f,
        "xa90": xa * f,
        "saves90": p.get("saves", 0.0) * f,
        "bonus90": p.get("bonus", 0.0) * f,
        "defcon_rate": _defcon_rate(p.get("defensive_contribution", 0.0), minutes),
    }


def _prior_rates(prior: dict, stats: dict) -> dict[str, float | None]:
    out: dict[str, float | None] = {}

    # FBref/Understat first: unambiguous raw counts, better xG models.
    if stats.get("matched") and stats.get("u_minutes", 0.0) >= 270:
        for src, dst in (("u_xg90", "xg90"), ("u_xa90", "xa90"), ("u_defact90", "defact90")):
            val = stats.get(src)
            if val is not None and not _isnan(val):
                out[dst] = float(val)

    pm = prior.get("minutes", 0.0)
    if pm >= 270:
        f = 90.0 / pm
        if "xg90" not in out:
            out["xg90"] = (prior.get("expected_goals") or prior.get("goals_scored", 0.0)) * f
        if "xa90" not in out:
            out["xa90"] = (prior.get("expected_assists") or prior.get("assists", 0.0)) * f
        out["saves90"] = prior.get("saves", 0.0) * f
        out["bonus90"] = prior.get("bonus", 0.0) * f
        out["defcon_rate"] = _defcon_rate(prior.get("defensive_contribution", 0.0), pm)
    return out


def _defcon_rate(raw: float, minutes: float) -> float | None:
    """FPL's `defensive_contribution` counts DefCon events, not raw actions.

    Guard against the alternative reading: if the value is large relative to
    matches played it must be raw actions, so scale it down to an event rate
    via the Poisson tail instead of treating it as one event per match.
    """
    if minutes < 270 or raw <= 0:
        return None
    matches = minutes / 90.0
    per_match = raw / matches
    if per_match <= 1.05:
        return min(1.0, per_match)
    return None  # looks like raw actions; the defact90 path handles it


# ---------------------------------------------------------------------- #
# Horizon scoring
# ---------------------------------------------------------------------- #


def _score_horizon(
    cfg: dict,
    p: dict,
    rates: dict,
    fixtures: list[dict],
    team_factors: dict[int, dict],
    current_event: int,
    horizon: int,
) -> tuple[list[float], dict]:
    sc = cfg["scoring"]
    fx = cfg["fixtures"]
    pos = p["position"]
    em = p["expected_minutes"]

    if pos == "GK":
        # Starts implies 90 minutes, so both probabilities collapse to P(start).
        p60 = max(0.0, min(0.98, em / 90.0))
        p_any = p60
    else:
        p60 = max(0.0, min(0.95, (em - 20.0) / 50.0))
        p_any = max(0.0, min(0.98, em / 25.0))
    minutes_share = em / 90.0

    atk_map = {int(k): float(v) for k, v in fx["attack_multiplier"].items()}
    def_map = {int(k): float(v) for k, v in fx["defence_multiplier"].items()}
    factors = team_factors.get(p["team_id"], {"attack": 1.0, "defence": 1.0})

    by_event: dict[int, list[dict]] = {}
    for f in fixtures:
        by_event.setdefault(f["event"], []).append(f)

    gw_scores: list[float] = []
    totals = {
        "goals": 0.0, "assists": 0.0, "clean_sheets": 0.0, "conceded": 0.0,
        "saves": 0.0, "defcon": 0.0, "bonus": 0.0, "appearance": 0.0,
    }

    for offset in range(horizon):
        event = current_event + offset
        event_total = 0.0
        for f in by_event.get(event, []):
            home = fx["home_advantage"] if f["is_home"] else 1.0
            atk = atk_map.get(f["fdr"], 1.0) * home * factors["attack"]
            dfn = def_map.get(f["fdr"], 1.0) * home * factors["defence"]

            goals = rates["xg90"] * minutes_share * atk * sc["goal"][pos]
            assists = rates["xa90"] * minutes_share * atk * sc["assist"]

            p_cs = max(0.02, min(0.65, float(fx["base_clean_sheet_prob"]) * dfn))
            clean = p_cs * sc["clean_sheet"][pos] * p60

            conceded = 0.0
            if pos in ("GK", "DEF"):
                lam = -math.log(max(p_cs, 1e-6))
                conceded = -0.5 * lam * p60

            saves = 0.0
            if pos == "GK":
                # Weaker defences face more shots, so saves scale inversely.
                saves = rates["saves90"] * minutes_share / max(dfn, 0.4) / sc["saves_per_point"]

            defcon = _defcon_points(cfg, pos, rates, em) * sc["defcon_points"]
            bonus = rates["bonus90"] * minutes_share * (0.5 + 0.5 * atk)
            appearance = sc["appearance_60plus"] * p60 + sc["appearance_under60"] * (p_any - p60)

            totals["goals"] += goals
            totals["assists"] += assists
            totals["clean_sheets"] += clean
            totals["conceded"] += conceded
            totals["saves"] += saves
            totals["defcon"] += defcon
            totals["bonus"] += bonus
            totals["appearance"] += appearance

            event_total += (
                goals + assists + clean + conceded + saves + defcon + bonus + appearance
            )
        gw_scores.append(round(event_total, 3))

    return gw_scores, {k: round(v, 3) for k, v in totals.items()}


def _defcon_points(cfg: dict, pos: str, rates: dict, em: float) -> float:
    """Probability of hitting the defensive-contribution threshold.

    Preferred path is a Poisson tail on FBref defensive actions per 90, scaled
    to expected minutes. Where FBref is unavailable we fall back to FPL's own
    DefCon event rate.
    """
    if pos == "GK":
        return 0.0
    threshold = int(cfg["scoring"]["defcon_threshold"][pos])
    actions90 = rates.get("defact90", 0.0)
    if actions90 and actions90 > 0:
        lam = actions90 * (em / 90.0)
        return _poisson_at_least(threshold, lam)
    rate = rates.get("defcon_rate", 0.0) or 0.0
    return float(min(1.0, rate)) * min(1.0, em / 70.0)


def _poisson_at_least(k: int, lam: float) -> float:
    if lam <= 0:
        return 0.0
    cdf, term = 0.0, math.exp(-lam)
    for i in range(k):
        if i > 0:
            term *= lam / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def _underlying_lookup(underlying: pd.DataFrame) -> dict[int, dict]:
    """Index the scraped stats frame by FPL element id."""
    if underlying is None or underlying.empty or "id" not in underlying.columns:
        return {}
    return {int(row["id"]): dict(row) for _, row in underlying.iterrows()}


def _isnan(value) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def _team_strength_factors(players: list[dict]) -> dict[int, dict]:
    """Normalise FPL's own team strength ratings to a mean of 1.0.

    FDR is a coarse 1-5 integer. Team strength adds resolution: two fixtures
    both rated FDR 3 are not equally good if one side scores 40% more.
    """
    seen: dict[int, dict] = {}
    for p in players:
        if p["team_id"] in seen:
            continue
        seen[p["team_id"]] = {
            "attack": (p["team_strength_attack_home"] + p["team_strength_attack_away"]) / 2.0,
            "defence": (p["team_strength_defence_home"] + p["team_strength_defence_away"]) / 2.0,
        }
    if not seen:
        return {}
    mean_atk = sum(v["attack"] for v in seen.values()) / len(seen)
    mean_def = sum(v["defence"] for v in seen.values()) / len(seen)
    return {
        tid: {
            "attack": (v["attack"] / mean_atk) if mean_atk else 1.0,
            # Higher defence rating means harder to score against, so a stronger
            # defence should raise clean-sheet odds.
            "defence": (v["defence"] / mean_def) if mean_def else 1.0,
        }
        for tid, v in seen.items()
    }


# ---------------------------------------------------------------------- #
# Ensemble
# ---------------------------------------------------------------------- #


def _apply_ensemble(cfg: dict, scored: list[dict]) -> None:
    weights = dict(cfg["ensemble"]["weights"])
    w_own = float(weights.get("own_model", 1.0))
    w_ep = float(weights.get("fpl_ep_next", 0.0))

    for p in scored:
        own_next = p["xp_next"]
        ep = p.get("ep_next", 0.0)
        sources = {"own_model": own_next}
        available_w = w_own
        if ep and ep > 0:
            sources["fpl_ep_next"] = ep
            available_w += w_ep

        if available_w <= 0:
            blended_next = own_next
        else:
            blended_next = (
                w_own * own_next + (w_ep * ep if "fpl_ep_next" in sources else 0.0)
            ) / available_w

        ratio = 1.0
        if own_next > 0.15:
            ratio = max(0.5, min(2.0, blended_next / own_next))
        elif blended_next > 0:
            ratio = 1.0

        p["consensus_xp"] = round(p["own_xp_horizon"] * ratio, 3)
        p["consensus_xp_next"] = round(blended_next, 3)
        p["ensemble_ratio"] = round(ratio, 3)
        p["sources"] = sources
        p["source_disagreement"] = round(abs(own_next - ep), 3) if ep else None
