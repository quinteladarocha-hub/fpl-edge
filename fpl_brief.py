"""The gameweek brief: one self-contained HTML file you can read in five minutes.

Design brief was navy and teal. The register is a research note rather than a
marketing page: dense, tabular, monospaced numerals, one bold element. That
bold element is the pitch - the XI laid out in its actual formation, each
player carrying a stacked bar showing where his expected points come from.
Everything else stays quiet.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

COMPONENTS = [
    ("goals", "Goals", "#12B0A0"),
    ("assists", "Assists", "#3FC7B4"),
    ("clean_sheets", "Clean sheets", "#6FDBCB"),
    ("defcon", "DefCon", "#9BE7DB"),
    ("bonus", "Bonus", "#4B7FA8"),
    ("appearance", "Minutes", "#1C4E76"),
]

CSS = """
:root{
  --navy-900:#071A2C; --navy-700:#0E2C47; --navy-500:#17456E;
  --teal-500:#12B0A0; --teal-300:#6FDBCB; --teal-050:#E4F5F2;
  --paper:#F1F5F7; --card:#FFFFFF; --line:#D3DFE6;
  --ink:#0B2136; --muted:#5C7285; --warn:#B4771F;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 64px}
h1,h2,h3,.eyebrow,th{font-family:"Barlow Condensed","Oswald",
  "Helvetica Neue",Arial,sans-serif;letter-spacing:.02em}
.num{font-family:ui-monospace,"SF Mono","Roboto Mono",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}

header{background:var(--navy-900);color:#fff;padding:28px 0 24px;
  border-bottom:3px solid var(--teal-500)}
header .wrap{padding-bottom:0}
.brandline{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:38px;font-weight:600;text-transform:uppercase;line-height:1}
.gw{color:var(--teal-300);font-size:20px;text-transform:uppercase;font-weight:500}
.stamp{color:#8FA9BF;font-size:12px;margin-top:6px}

.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line);margin:24px 0 32px}
.stat{background:var(--card);padding:12px 14px}
.stat .k{font-size:10px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--muted);margin-bottom:3px}
.stat .v{font-size:24px;font-weight:600;line-height:1.1;color:var(--navy-700)}
.stat .v.accent{color:var(--teal-500)}
.stat .sub{font-size:11px;color:var(--muted);margin-top:2px}

.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.14em;
  color:var(--muted);border-bottom:1px solid var(--line);
  padding-bottom:6px;margin:36px 0 16px;display:flex;
  justify-content:space-between;align-items:baseline}
.eyebrow span{color:var(--muted);letter-spacing:.02em;font-size:11px}

.pitch{background:
    linear-gradient(180deg,var(--navy-700) 0%,var(--navy-900) 100%);
  border-radius:2px;padding:26px 18px;position:relative;overflow:hidden}
.pitch::before{content:"";position:absolute;inset:12px;
  border:1px solid rgba(111,219,203,.22);border-radius:2px;pointer-events:none}
.pitch::after{content:"";position:absolute;left:12px;right:12px;top:50%;
  border-top:1px solid rgba(111,219,203,.16);pointer-events:none}
.row{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;
  margin-bottom:16px;position:relative;z-index:1}
.row:last-child{margin-bottom:0}

.player{background:rgba(255,255,255,.96);border-radius:2px;width:132px;
  padding:8px 9px 7px;border-top:3px solid var(--teal-500)}
.player.cap{border-top-color:#FFC94A}
.player .nm{font-weight:600;font-size:13px;line-height:1.2;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.player .meta{display:flex;justify-content:space-between;font-size:10px;
  color:var(--muted);margin-top:2px}
.player .xp{font-size:17px;font-weight:600;color:var(--navy-700);margin-top:3px}
.player .xp small{font-size:9px;color:var(--muted);font-weight:400;
  text-transform:uppercase;letter-spacing:.06em;margin-left:3px}
.badge{display:inline-block;background:#FFC94A;color:var(--navy-900);
  font-size:9px;font-weight:700;padding:0 4px;border-radius:2px;
  vertical-align:middle;margin-left:4px;letter-spacing:.04em}
.badge.v{background:var(--teal-050);color:var(--navy-500)}

.bar{display:flex;height:5px;margin-top:5px;border-radius:1px;
  overflow:hidden;background:#E8EEF2}
.bar i{display:block;height:100%}

.bench{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.bench .player{border-top-color:var(--muted);opacity:.9}

table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--line)}
th{background:var(--navy-700);color:#fff;font-size:11px;font-weight:500;
  text-transform:uppercase;letter-spacing:.07em;padding:8px 9px;text-align:right}
th:first-child,th.l{text-align:left}
td{padding:7px 9px;border-top:1px solid var(--line);text-align:right;font-size:13px}
td:first-child,td.l{text-align:left}
tbody tr:nth-child(even){background:#FAFCFD}
tr.starter td:first-child{border-left:3px solid var(--teal-500)}
tr.benched td:first-child{border-left:3px solid var(--line);color:var(--muted)}
.pos{display:inline-block;width:30px;font-size:10px;color:var(--muted);
  letter-spacing:.06em}
.flag{font-size:10px;color:var(--warn)}

.note{background:var(--teal-050);border-left:3px solid var(--teal-500);
  padding:12px 14px;font-size:13px;margin-top:14px}
.note.warn{background:#FDF4E4;border-left-color:var(--warn)}
.note b{font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;
  color:var(--muted);margin-top:10px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:1px;
  margin-right:4px;vertical-align:-1px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:24px}
@media(max-width:760px){.two{grid-template-columns:1fr}
  .player{width:calc(33.333% - 7px)}h1{font-size:28px}}
@media(prefers-reduced-motion:no-preference){
  .player{animation:rise .4s ease both}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1}}}
"""


def render(cfg: dict, result: dict, context: dict) -> str:
    gw = context["event"]
    parts: list[str] = []
    parts.append(f"<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    parts.append(f"<title>FPL Edge - Gameweek {gw}</title>")
    parts.append(
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
        "family=Barlow+Condensed:wght@500;600&family=Inter:wght@400;600&display=swap'>"
    )
    parts.append(f"<style>{CSS}</style></head><body>")
    parts.append(_header(gw, context))
    parts.append("<div class='wrap'>")
    parts.append(_strip(result, context))
    parts.append(_pitch(result))
    parts.append(_bench(result))
    parts.append(_squad_table(result))
    parts.append("<div class='two'>")
    parts.append(_shortlist("Captaincy shortlist", context["captain_shortlist"],
                            "xP over horizon"))
    parts.append(_shortlist("Differentials", context["differential_shortlist"],
                            "Owned by", ownership=True))
    parts.append("</div>")
    parts.append(_data_quality(cfg, result, context))
    parts.append("</div></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------- #


def _header(gw: int, context: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    deadline = context.get("deadline", "")
    if deadline:
        try:
            dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            deadline = dt.strftime("%a %d %b, %H:%M UTC")
        except ValueError:
            pass
    return (
        "<header><div class='wrap'><div class='brandline'>"
        "<h1>FPL Edge</h1>"
        f"<div class='gw'>Gameweek {gw}</div></div>"
        f"<div class='stamp'>Deadline {html.escape(deadline)} &middot; "
        f"built {stamp} &middot; horizon "
        f"{context['horizon']} gameweeks</div></div></header>"
    )


def _strip(result: dict, context: dict) -> str:
    cap = result["captain"]
    cells = [
        ("Projected points", f"{result['total_xp']:.1f}", "XI plus captain, accent"),
        ("Formation", result["formation"], "1 GK plus outfield"),
        ("Squad cost", f"{result['cost']:.1f}m", "of 100.0m"),
        ("In the bank", f"{result['bank']:.1f}m", "unspent"),
        ("Captain", cap["name"] if cap else "-",
         f"{cap['consensus_xp']:.1f} xP doubled" if cap else ""),
        ("Vice", result["vice_captain"]["name"] if result["vice_captain"] else "-", ""),
    ]
    out = ["<div class='strip'>"]
    for k, v, sub in cells:
        accent = " accent" if "accent" in sub else ""
        sub = sub.replace(", accent", "")
        out.append(
            f"<div class='stat'><div class='k'>{html.escape(k)}</div>"
            f"<div class='v{accent} num'>{html.escape(str(v))}</div>"
            f"<div class='sub'>{html.escape(sub)}</div></div>"
        )
    out.append("</div>")
    return "".join(out)


def _pitch(result: dict) -> str:
    rows = {pos: [p for p in result["xi"] if p["position"] == pos]
            for pos in ("GK", "DEF", "MID", "FWD")}
    out = [
        "<div class='eyebrow'>Starting XI"
        f"<span>{result['formation']} &middot; {result['xi_xp']:.1f} xP before captaincy</span>"
        "</div><div class='pitch'>"
    ]
    for pos in ("GK", "DEF", "MID", "FWD"):
        if not rows[pos]:
            continue
        out.append("<div class='row'>")
        for p in sorted(rows[pos], key=lambda q: -q["consensus_xp"]):
            out.append(_card(p, vice=result["vice_captain"]))
        out.append("</div>")
    out.append("</div>")
    out.append(_legend())
    return "".join(out)


def _bench(result: dict) -> str:
    out = [
        "<div class='eyebrow'>Bench"
        "<span>ordered for autosubs, goalkeeper first</span></div>"
        "<div class='bench'>"
    ]
    for p in result["bench"]:
        out.append(_card(p, bench=True))
    out.append("</div>")
    return "".join(out)


def _card(p: dict, bench: bool = False, vice: dict | None = None) -> str:
    cls = "player cap" if p.get("is_captain") else "player"
    badge = ""
    if p.get("is_captain"):
        badge = "<span class='badge'>C</span>"
    elif vice and p["id"] == vice["id"]:
        badge = "<span class='badge v'>V</span>"
    return (
        f"<div class='{cls}'>"
        f"<div class='nm'>{html.escape(p['name'])}{badge}</div>"
        f"<div class='meta'><span>{html.escape(p['team'])} &middot; {p['position']}</span>"
        f"<span class='num'>{p['price']:.1f}m</span></div>"
        f"<div class='xp num'>{p['consensus_xp']:.1f}<small>xP</small></div>"
        f"{_signal_bar(p)}"
        "</div>"
    )


def _signal_bar(p: dict) -> str:
    b = p.get("breakdown", {})
    positives = [(k, max(0.0, float(b.get(k, 0.0)))) for k, _, _ in COMPONENTS]
    total = sum(v for _, v in positives)
    if total <= 0:
        return "<div class='bar'></div>"
    segments = []
    for (key, _, colour) in COMPONENTS:
        share = max(0.0, float(b.get(key, 0.0))) / total * 100.0
        if share > 0.4:
            segments.append(f"<i style='width:{share:.1f}%;background:{colour}'></i>")
    return f"<div class='bar'>{''.join(segments)}</div>"


def _legend() -> str:
    items = "".join(
        f"<span><i style='background:{c}'></i>{html.escape(label)}</span>"
        for _, label, c in COMPONENTS
    )
    return f"<div class='legend'>{items}</div>"


def _squad_table(result: dict) -> str:
    out = [
        "<div class='eyebrow'>Signal breakdown"
        "<span>expected points by component over the horizon</span></div>",
        "<table><thead><tr><th class='l'>Player</th><th class='l'>Club</th>"
        "<th>Price</th><th>Mins</th><th>Fix</th>",
    ]
    for _, label, _ in COMPONENTS:
        out.append(f"<th>{html.escape(label)}</th>")
    out.append("<th>Own xP</th><th>FPL ep</th><th>Consensus</th></tr></thead><tbody>")

    ordered = sorted(
        result["squad"],
        key=lambda p: (not p["is_starter"],
                       ("GK", "DEF", "MID", "FWD").index(p["position"]),
                       -p["consensus_xp"]),
    )
    for p in ordered:
        cls = "starter" if p["is_starter"] else "benched"
        flags = []
        if not p.get("stats_matched"):
            flags.append("no FBref match")
        if not p.get("has_prior"):
            flags.append("no PL history")
        if p.get("availability", 1.0) < 1.0:
            flags.append(f"{p['availability']:.0%} fit")
        flag = f" <span class='flag'>{html.escape(', '.join(flags))}</span>" if flags else ""
        cap = " <span class='badge'>C</span>" if p.get("is_captain") else ""

        out.append(
            f"<tr class='{cls}'><td class='l'><span class='pos'>{p['position']}</span>"
            f"{html.escape(p['name'])}{cap}{flag}</td>"
            f"<td class='l'>{html.escape(p['team'])}</td>"
            f"<td class='num'>{p['price']:.1f}</td>"
            f"<td class='num'>{p['expected_minutes']:.0f}</td>"
            f"<td class='num'>{p['fixture_count']}</td>"
        )
        for key, _, _ in COMPONENTS:
            val = p.get("breakdown", {}).get(key, 0.0)
            out.append(f"<td class='num'>{val:.1f}</td>")
        ep = p.get("ep_next", 0.0)
        out.append(
            f"<td class='num'>{p['own_xp_horizon']:.1f}</td>"
            f"<td class='num'>{ep:.1f}</td>"
            f"<td class='num'><b>{p['consensus_xp']:.1f}</b></td></tr>"
        )
    out.append("</tbody></table>")
    out.append(
        "<div class='note'><b>Reading this.</b> Own xP is this project's "
        "component model over the horizon. FPL ep is the official one-gameweek "
        "expectation, used only to nudge the level. Consensus is the blend the "
        "optimiser actually maximises. A large gap between the two is where the "
        "sources disagree and where you should apply your own judgement.</div>"
    )
    return "".join(out)


def _shortlist(title: str, rows: list[dict], caption: str, ownership: bool = False) -> str:
    out = [
        f"<div><div class='eyebrow'>{html.escape(title)}"
        f"<span>{html.escape(caption)}</span></div>",
        "<table><thead><tr><th class='l'>Player</th><th class='l'>Club</th>"
        "<th>Price</th>",
        "<th>Own %</th>" if ownership else "<th>Mins</th>",
        "<th>xP</th></tr></thead><tbody>",
    ]
    for p in rows:
        third = (f"{p['selected_by_percent']:.1f}" if ownership
                 else f"{p['expected_minutes']:.0f}")
        out.append(
            f"<tr><td class='l'>{html.escape(p['name'])}</td>"
            f"<td class='l'>{html.escape(p['team'])}</td>"
            f"<td class='num'>{p['price']:.1f}</td>"
            f"<td class='num'>{third}</td>"
            f"<td class='num'><b>{p['consensus_xp']:.1f}</b></td></tr>"
        )
    out.append("</tbody></table></div>")
    return "".join(out)


def _data_quality(cfg: dict, result: dict, context: dict) -> str:
    meta = context.get("stats_meta", {})
    sources = []
    sources.append(("Official FPL API", True, f"{context['player_count']} players"))
    sources.append(("FBref via soccerdata", meta.get("fbref", False),
                    f"{meta.get('matched', 0)} players matched"))
    sources.append(("Understat via soccerdata", meta.get("understat", False),
                    "xG cross-check"))
    sources.append(("Prior-season FPL history", context.get("prior_count", 0) > 0,
                    f"{context.get('prior_count', 0)} players"))

    rows = "".join(
        f"<tr><td class='l'>{html.escape(name)}</td>"
        f"<td class='l'>{'live' if ok else 'unavailable'}</td>"
        f"<td class='l'>{html.escape(detail)}</td></tr>"
        for name, ok, detail in sources
    )

    warn = ""
    if not meta.get("fbref") and not meta.get("understat"):
        warn = (
            "<div class='note warn'><b>Underlying stats did not load.</b> "
            "The model fell back to official FPL data alone, so expected goals "
            "and defensive-contribution estimates are coarser than usual. "
            "Rerun the workflow later if FBref was rate limiting.</div>"
        )

    weights = ", ".join(f"{k} {v:.0%}" for k, v in cfg["ensemble"]["weights"].items())
    return (
        "<div class='eyebrow'>Data quality<span>what fed these numbers</span></div>"
        "<table><thead><tr><th class='l'>Source</th><th class='l'>Status</th>"
        f"<th class='l'>Detail</th></tr></thead><tbody>{rows}</tbody></table>"
        f"{warn}"
        f"<div class='note'><b>Solver</b> {html.escape(result['backend'])} on "
        f"{result['universe_size']} eligible players. <b>Ensemble</b> {html.escape(weights)}. "
        f"<b>Shrinkage</b> current-season rates carry weight "
        f"mins/(mins+{cfg['prior']['shrinkage_k_minutes']}), so a Gameweek 1 run "
        "is entirely prior-season driven. <b>Not yet live:</b> the minutes and "
        "rotation model, the crowd signal, and the multi-week transfer planner.</div>"
    )


def write_json(path: str, cfg: dict, result: dict, context: dict) -> None:
    payload = {
        "event": context["event"],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "formation": result["formation"],
        "cost": result["cost"],
        "bank": result["bank"],
        "total_xp": result["total_xp"],
        "captain": result["captain"]["name"] if result["captain"] else None,
        "vice_captain": result["vice_captain"]["name"] if result["vice_captain"] else None,
        "squad": [
            {
                "id": p["id"], "name": p["name"], "team": p["team"],
                "position": p["position"], "price": p["price"],
                "starter": p["is_starter"], "captain": p["is_captain"],
                "expected_minutes": round(p["expected_minutes"], 1),
                "consensus_xp": p["consensus_xp"],
                "own_xp": p["own_xp_horizon"],
                "breakdown": p["breakdown"],
            }
            for p in result["squad"]
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
