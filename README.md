# FPL Edge

Produces the optimal legal Fantasy Premier League squad each gameweek and
delivers it as a one-page HTML brief. Runs on GitHub Actions. Costs nothing.

Phase 1 (this release): FPL API client, FBref/Understat enrichment, an
interpretable expected-points model, and an integer programme that returns a
legal, budget-valid 15 with the XI, bench, formation and captain.

Phase 2 (next): minutes and rotation model, free crowd signal.
Phase 3: multi-week transfer planner.

## Files

| File | What it does |
| --- | --- |
| `fpl_config.yaml` | Every weight, threshold and rule. Nothing numeric is hard-coded elsewhere. |
| `fpl_api.py` | Official FPL API client with disk cache and rate limiting. |
| `fpl_stats.py` | FBref and Understat via `soccerdata`, with name matching to FPL ids. |
| `fpl_model.py` | Expected-points model and the ensemble blend. |
| `fpl_solver.py` | Binary MILP abstraction. PuLP/CBC primary, scipy/HiGHS fallback. |
| `fpl_optimiser.py` | The squad programme, plus an independent legality check. |
| `fpl_brief.py` | The HTML brief and the JSON export. |
| `run_fpl_edge.py` | Entry point. |
| `test_fpl_edge.py` | Offline self-test on a synthetic league. No network needed. |

## Running it

```
pip install -r requirements.txt
python run_fpl_edge.py            # full run
python run_fpl_edge.py --no-stats # FPL API only, about 90 seconds
python test_fpl_edge.py           # offline self-test
```

Outputs `fpl_edge_brief.html` and `fpl_edge_squad.json`.

## The model in one page

**Shrinkage, not switching.** Current-season rates are weighted
`mins / (mins + 450)` against prior-season rates. At Gameweek 1 that weight is
zero, so the model runs entirely on last season's underlying numbers. There is
no arbitrary "ignore form until Gameweek 6" rule.

**Points are built, not extrapolated.** Goals come from xG, assists from xA,
clean sheets from a fixture-adjusted probability, goals conceded from a Poisson
inversion of that probability, defensive contributions from a Poisson tail on
defensive actions per 90. This is what lets the model say a player is
underperforming his underlying numbers rather than just projecting his returns
forward.

**Minutes gate everything.** Every rate is per 90 and then multiplied by
expected minutes. Goalkeepers are modelled as binary starters because they play
90 or nothing. The Phase 1 minutes estimate is deliberately crude and is the
single biggest thing Phase 2 improves.

**The ensemble blends per gameweek, not per horizon.** Our component model owns
the fixture shape across the horizon; FPL's own `ep_next` adjusts the level for
the next gameweek only. Blending a horizon total against a one-week number
would be a units error. Weights are in `fpl_config.yaml` and renormalise over
whatever sources actually loaded, so a failed FBref scrape degrades the answer
rather than killing the run.

**Squad, XI and captain are solved together.** Picking the best 15 and then
picking an XI from it gives a worse answer, because the best 15 in isolation
over-invests in players who end up on the bench. Solving jointly lets the
programme deliberately buy cheap enablers to fund a stronger XI. Bench players
carry a 10% weight in the objective: zero buys pure 4.0m junk with no cover,
100% wastes budget on points that never score.

## Rules encoded (2026/27)

Squad of 15 (2 GK, 5 DEF, 5 MID, 3 FWD), 100.0m budget, maximum 3 per club.
Starting XI of 11: exactly 1 GK, 3 to 5 DEF, 2 to 5 MID, 1 to 3 FWD. Captain
scores double. Free transfers roll to a cap of 5; extra transfers cost 4 points.
Defensive contribution points are unchanged: 10 actions for defenders, 12 for
midfielders and forwards, 2 points, capped at 2 per match.

## Cost

Zero. Official FPL API needs no key. FBref and Understat are scraped politely
through `soccerdata` with caching. PuLP, CBC, HiGHS and scipy are open source.
GitHub Actions is free. SMTP delivery uses a Gmail app password if you want it.

## Secrets

Only needed for email. Set in Settings, Secrets and variables, Actions:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_TO`. Never commit
these.
