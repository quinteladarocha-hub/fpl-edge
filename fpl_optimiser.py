"""Integer programme that picks the squad, the XI and the captain together.

The three decisions are solved jointly, not in sequence. Picking the best 15
and then picking an XI from it gives a worse answer, because the best 15 in
isolation over-invests in players who would end up on the bench. Solving them
together lets the programme deliberately buy cheap bench fodder to fund a
stronger XI.

Variables per player i:
  s_i  in squad
  x_i  in starting XI      (x_i <= s_i)
  c_i  is captain          (c_i <= x_i)

Objective:
  sum(x_i * xp_i)                      XI points
  + sum(c_i * xp_i)                    captain scores double, so count again
  + bench_weight * sum((s_i - x_i) * xp_i)   a little insurance on the bench

Constraints: squad size and shape, budget, max 3 per club, XI size and legal
formation ranges, exactly one captain.
"""

from __future__ import annotations

import logging

from fpl_solver import EQ, GE, LE, BinaryProgram

log = logging.getLogger("fpl_edge.optimiser")

POSITIONS = ("GK", "DEF", "MID", "FWD")


def optimise_squad(
    cfg: dict,
    players: list[dict],
    budget: float | None = None,
    locked_ids: set[int] | None = None,
    banned_ids: set[int] | None = None,
) -> dict:
    rules = cfg["rules"]
    opt = cfg["optimiser"]
    locked_ids = locked_ids or set()
    banned_ids = banned_ids or set()

    floor = float(opt["min_expected_minutes_for_squad"])
    universe = [
        p for p in players
        if p["id"] not in banned_ids
        and p["availability"] > 0.0
        and (p["expected_minutes"] >= floor or p["id"] in locked_ids)
    ]
    if len(universe) < rules["squad_size"]:
        raise RuntimeError("player universe too small after filtering")

    prog = BinaryProgram("fpl_edge_squad")
    s_idx, x_idx, c_idx = {}, {}, {}
    bench_w = float(opt["bench_weight"])
    bench_gk_w = float(opt["bench_gk_weight"])

    for i, p in enumerate(universe):
        xp = float(p["consensus_xp"])
        w = bench_gk_w if p["position"] == "GK" else bench_w
        # s_i carries the bench value; x_i carries the top-up to full value.
        s_idx[i] = prog.add_var(f"s{p['id']}", obj=w * xp)
        x_idx[i] = prog.add_var(f"x{p['id']}", obj=(1.0 - w) * xp)
        c_idx[i] = prog.add_var(f"c{p['id']}", obj=xp)

    idx = range(len(universe))

    # Linking: cannot start unless in squad, cannot captain unless starting.
    for i in idx:
        prog.add_constraint({x_idx[i]: 1.0, s_idx[i]: -1.0}, LE, 0.0, f"link_x_{i}")
        prog.add_constraint({c_idx[i]: 1.0, x_idx[i]: -1.0}, LE, 0.0, f"link_c_{i}")

    # Squad shape.
    prog.add_constraint({s_idx[i]: 1.0 for i in idx}, EQ, rules["squad_size"], "squad_size")
    for pos in POSITIONS:
        members = {s_idx[i]: 1.0 for i in idx if universe[i]["position"] == pos}
        prog.add_constraint(members, EQ, rules["squad_by_position"][pos], f"squad_{pos}")

    # Budget, in tenths of a million so the coefficients stay integral.
    budget_tenths = round(float(budget if budget is not None else rules["budget"]) * 10)
    prog.add_constraint(
        {s_idx[i]: float(universe[i]["price_tenths"]) for i in idx},
        LE, float(budget_tenths), "budget",
    )

    # Max three per club.
    clubs = {p["team_id"] for p in universe}
    for team_id in clubs:
        members = {s_idx[i]: 1.0 for i in idx if universe[i]["team_id"] == team_id}
        prog.add_constraint(members, LE, rules["max_per_club"], f"club_{team_id}")

    # Starting XI and legal formation.
    prog.add_constraint({x_idx[i]: 1.0 for i in idx}, EQ, rules["xi_size"], "xi_size")
    for pos in POSITIONS:
        members = {x_idx[i]: 1.0 for i in idx if universe[i]["position"] == pos}
        prog.add_constraint(members, GE, rules["xi_min"][pos], f"xi_min_{pos}")
        prog.add_constraint(members, LE, rules["xi_max"][pos], f"xi_max_{pos}")

    # Exactly one captain.
    prog.add_constraint({c_idx[i]: 1.0 for i in idx}, EQ, 1.0, "captain")

    # Forced inclusions (used by later phases for transfer-constrained runs).
    for i in idx:
        if universe[i]["id"] in locked_ids:
            prog.add_constraint({s_idx[i]: 1.0}, EQ, 1.0, f"lock_{i}")

    chosen, backend = prog.solve(time_limit=int(opt["solver_time_limit_seconds"]))
    chosen_set = set(chosen)

    squad, xi, captain = [], [], None
    for i in idx:
        if s_idx[i] in chosen_set:
            entry = dict(universe[i])
            entry["is_starter"] = x_idx[i] in chosen_set
            entry["is_captain"] = c_idx[i] in chosen_set
            squad.append(entry)
            if entry["is_starter"]:
                xi.append(entry)
            if entry["is_captain"]:
                captain = entry

    bench = [p for p in squad if not p["is_starter"]]
    bench.sort(key=lambda p: (p["position"] != "GK", -p["consensus_xp"]))
    xi.sort(key=lambda p: (POSITIONS.index(p["position"]), -p["consensus_xp"]))

    vice = max(
        (p for p in xi if not p["is_captain"]),
        key=lambda p: p["consensus_xp"],
        default=None,
    )

    counts = {pos: sum(1 for p in xi if p["position"] == pos) for pos in POSITIONS}
    formation = f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"
    cost = sum(p["price"] for p in squad)

    result = {
        "squad": squad,
        "xi": xi,
        "bench": bench,
        "captain": captain,
        "vice_captain": vice,
        "formation": formation,
        "cost": round(cost, 1),
        "bank": round(float(budget if budget is not None else rules["budget"]) - cost, 1),
        "xi_xp": round(sum(p["consensus_xp"] for p in xi), 2),
        "captain_bonus_xp": round(captain["consensus_xp"], 2) if captain else 0.0,
        "squad_xp": round(sum(p["consensus_xp"] for p in squad), 2),
        "backend": backend,
        "universe_size": len(universe),
    }
    result["total_xp"] = round(result["xi_xp"] + result["captain_bonus_xp"], 2)
    return result


def validate(cfg: dict, result: dict) -> list[str]:
    """Independent legality check. Never trust the solver blindly."""
    rules = cfg["rules"]
    errors: list[str] = []
    squad, xi = result["squad"], result["xi"]

    if len(squad) != rules["squad_size"]:
        errors.append(f"squad has {len(squad)} players, expected {rules['squad_size']}")
    for pos, want in rules["squad_by_position"].items():
        got = sum(1 for p in squad if p["position"] == pos)
        if got != want:
            errors.append(f"squad has {got} {pos}, expected {want}")

    cost = sum(p["price"] for p in squad)
    if cost > rules["budget"] + 1e-6:
        errors.append(f"squad costs {cost:.1f}m, over the {rules['budget']:.1f}m budget")

    from collections import Counter
    club_counts = Counter(p["team"] for p in squad)
    for club, count in club_counts.items():
        if count > rules["max_per_club"]:
            errors.append(f"{count} players from {club}, max is {rules['max_per_club']}")

    if len(xi) != rules["xi_size"]:
        errors.append(f"XI has {len(xi)} players, expected {rules['xi_size']}")
    for pos in POSITIONS:
        got = sum(1 for p in xi if p["position"] == pos)
        if got < rules["xi_min"][pos] or got > rules["xi_max"][pos]:
            errors.append(
                f"XI has {got} {pos}, legal range is "
                f"{rules['xi_min'][pos]}-{rules['xi_max'][pos]}"
            )

    if not all(p["id"] in {q["id"] for q in squad} for p in xi):
        errors.append("XI contains a player who is not in the squad")
    if sum(1 for p in xi if p["is_captain"]) != 1:
        errors.append("captain is not exactly one starting player")
    if len({p["id"] for p in squad}) != len(squad):
        errors.append("squad contains a duplicate player")

    return errors
