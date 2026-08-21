"""FPL Edge entry point. Phase 1: free core.

  python run_fpl_edge.py                 full run
  python run_fpl_edge.py --no-stats      skip FBref/Understat (fast, FPL API only)
  python run_fpl_edge.py --budget 101.3  optimise against a different budget
  python run_fpl_edge.py --email         send the brief over SMTP

Exit code is non-zero if the produced squad fails the independent legality
check, so a broken run fails the GitHub Actions job loudly rather than
emailing a nonsense team.
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
import yaml

import fpl_api
import fpl_brief
import fpl_model
import fpl_optimiser
import fpl_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fpl_edge")


def main() -> int:
    ap = argparse.ArgumentParser(description="FPL Edge - Phase 1")
    ap.add_argument("--config", default="fpl_config.yaml")
    ap.add_argument("--no-stats", action="store_true",
                    help="skip FBref/Understat enrichment")
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--event", type=int, default=None,
                    help="override the target gameweek")
    ap.add_argument("--email", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())

    client = fpl_api.FPLClient(cfg)
    log.info("fetching bootstrap-static")
    bootstrap = client.bootstrap()
    event = args.event or fpl_api.current_event(bootstrap)
    deadline = fpl_api.deadline_for_event(bootstrap, event)
    log.info("target gameweek %s, deadline %s", event, deadline or "unknown")

    players = fpl_api.players_frame(bootstrap)
    log.info("%s players in the game", len(players))

    log.info("fetching fixtures")
    fixtures = client.fixtures()
    horizon = int(cfg["horizon"]["gameweeks"])
    fixtures_by_team = fpl_api.upcoming_fixtures(fixtures, event, horizon)

    log.info("fetching prior-season history for the shortlist")
    shortlist = fpl_api.shortlist_for_prior(players, int(cfg["prior"]["max_lookups"]))
    priors = fpl_api.prior_season_totals(client, shortlist)
    log.info("prior-season history for %s players", len(priors))

    if args.no_stats:
        underlying, stats_meta = pd.DataFrame(), {"fbref": False, "understat": False}
        log.info("underlying stats skipped by flag")
    else:
        underlying, stats_meta = fpl_stats.load_underlying(cfg, players)

    log.info("scoring players")
    scored = fpl_model.build_scores(
        cfg, players, fixtures_by_team, priors, underlying, event
    )

    log.info("optimising squad")
    result = fpl_optimiser.optimise_squad(cfg, scored, budget=args.budget)

    errors = fpl_optimiser.validate(cfg, result)
    if errors:
        for e in errors:
            log.error("LEGALITY FAILURE: %s", e)
        return 1

    context = _context(cfg, scored, result, event, deadline, stats_meta, priors)

    html_path = cfg["output"]["html_path"]
    Path(html_path).write_text(fpl_brief.render(cfg, result, context))
    fpl_brief.write_json(cfg["output"]["json_path"], cfg, result, context)
    log.info("wrote %s and %s", html_path, cfg["output"]["json_path"])

    _print_summary(result)

    if args.email:
        _send_email(html_path, event)

    return 0


def _context(cfg, scored, result, event, deadline, stats_meta, priors) -> dict:
    size = int(cfg["output"]["shortlist_size"])
    ceiling = float(cfg["output"]["differential_ownership_ceiling"])
    playable = [p for p in scored
                if p["availability"] > 0 and p["expected_minutes"] >= 45]

    captain_pool = sorted(playable, key=lambda p: -p["consensus_xp"])[:size]
    differentials = sorted(
        [p for p in playable if p["selected_by_percent"] <= ceiling],
        key=lambda p: -p["consensus_xp"],
    )[:size]

    return {
        "event": event,
        "deadline": deadline,
        "horizon": cfg["horizon"]["gameweeks"],
        "player_count": len(scored),
        "prior_count": len(priors),
        "stats_meta": stats_meta,
        "captain_shortlist": captain_pool,
        "differential_shortlist": differentials,
    }


def _print_summary(result: dict) -> None:
    print()
    print(f"  Formation {result['formation']}    "
          f"cost {result['cost']:.1f}m    bank {result['bank']:.1f}m    "
          f"projected {result['total_xp']:.1f} pts")
    print(f"  Solver: {result['backend']}")
    print()
    for label, group in (("XI", result["xi"]), ("BENCH", result["bench"])):
        print(f"  {label}")
        for p in group:
            mark = " (C)" if p["is_captain"] else ""
            print(f"    {p['position']:<4}{p['name']:<20}{p['team']:<5}"
                  f"{p['price']:>5.1f}m  {p['consensus_xp']:>6.2f} xP{mark}")
        print()


def _send_email(html_path: str, event: int) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to_addr = os.environ.get("SMTP_TO", user)
    if not user or not password:
        log.warning("SMTP_USER or SMTP_PASS missing, email skipped")
        return

    msg = EmailMessage()
    msg["Subject"] = f"FPL Edge - Gameweek {event}"
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content("Your FPL Edge brief is attached. Open it in a browser.")
    msg.add_alternative(Path(html_path).read_text(), subtype="html")

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    log.info("brief emailed to %s", to_addr)


if __name__ == "__main__":
    sys.exit(main())
