"""Render the board to a self-contained HTML page.

The page is a working surface, not a report: it is filtered, searched and
expanded. Descriptions are embedded so a job can be read without leaving the
page, truncated so the file stays well inside the artifact size limit.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .geo import HOME_LABEL, TIER_LABELS
from .authorization import profile_summary
from .store import (all_jobs, applied_at_map, latest_runs, recruiters_for,
                    sources_for, stats)

DESC_LIMIT = 5000

TIER_META = {
    0: (TIER_LABELS[0], "primary", f"Primary search area near {HOME_LABEL}."),
    1: (TIER_LABELS[1], "secondary", "Secondary preferred area."),
    2: ("Remote (US)", "remote", "Location-independent US roles."),
    3: ("Rest of US", "us", f"Ordered by distance from {HOME_LABEL}."),
    4: ("International", "intl", "Visa and relocation required."),
    5: ("Unspecified", "intl", "Location not stated by the source."),
}

SOURCE_LABEL = {
    "ats": "Direct ATS",
    "simplify": "Simplify",
    "vansh": "vanshb03",
    "linkedin": "LinkedIn",
    "wellfound": "Wellfound",
    "swelist": "swelist",
    "yc": "Y Combinator",
}


def _rows_payload(conn) -> list[dict]:
    payload = []
    sightings = sources_for(conn)
    recruiters = recruiters_for(conn)
    applied = applied_at_map(conn)
    for row in all_jobs(conn, active_only=False):
        desc = row["description"] or ""
        seen = sightings.get(row["key"], [])
        payload.append(
            {
                "k": row["key"],
                "c": row["company"],
                "t": row["title"],
                "u": row["url"],
                "p": row["place_label"] or "",
                "tier": row["tier"],
                "mi": round(row["distance_mi"]) if row["distance_mi"] is not None else None,
                "s": row["source"],
                "tm": (row["terms"] or "").replace("|", ", "),
                "sp": row["sponsorship"] if row["sponsorship"] not in (None, "Other") else "",
                "st": row["status"],
                "score": row["priority_score"],
                "fresh": row["freshness_score"],
                "posted": row["date_posted"] or row["first_seen"],
                "postedKnown": bool(row["date_posted"]),
                "discovered": row["first_seen"],
                "applicants": row["applicant_count"],
                "reasons": json.loads(row["score_reasons"] or "[]"),
                "fit": row["fit_score"],
                "report": row["fit_report_path"] or "",
                "tex": row["resume_tex_path"] or "",
                "pdf": row["resume_pdf_path"] or "",
                "ats": row["ats_vendor"] or "",
                "live": row["availability_status"],
                "d": desc[:DESC_LIMIT],
                "dt": len(desc) > DESC_LIMIT,
                "new": 1 if row["status"] == "NEW" else 0,
                # Feed order key: real posting time when the source gave one,
                # discovery time otherwise. Never a fabricated posting date.
                "when": row["date_posted"] or row["first_seen"],
                "seen": 1 if row["seen_at"] else 0,
                "appliedAt": applied.get(row["key"]),
                "reqAt": row["resume_requested_at"],
                "saved": 1 if row["saved"] else 0,
                "canon": row["canonical_url"] or "",
                "srcs": [
                    {"s": SOURCE_LABEL.get(x["source"], x["source"]), "u": x["url"] or ""}
                    for x in seen
                ] or [{"s": SOURCE_LABEL.get(row["source"], row["source"]), "u": row["url"] or ""}],
                "rec": [
                    {
                        "id": r["id"], "name": r["name"] or "", "profile": r["profile_url"] or "",
                        "msg": r["message_url"] or "", "status": r["status"],
                        "at": r["contacted_at"], "notes": r["notes"] or "",
                    }
                    for r in recruiters.get(row["key"], [])
                ],
            }
        )
    return payload


def _source_health(conn) -> list[dict]:
    out = []
    for run in latest_runs(conn):
        age_h = (time.time() - run["ran_at"]) / 3600
        out.append(
            {
                "name": SOURCE_LABEL.get(run["source"], run["source"]),
                "ok": bool(run["ok"]),
                "status": run["status"],
                "kept": run["kept"],
                "age": f"{age_h:.0f}h ago" if age_h >= 1 else "just now",
                "stale_days": (
                    int((time.time() - run["newest_posting"]) // 86400)
                    if run["newest_posting"] else None
                ),
                # The diagnostics number that matters: how old is the freshest
                # thing this feed actually returned, in hours.
                "newest_h": (
                    round((time.time() - run["newest_posting"]) / 3600, 1)
                    if run["newest_posting"] else None
                ),
                "note": run["note"] or "",
            }
        )
    return out


def render(conn, out_path: Path, max_tier: int = 4) -> Path:
    rows = [r for r in _rows_payload(conn) if r["tier"] <= max_tier]
    health = _source_health(conn)
    counts: dict[int, int] = {}
    for row in rows:
        counts[row["tier"]] = counts.get(row["tier"], 0) + 1
    described = sum(1 for r in rows if r["d"])
    generated = datetime.now(timezone.utc).astimezone().strftime("%B %d, %Y at %-I:%M %p")

    tiers_js = json.dumps({str(k): [v[0], v[1], v[2]] for k, v in TIER_META.items()})
    html = PAGE.replace("__ROWS__", json.dumps(rows, separators=(",", ":")))
    html = html.replace("__TIERS__", tiers_js)
    html = html.replace("__HEALTH__", json.dumps(health))
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__TOTAL__", str(len(rows)))
    html = html.replace("__DESCRIBED__", str(described))
    html = html.replace("__NJ__", str(counts.get(0, 0)))
    html = html.replace("__NY__", str(counts.get(1, 0)))
    html = html.replace("__PRIMARY_LABEL__", TIER_LABELS[0])
    html = html.replace("__SECONDARY_LABEL__", TIER_LABELS[1])
    html = html.replace("__AUTH__", profile_summary())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


PAGE = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proximity Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{
  --paper:#F5F7F9; --surface:#FFFFFF; --sunk:#EDF0F4;
  --ink:#11141A; --ink-2:#434B57; --ink-3:#6C7583;
  --line:#DDE2E9; --line-2:#C8D0DA;
  --nj:#0C6E5B; --ny:#1A5F96; --remote:#5E4694; --us:#95590F; --intl:#5A6472;
  --nj-wash:#E4F1ED; --ny-wash:#E3EDF6; --remote-wash:#ECE7F5; --us-wash:#F6ECDF; --intl-wash:#ECEFF3;
  --flag:#A32018; --flag-wash:#F8E8E6;
  --focus:#1A5F96;
  --shadow:0 1px 2px rgba(17,20,26,.05), 0 8px 24px -12px rgba(17,20,26,.18);
}
:root:not([data-theme="light"]){
  @media (prefers-color-scheme: dark){
    --paper:#0E1116; --surface:#161A21; --sunk:#1D222B;
    --ink:#EDF0F4; --ink-2:#AEB7C4; --ink-3:#7C8694;
    --line:#272D37; --line-2:#333B47;
    --nj:#4FCBAA; --ny:#71B4E8; --remote:#AE98E6; --us:#E0A458; --intl:#9AA5B4;
    --nj-wash:#11302A; --ny-wash:#12293C; --remote-wash:#231C38; --us-wash:#332413; --intl-wash:#212730;
    --flag:#F08076; --flag-wash:#381916;
    --focus:#71B4E8;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -14px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --paper:#0E1116; --surface:#161A21; --sunk:#1D222B;
  --ink:#EDF0F4; --ink-2:#AEB7C4; --ink-3:#7C8694;
  --line:#272D37; --line-2:#333B47;
  --nj:#4FCBAA; --ny:#71B4E8; --remote:#AE98E6; --us:#E0A458; --intl:#9AA5B4;
  --nj-wash:#11302A; --ny-wash:#12293C; --remote-wash:#231C38; --us-wash:#332413; --intl-wash:#212730;
  --flag:#F08076; --flag-wash:#381916;
  --focus:#71B4E8;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -14px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:16.5px; line-height:1.5; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1460px; margin:0 auto; padding-inline:26px; padding-block:0 64px}

/* ---------- masthead ---------- */
header{padding-block:40px 24px; border-bottom:2px solid var(--ink); margin-bottom:0}
.eyebrow{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:13px; font-weight:500;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3); margin:0 0 10px
}
h1{
  font-family:Archivo,system-ui,sans-serif; font-weight:700; font-size:clamp(30px,5.2vw,46px);
  letter-spacing:-.025em; line-height:1.02; margin:0 0 12px; text-wrap:balance
}
.lede{max-width:62ch; color:var(--ink-2); font-size:17.7px; margin:0}
.lede b{color:var(--ink); font-weight:600}
.notice{margin:14px 0 0;padding:9px 12px;border-left:3px solid var(--flag);background:var(--flag-wash);color:var(--ink-2);font-size:14.8px}

/* ---------- summary strip ---------- */
.strip{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:0; border-bottom:1px solid var(--line); background:var(--surface);
}
.stat{padding:16px 18px; border-right:1px solid var(--line)}
.stat:last-child{border-right:none}
.stat .n{
  font-family:"IBM Plex Mono",monospace; font-size:30.7px; font-weight:600;
  letter-spacing:-.02em; font-variant-numeric:tabular-nums; display:block; line-height:1.1
}
.stat .l{font-size:13px; letter-spacing:.07em; text-transform:uppercase; color:var(--ink-3); margin-top:4px; display:block}
.stat.is-nj .n{color:var(--nj)} .stat.is-ny .n{color:var(--ny)}

/* ---------- controls ---------- */
.controls{
  position:sticky; top:0; z-index:20; background:var(--paper);
  padding-block:14px; border-bottom:1px solid var(--line);
  display:flex; flex-wrap:wrap; gap:10px; align-items:center;
}
#q{
  flex:1 1 240px; min-width:0; padding:9px 12px; font:inherit; font-size:16.5px;
  color:var(--ink); background:var(--surface);
  border:1px solid var(--line-2); border-radius:3px;
}
#q::placeholder{color:var(--ink-3)}
#q:focus-visible,.pill:focus-visible,.row:focus-visible,.ghost:focus-visible{
  outline:2px solid var(--focus); outline-offset:2px
}
.pills{display:flex; flex-wrap:wrap; gap:6px}
.pill{
  font:inherit; font-size:14.2px; font-weight:500; cursor:pointer;
  padding:7px 11px; border-radius:3px; border:1px solid var(--line-2);
  background:var(--surface); color:var(--ink-2); white-space:nowrap;
}
.pill[aria-pressed="true"]{background:var(--ink); border-color:var(--ink); color:var(--paper)}
.pill .c{font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; opacity:.65; margin-left:5px}
label.pill{display:inline-flex; align-items:center; gap:5px; cursor:default}
label.pill select{
  font:inherit; font-size:13.6px; background:none; color:inherit;
  border:0; cursor:pointer; padding:0
}

/* ---------- tier sections ---------- */
.tier{margin-top:34px}
.tier-head{display:flex; align-items:baseline; gap:12px; padding-bottom:9px; border-bottom:2px solid var(--accent)}
.tier-head h2{
  font-family:Archivo,sans-serif; font-weight:600; font-size:22.4px; letter-spacing:-.015em;
  margin:0; color:var(--accent)
}
.tier-head .n{
  font-family:"IBM Plex Mono",monospace; font-size:14.2px; font-weight:500;
  font-variant-numeric:tabular-nums; color:var(--accent);
  background:var(--accent-wash); padding:2px 7px; border-radius:2px
}
.tier-head .blurb{font-size:14.2px; color:var(--ink-3); margin-left:auto; text-align:right}
.tier[data-t="0"]{--accent:var(--nj); --accent-wash:var(--nj-wash)}
.tier[data-t="1"]{--accent:var(--ny); --accent-wash:var(--ny-wash)}
.tier[data-t="2"]{--accent:var(--remote); --accent-wash:var(--remote-wash)}
.tier[data-t="3"]{--accent:var(--us); --accent-wash:var(--us-wash)}
.tier[data-t="4"],.tier[data-t="5"]{--accent:var(--intl); --accent-wash:var(--intl-wash)}
.latest{--accent:var(--ink);--accent-wash:var(--sunk)}
.latest .tier-head{border-bottom-color:var(--line-2)}
.latest .tier-head h2{color:var(--ink)}
.latest .tier-head .n{color:var(--ink);background:var(--sunk)}

/* ---------- job cards ---------- */
.row{
  display:grid; grid-template-columns:92px minmax(0,1fr) minmax(0,1.4fr) minmax(0,.85fr) 52px 300px;
  gap:16px; align-items:baseline; width:100%; text-align:left;
  padding:14px 12px 14px 16px; border:0; border-bottom:1px solid var(--line);
  border-left:3px solid transparent; background:none; color:inherit;
  font:inherit; cursor:pointer;
}
.row:hover{background:var(--sunk); border-left-color:var(--accent)}
.row[aria-expanded="true"]{background:var(--sunk); border-left-color:var(--accent)}
.latest .row{border-left-color:var(--accent)}
.row[data-t="0"]{--accent:var(--nj);--accent-wash:var(--nj-wash)}
.row[data-t="1"]{--accent:var(--ny);--accent-wash:var(--ny-wash)}
.row[data-t="2"]{--accent:var(--remote);--accent-wash:var(--remote-wash)}
.row[data-t="3"]{--accent:var(--us);--accent-wash:var(--us-wash)}
.row[data-t="4"],.row[data-t="5"]{--accent:var(--intl);--accent-wash:var(--intl-wash)}
.co{font-weight:600; color:var(--ink); overflow-wrap:anywhere}

/* Freshness is the signal you act on, so it gets the largest type on the row.
   Fit is a judgement about the job, not a reason to look now: it shrinks. */
.when{
  font-family:"IBM Plex Mono",monospace; font-size:17.7px; font-weight:600;
  color:var(--ink-2); font-variant-numeric:tabular-nums; line-height:1.25;
}
.when.hot{color:var(--nj)}
.when.applied{color:var(--ny)}
.recchip.stale{border-style:dashed; opacity:.85}
.tailor{
  font-family:"IBM Plex Mono",monospace; font-size:12.4px; padding:3px 7px;
  border:1px solid var(--line-2); border-radius:2px; background:none;
  color:var(--ink-3); cursor:pointer; white-space:nowrap; flex:none
}
.tailor:hover{border-color:var(--accent); color:var(--accent)}
.tailor.on{border-color:var(--remote); color:var(--remote); font-weight:600}
.tailor:disabled{opacity:.5; cursor:progress}
.ghost.tailor{font-size:14.2px; padding:6px 11px}
.zoomer{display:inline-flex; align-items:center; gap:7px}
.zoomer button{
  font:inherit; font-size:15px; line-height:1; width:20px; height:20px;
  border:1px solid var(--line-2); border-radius:2px; background:none;
  color:var(--ink-2); cursor:pointer; padding:0
}
.zoomer button:hover{border-color:var(--accent); color:var(--accent)}
.zoomer #zoomNow{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px;
  color:var(--ink-3); min-width:38px; text-align:center
}
.when small{
  display:block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--ink-3); font-weight:500
}
.fit{
  font-family:"IBM Plex Mono",monospace; font-size:14.8px; font-weight:600;
  color:var(--ink-3); text-align:right; font-variant-numeric:tabular-nums
}
.fit small{display:block; font-size:11px; letter-spacing:.08em; text-transform:uppercase; opacity:.8}

/* Freshness buckets replace the geographic sections as the feed's spine. */
.bucket{margin-top:30px}
.bucket-head{
  display:flex; align-items:baseline; gap:11px; padding-bottom:8px;
  border-bottom:2px solid var(--line-2); background:var(--paper)
}
.bucket-head h2{
  font-family:"IBM Plex Mono",monospace; font-size:13px; letter-spacing:.14em;
  text-transform:uppercase; margin:0; color:var(--ink-2); font-weight:600
}
.bucket-head .n{
  font-family:"IBM Plex Mono",monospace; font-size:13px; color:var(--ink-3);
  font-variant-numeric:tabular-nums
}
.bucket-head .blurb{font-size:13.6px; color:var(--ink-3); margin-left:auto}

/* Source pills: where this listing was found, each linking to that sighting. */
.srcpills{display:inline-flex; flex-wrap:wrap; gap:4px; margin-right:6px}
.srcpill{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px; letter-spacing:.03em;
  border:1px solid var(--line-2); border-radius:2px; padding:1px 5px;
  color:var(--ink-3); text-decoration:none; white-space:nowrap
}
.srcpill:hover{border-color:var(--accent); color:var(--accent)}
.srcpill.canon{border-color:var(--accent); color:var(--accent); font-weight:600}

/* Recruiter state, tiny on the row and expanded in the drawer. */
.recchip{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px; padding:1px 5px;
  border-radius:2px; white-space:nowrap; flex:none
}
.recchip.found{color:var(--us); border:1px solid var(--us)}
.recchip.messaged{color:var(--ny); border:1px solid var(--ny)}
.recchip.replied{color:var(--nj); border:1px solid var(--nj); font-weight:600}
.track{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr));
  gap:14px; margin:14px 0; padding:13px; background:var(--paper);
  border:1px solid var(--line); border-radius:3px
}
.track h4{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px; letter-spacing:.12em;
  text-transform:uppercase; color:var(--ink-3); margin:0 0 8px; font-weight:600
}
.track label{display:block; font-size:14.2px; color:var(--ink-2); margin-bottom:7px}
.track input[type=text],.track input[type=url],.track input[type=date],.track textarea{
  width:100%; margin-top:3px; padding:5px 7px; font:inherit; font-size:14.2px;
  background:var(--sunk); color:var(--ink); border:1px solid var(--line-2); border-radius:2px
}
.track textarea{min-height:52px; resize:vertical}
.radios{display:flex; flex-wrap:wrap; gap:9px; margin-bottom:9px}
.radios label{display:inline-flex; align-items:center; gap:4px; margin:0; font-size:14.2px; cursor:pointer}
.seen-dot{width:6px; height:6px; border-radius:50%; background:var(--ny); flex:none}
.row.is-seen .co{font-weight:500; color:var(--ink-2)}
.score{font-family:"IBM Plex Mono",monospace;font-size:23.6px;font-weight:700;color:var(--accent)}
.score small{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.ti{color:var(--ink-2); overflow-wrap:anywhere}
.lo{font-size:14.8px; color:var(--ink-3); overflow-wrap:anywhere}
.mi{
  font-family:"IBM Plex Mono",monospace; font-size:14.2px; font-variant-numeric:tabular-nums;
  color:var(--ink-3); text-align:right; white-space:nowrap
}
.marks{display:flex; gap:7px; justify-content:flex-end; align-items:center; min-width:0}
.fit{min-width:0; overflow:hidden}
.applied-check{
  display:inline-flex;align-items:center;gap:4px;cursor:pointer;white-space:nowrap;
  font-family:"IBM Plex Mono",monospace;font-size:11px;font-weight:600;color:var(--ink-3)
}
.applied-check input{margin:0;width:13px;height:13px;accent-color:var(--accent);cursor:pointer}
.applied-check:has(input:checked){color:var(--accent)}
.applied-check:has(input:disabled){cursor:default}
.applied-check input:disabled{cursor:default;opacity:1}
.quick-apply{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px; font-weight:600;
  color:var(--accent); text-decoration:none; padding:4px 6px;
  border:1px solid var(--accent); border-radius:3px; white-space:nowrap
}
.quick-apply:hover{background:var(--accent);color:var(--surface)}
.dot{width:6px; height:6px; border-radius:50%; background:var(--accent); flex:none}
.dot.is-new{box-shadow:0 0 0 3px var(--accent-wash)}
.doc{font-family:"IBM Plex Mono",monospace; font-size:11.8px; font-weight:600; color:var(--accent)}
.tag{
  font-family:"IBM Plex Mono",monospace; font-size:11.8px; font-weight:500;
  letter-spacing:.04em; text-transform:uppercase; color:var(--ink-3);
  border:1px solid var(--line-2); border-radius:2px; padding:1px 4px; margin-left:7px;
  white-space:nowrap; vertical-align:1px
}
.tag.warn{color:var(--flag); background:var(--flag-wash); border-color:transparent}
.tag.ready{color:var(--nj);background:var(--nj-wash);border-color:transparent}
.location-tag{color:var(--accent);background:var(--accent-wash);border-color:transparent;margin-left:0;margin-right:7px}
.why{margin:12px 0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:6px}
.why li{font-size:14.2px;background:var(--surface);border:1px solid var(--line);padding:4px 7px;border-radius:2px}
.status-select{font:inherit;font-size:14.2px;padding:7px 9px;color:var(--ink);background:var(--surface);border:1px solid var(--line-2);border-radius:3px}

/* ---------- expanded detail ---------- */
.detail{
  padding:6px 14px 22px 16px; border-bottom:1px solid var(--line);
  border-left:3px solid var(--accent); background:var(--sunk);
}
.detail .meta{
  font-family:"IBM Plex Mono",monospace; font-size:13.6px; color:var(--ink-3);
  display:flex; flex-wrap:wrap; gap:14px; margin-bottom:12px
}
.detail .body{
  max-width:74ch; font-size:15.9px; line-height:1.62; color:var(--ink-2);
  max-height:420px; overflow-y:auto; white-space:pre-wrap; overflow-wrap:anywhere;
  padding-right:8px
}
.detail .none{font-size:15.3px; color:var(--ink-3); font-style:italic}
.acts{display:flex; flex-wrap:wrap; gap:8px; margin-top:14px}
.btn,.ghost{
  font:inherit; font-size:14.8px; font-weight:600; padding:8px 13px; border-radius:3px;
  cursor:pointer; text-decoration:none; display:inline-block; border:1px solid transparent
}
.btn{background:var(--accent); color:var(--surface)}
.btn:hover{filter:brightness(1.08)}
.ghost{background:var(--surface); color:var(--ink-2); border-color:var(--line-2)}
.ghost:hover{border-color:var(--ink-3); color:var(--ink)}
code.cmd{
  font-family:"IBM Plex Mono",monospace; font-size:13.6px; background:var(--surface);
  border:1px solid var(--line); border-radius:2px; padding:2px 6px; color:var(--ink-2)
}

/* ---------- footer ---------- */
.empty{padding:40px 0; color:var(--ink-3); text-align:center}
footer{margin-top:52px; padding-top:22px; border-top:1px solid var(--line); color:var(--ink-3); font-size:14.8px}
footer h3{
  font-family:"IBM Plex Mono",monospace; font-size:13px; letter-spacing:.12em;
  text-transform:uppercase; color:var(--ink-3); margin:0 0 12px; font-weight:500
}
.srcs{display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:10px 22px; margin-bottom:22px}
.src{display:flex; gap:9px; align-items:flex-start; padding-bottom:10px; border-bottom:1px solid var(--line)}
.src .sd{width:7px; height:7px; border-radius:50%; margin-top:6px; flex:none; background:var(--nj)}
.src.bad .sd{background:var(--flag)}
.src.warn .sd{background:var(--us)}
.src .sn{font-weight:600; color:var(--ink); font-size:15.3px}
.src .sm{font-size:13.6px; color:var(--ink-3); margin-top:1px}
kbd{
  font-family:"IBM Plex Mono",monospace; font-size:13px; background:var(--sunk);
  border:1px solid var(--line-2); border-bottom-width:2px; border-radius:3px; padding:1px 5px; color:var(--ink-2)
}
@media (max-width:720px){
  .row{grid-template-columns:74px minmax(0,1fr) 58px 150px;gap:4px 12px}
  .when{grid-column:1;font-size:15.3px}.co,.ti,.lo{grid-column:2/3}
  .fit{grid-column:3/4;grid-row:1}.marks{grid-column:4/5;grid-row:1}
  .track{grid-template-columns:1fr}
  .tier-head{flex-wrap:wrap} .tier-head .blurb{margin-left:0; text-align:left; flex-basis:100%}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
</style>

<div class="wrap">
<header>
  <p class="eyebrow">Software · AI · Data internships</p>
  <h1>Application Command Center</h1>
  <p class="lede">Internships from direct company ATS feeds and aggregators, newest first.
  <b>Freshness sets the order; fit scores what it is worth.</b> Location is a filter, not a section.</p>
  <p class="notice">__AUTH__ Update <code>config/profile.yml</code> before automatic resume generation.</p>
</header>

<div class="strip">
  <div class="stat is-nj"><span class="n">__NJ__</span><span class="l">__PRIMARY_LABEL__</span></div>
  <div class="stat is-ny"><span class="n">__NY__</span><span class="l">__SECONDARY_LABEL__</span></div>
  <div class="stat"><span class="n">__TOTAL__</span><span class="l">Tracked</span></div>
  <div class="stat"><span class="n">__DESCRIBED__</span><span class="l">Descriptions</span></div>
</div>

<div class="controls">
  <input id="q" type="search" placeholder="Search company, role, or city" autocomplete="off">
  <div class="pills" id="tierPills"></div>
  <div class="pills">
    <button class="pill" id="fDesc" aria-pressed="false" type="button">Has description</button>
    <button class="pill" id="fUnseen" aria-pressed="false" type="button">Unseen <span class="c" id="unseenCount"></span></button>
    <button class="pill" id="fRec" aria-pressed="false" type="button">Has recruiter <span class="c" id="recCount"></span></button>
    <button class="pill" id="fDone" aria-pressed="false" type="button">Done <span class="c" id="doneCount"></span></button>
    <label class="pill" for="sort">Sort
      <select id="sort" aria-label="Sort order">
        <option value="new">Newest</option>
        <option value="fit">Best fit</option>
        <option value="near">Closest</option>
      </select>
    </label>
    <span class="pill zoomer">Zoom
      <button type="button" id="zoomOut" aria-label="Smaller">&minus;</button>
      <span id="zoomNow">100%</span>
      <button type="button" id="zoomIn" aria-label="Bigger">+</button>
    </span>
    <label class="pill" for="view">View
      <select id="view" aria-label="Which jobs to show">
        <option value="active">Active</option>
        <option value="applied">Applied</option>
        <option value="all">All</option>
      </select>
    </label>
  </div>
</div>

<main id="board"></main>

<footer>
  <h3>Sources</h3>
  <div class="srcs" id="srcs"></div>
  <p>Generated __GENERATED__ · Refresh with <code class="cmd">python scripts/jobboard.py refresh</code>
  then <code class="cmd">jobboard render</code>. Press <kbd>/</kbd> to search.</p>
</footer>
</div>

<script>
const ROWS = __ROWS__;
const TIERS = __TIERS__;
const HEALTH = __HEALTH__;
const state = {q:"", chip:"all", sort:"new", view:"active",
               desc:false, unseen:false, rec:false, done:false, open:null};
const STATUSES = ["NEW","EVALUATING","RESUME_GENERATING","READY_TO_APPLY","APPLIED","OA","INTERVIEW","OFFER","REJECTED","WITHDRAWN"];
const REC_STATES = [["none","None"],["found","Found recruiter"],["messaged","Messaged"],["replied","Replied"]];
const LATEST_HOURS = 6;
const DAY = 86400;

const esc = s => String(s==null?"":s).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const now = () => Date.now()/1000;

/* ---------- derived state ---------- */
const doneKeys = new Set();
function refreshDoneKeys(){
  doneKeys.clear();
  const donePdfs = new Set();
  ROWS.forEach(r => {
    if (r.st !== "READY_TO_APPLY" || !r.pdf || donePdfs.has(r.pdf)) return;
    donePdfs.add(r.pdf);
    doneKeys.add(r.k);
  });
  document.getElementById("doneCount").textContent = doneKeys.size;
}
const FOLLOWUP_DAYS = 5;
/* Something is waiting on you when an application or a message has gone quiet.
   A replied recruiter is never chased; a job nobody was contacted about still
   counts, because that is the outreach you have not done yet. */
function needsFollowUp(r){
  const rs = recState(r);
  if (rs === "replied") return false;
  if (rs === "messaged"){
    const at = (r.rec.find(c => c.status === "messaged") || {}).at;
    return at ? (now()-at) > FOLLOWUP_DAYS*DAY : false;
  }
  if (!isApplied(r)) return false;
  return r.appliedAt ? (now()-r.appliedAt) > FOLLOWUP_DAYS*DAY : false;
}

const recState = r => (r.rec && r.rec.length)
  ? r.rec.reduce((best,c) => REC_STATES.findIndex(x=>x[0]===c.status) >
      REC_STATES.findIndex(x=>x[0]===best) ? c.status : best, "none")
  : "none";
const isApplied = r => ["APPLIED","OA","INTERVIEW","OFFER","REJECTED"].includes(r.st);

function refreshCounts(){
  document.getElementById("unseenCount").textContent = ROWS.filter(r=>!r.seen && !isApplied(r)).length;
  document.getElementById("recCount").textContent = ROWS.filter(r=>recState(r)!=="none").length;
}

/* ---------- filter chips: location becomes a filter, never a section ---------- */
function chipCounts(){
  const c = {all:ROWS.length, new:0, saved:0, applied:0, followup:0, tailor:0};
  ROWS.forEach(r => {
    if (now()-r.discovered <= LATEST_HOURS*3600) c.new++;
    if (r.saved) c.saved++;
    if (isApplied(r)) c.applied++;
    if (needsFollowUp(r)) c.followup++;
    if (r.reqAt && !r.pdf) c.tailor++;
    c[r.tier] = (c[r.tier]||0)+1;
  });
  return c;
}
const pills = document.getElementById("tierPills");
function paintChips(){
  const c = chipCounts();
  const chip = (id,label,n) => '<button class="pill" data-chip="'+id+'" type="button" aria-pressed="'
    + (state.chip===id) + '">' + esc(label) + '<span class="c">'+n+'</span></button>';
  pills.innerHTML = chip("all","All",c.all) + chip("new","New",c.new)
    + Object.keys(TIERS).filter(t=>c[t]).map(t=>chip(t,TIERS[t][0],c[t])).join("")
    + chip("saved","Saved",c.saved) + chip("applied","Applied",c.applied)
    + chip("followup","Follow up",c.followup) + chip("tailor","Tailor queue",c.tailor);
}

function match(r){
  // A chip that explicitly asks for applied or follow-up work outranks the
  // View control. Letting "Active" veto them first made those chips dead.
  const chipWantsApplied = state.chip === "applied" || state.chip === "followup"
    || state.chip === "tailor";
  if (!chipWantsApplied){
    if (state.view === "active" && isApplied(r)) return false;
    if (state.view === "applied" && !isApplied(r)) return false;
  }
  if (state.chip === "new"){ if (now()-r.discovered > LATEST_HOURS*3600) return false; }
  else if (state.chip === "saved"){ if (!r.saved) return false; }
  else if (state.chip === "applied"){ if (!isApplied(r)) return false; }
  else if (state.chip === "followup"){ if (!needsFollowUp(r)) return false; }
  else if (state.chip === "tailor"){ if (!r.reqAt || r.pdf) return false; }
  else if (state.chip !== "all"){ if (r.tier !== Number(state.chip)) return false; }
  if (state.desc && !r.d) return false;
  if (state.unseen && r.seen) return false;
  if (state.rec && recState(r) === "none") return false;
  if (state.done && !(doneKeys.has(r.k) && r.st === "READY_TO_APPLY" && r.pdf)) return false;
  if (state.q){
    const hay = (r.c+" "+r.t+" "+r.p+" "+r.s).toLowerCase();
    if (!state.q.split(/\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}

/* ---------- ordering ---------- */
/* Default: newest posting first. A job with no posting date falls back to when we
   discovered it, and priority only breaks ties. Fit answers "how good is this",
   the feed answers "what just opened" - separate questions, separate controls. */
const stamp = r => isApplied(r) && r.appliedAt ? r.appliedAt : r.when;
const SORTS = {
  new:  (a,b) => stamp(b) - stamp(a) || b.score - a.score,
  fit:  (a,b) => b.score - a.score || b.when - a.when,
  near: (a,b) => (a.mi==null?1e9:a.mi) - (b.mi==null?1e9:b.mi) || b.when - a.when,
};

function age(stamp){
  const seconds = Math.max(0, now()-stamp);
  if (seconds < 3600) return Math.max(1,Math.round(seconds/60))+"m ago";
  if (seconds < DAY) return Math.round(seconds/3600)+"h ago";
  return Math.round(seconds/DAY)+"d ago";
}

/* Freshness buckets replace the geographic grouping. */
function bucketOf(r){
  const midnight = new Date(); midnight.setHours(0,0,0,0);
  const start = midnight.getTime()/1000;
  if (isApplied(r)) return "APPLIED";
  if (r.when >= start) return "TODAY";
  if (r.when >= start-DAY) return "YESTERDAY";
  if (r.when >= start-7*DAY) return "THIS WEEK";
  if (r.when >= start-30*DAY) return "THIS MONTH";
  return "EARLIER";
}
const BUCKET_ORDER = ["APPLIED","TODAY","YESTERDAY","THIS WEEK","THIS MONTH","EARLIER"];
const BUCKET_BLURB = {
  "APPLIED":"Submitted. Chase the recruiter here and update the thread when they reply.",
  "TODAY":"Posted since midnight.",
  "YESTERDAY":"Posted yesterday.",
  "THIS WEEK":"Posted in the last seven days.",
  "THIS MONTH":"Posted in the last thirty days.",
  "EARLIER":"Older than thirty days. Many will already be closed.",
};

function sourcePills(r){
  const canon = r.canon;
  const parts = (r.srcs||[]).map(x =>
    '<a class="srcpill" href="'+esc(x.u||r.u)+'" target="_blank" rel="noopener" '
    + 'title="Discovered via '+esc(x.s)+'">'+esc(x.s)+'</a>');
  if (canon) parts.unshift('<a class="srcpill canon" href="'+esc(canon)+'" target="_blank" '
    + 'rel="noopener" title="Canonical posting">Company site</a>');
  return parts.length ? '<span class="srcpills">'+parts.join("")+'</span>' : "";
}

/* ---------- drawer ---------- */
function recruiterForm(r){
  const c = (r.rec && r.rec[0]) || {id:"",name:"",profile:"",msg:"",status:"none",at:null,notes:""};
  const date = c.at ? new Date(c.at*1000).toISOString().slice(0,10) : "";
  const radios = REC_STATES.map(([v,label]) =>
    '<label><input type="radio" name="rec-'+esc(r.k)+'" value="'+v+'"'
    + (c.status===v?" checked":"")+'>'+esc(label)+'</label>').join("");
  const expanded = c.status !== "none";
  return '<form class="rec-form" data-reckey="'+esc(r.k)+'" data-recid="'+esc(c.id||"")+'">'
    + '<h4>Recruiter outreach</h4><div class="radios">'+radios+'</div>'
    + '<div class="rec-fields"'+(expanded?"":" hidden")+'>'
    +   '<label>Name<input type="text" name="name" value="'+esc(c.name)+'" placeholder="Jane Smith"></label>'
    +   '<label>Profile<input type="url" name="profile" value="'+esc(c.profile)+'" placeholder="https://linkedin.com/in/..."></label>'
    +   '<label>Message thread<input type="url" name="msg" value="'+esc(c.msg)+'" placeholder="https://linkedin.com/messaging/..."></label>'
    +   '<label>Date contacted<input type="date" name="at" value="'+esc(date)+'"></label>'
    +   '<label>Notes<textarea name="notes" placeholder="University recruiter for SWE interns...">'+esc(c.notes)+'</textarea></label>'
    +   '<button class="ghost" type="submit">Save contact</button>'
    + '</div></form>';
}

function detailHTML(r){
  const meta = [];
  if (r.tm) meta.push("Term: "+esc(r.tm));
  if (r.mi != null) meta.push(r.mi+" mi from campus");
  meta.push("Pipeline: "+esc(r.st.replaceAll("_"," ")));
  if (r.fit != null) meta.push("Codex fit: "+r.fit+"%");
  if (r.applicants != null) meta.push(r.applicants+"+ LinkedIn applicants");
  if (r.ats) meta.push("ATS: "+esc(r.ats));
  meta.push("Listing: "+esc(r.live.replaceAll("_"," ")));
  meta.push(r.postedKnown ? "Posted "+age(r.posted) : "Posting date unknown");
  meta.push("Discovered "+age(r.discovered));
  const body = r.d
    ? '<div class="body">'+esc(r.d)+(r.dt?"\n\n[truncated - open the posting for the full text]":"")+'</div>'
    : '<p class="none">No description cached yet. Run <code class="cmd">python scripts/jobboard.py describe</code> to fetch it.</p>';
  const why = r.reasons.length ? '<ul class="why">'+r.reasons.map(x=>'<li>+ '+esc(x)+'</li>').join("")+'</ul>' : '';
  const localHref = p => /^(https?:|file:|\/)/.test(p) ? p : "../"+p;
  const report = r.report ? '<a class="ghost" href="'+esc(localHref(r.report))+'">Fit report</a>' : '';
  const resume = r.pdf ? '<a class="ghost" href="'+esc(localHref(r.pdf))+'">Resume PDF</a>' : '';
  const options = STATUSES.map(s=>'<option value="'+s+'"'+(s===r.st?' selected':'')+'>'+s.replaceAll("_"," ")+'</option>').join("");
  const sightings = (r.srcs||[]).map(x =>
    '<li><a href="'+esc(x.u||r.u)+'" target="_blank" rel="noopener">'+esc(x.s)+' ↗</a>'
    + ' <span class="none">first seen '+age(x.first_seen||r.discovered)+'</span></li>').join("");
  return '<div class="detail">'
    + '<div class="meta">'+meta.map(m=>"<span>"+m+"</span>").join("")+'</div>'
    + '<strong>Why it ranked here</strong>'+why+body
    + '<div class="track">'
    +   '<div><h4>Application</h4>'
    +     '<label><input type="checkbox" data-applied="'+esc(r.k)+'"'+(isApplied(r)?" checked":"")+'> Applied</label>'
    +     '<label><input type="checkbox" data-saved="'+esc(r.k)+'"'+(r.saved?" checked":"")+'> Saved for later</label>'
    +     '<label>Pipeline <select class="status-select" data-status="'+esc(r.k)+'">'+options+'</select></label>'
    +   '</div>'
    +   '<div>'+recruiterForm(r)+'</div>'
    +   '<div><h4>Sources</h4><ul class="why">'+sightings+'</ul></div>'
    + '</div>'
    + '<div class="acts">'
    +   '<a class="btn" href="'+esc(r.canon||r.u)+'" target="_blank" rel="noopener">Open application ↗</a>'
    + report + resume
    +   '<button class="ghost tailor'+(r.reqAt?" on":"")+'" type="button" data-tailor="'+esc(r.k)+'">'
    +     (r.reqAt ? "Cancel resume request" : "Tailor my resume for this")+'</button>'
    +   '<button class="ghost" type="button" data-copy="python scripts/jobboard.py requests">Copy agent handoff</button>'
    + '</div></div>';
}

/* ---------- feed ---------- */
function render(){
  const board = document.getElementById("board");
  paintChips();
  refreshCounts();
  const shown = ROWS.filter(match).sort(SORTS[state.sort]);
  if (!shown.length){
    board.innerHTML = '<p class="empty">Nothing matches that filter.</p>';
    return;
  }
  const rowHTML = r => {
    const open = state.open === r.k;
    const hot = now()-r.when < 6*3600;
    const applied = isApplied(r);
    const leadStamp = applied && r.appliedAt ? r.appliedAt : r.when;
    const leadLabel = applied && r.appliedAt ? "applied" : (r.postedKnown ? "posted" : "found");
    const rs = recState(r);
    // Only a real outreach state earns a chip; "no recruiter" is the default
    // on 2,000 rows and was wide enough to collide with the fit column.
    const recAt = rs === "none" ? null
      : ((r.rec.find(c => c.status === rs) || {}).at || null);
    const recChip = rs === "none" ? ""
      : '<span class="recchip '+rs+(needsFollowUp(r)&&rs==="messaged"?" stale":"")+'" '
        + 'title="Recruiter outreach">'
        + esc({found:"Found",messaged:"Messaged",replied:"Replied"}[rs])
        + (recAt ? " " + esc(age(recAt)) : "")+'</span>';
    const warn = r.sp ? '<span class="tag warn">'+esc(r.sp)+'</span>' : '';
    const ready = r.st === "READY_TO_APPLY" ? '<span class="tag ready">Ready</span>' : '';
    return '<div class="row'+(r.seen?" is-seen":"")+'" role="button" tabindex="0" data-k="'+esc(r.k)+'" '
      + 'data-t="'+r.tier+'" aria-expanded="'+open+'">'
      + '<span class="when'+(hot&&!applied?" hot":"")+(applied?" applied":"")+'">'+esc(age(leadStamp))
      +   '<small>'+leadLabel+'</small></span>'
      + '<span class="co">'+esc(r.c)+'</span>'
      + '<span class="ti">'+esc(r.t)+warn+ready+'</span>'
      + '<span class="lo">'+esc(r.p)+(r.mi!=null?" · "+r.mi+" mi":"")+'<br>'+sourcePills(r)+recChip
      +   (r.applicants != null ? '<span class="none">'+r.applicants+'+ applicants</span>' : '')+'</span>'
      + '<span class="fit">'+r.score+'<small>fit</small></span>'
      + '<span class="marks">'+(r.d?'<span class="doc" title="Description cached">JD</span>':'')
      +   '<label class="applied-check"><input type="checkbox" data-applied="'+esc(r.k)+'" '
      +   'aria-label="Mark '+esc(r.c)+' applied"'+(isApplied(r)?" checked":"")+'>Applied</label>'
      +   '<button class="tailor'+(r.reqAt?" on":"")+'" type="button" data-tailor="'+esc(r.k)+'" '
      +     'title="'+(r.pdf ? "Tailored resume ready" : r.reqAt ? "Requested - click to cancel"
                       : "Ask Claude/Codex to tailor a resume for this role")+'">'
      +     (r.pdf ? "Resume ✓" : r.reqAt ? "Queued" : "Tailor")+'</button>'
      +   '<a class="quick-apply" href="'+esc(r.canon||r.u)+'" target="_blank" rel="noopener">Apply ↗</a></span>'
      + '</div>'
      + (open ? detailHTML(r) : "");
  };

  if (state.sort !== "new"){
    board.innerHTML = '<section class="bucket"><div class="bucket-head">'
      + '<h2>'+(state.sort==="fit"?"Best fit first":"Closest first")+'</h2>'
      + '<span class="n">'+shown.length+'</span>'
      + '<span class="blurb">Freshness ordering is off. Switch Sort back to Newest for the feed.</span>'
      + '</div>'+shown.map(rowHTML).join("")+'</section>';
    return;
  }
  const groups = {};
  shown.forEach(r => (groups[bucketOf(r)] = groups[bucketOf(r)] || []).push(r));
  const last24 = shown.filter(r => now()-r.when <= DAY).length;
  board.innerHTML = BUCKET_ORDER.filter(b => groups[b]).map(b => {
    // A bucket holds jobs placed by posting date and jobs placed by discovery
    // date. Saying "posted" for both would claim a date the source never gave,
    // so the header reports the split instead.
    const undated = groups[b].filter(r => !r.postedKnown).length;
    let blurb = BUCKET_BLURB[b];
    if (undated) blurb += " " + undated + " of these carry no source date and are "
      + "placed by when we found them.";
    if (b === "TODAY") blurb += " " + last24 + " posted in the last 24 hours, "
      + "which is what aggregators show as 0d.";
    return '<section class="bucket"><div class="bucket-head"><h2>'+b+'</h2>'
      + '<span class="n">'+groups[b].length+'</span>'
      + '<span class="blurb">'+esc(blurb)+'</span></div>'
      + groups[b].map(rowHTML).join("")+'</section>';
  }).join("");
}

/* ---------- writes ---------- */
/* `fallbackCommand` is an equivalent CLI command when one exists. Some writes
   (recruiter contacts) have no CLI equivalent, so they say so rather than
   copying a sentence to the clipboard as if it were a command. */
async function post(path, payload, fallbackCommand){
  let response;
  try{
    response = await fetch(path,{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
  }catch(_){
    notifyUnreachable(fallbackCommand);
    return null;
  }
  if (response.ok) return await response.json();
  if (response.status === 404){
    alert("This board is newer than the server running it.\n\nRestart it so the "
      + "new endpoints load:\n\nlaunchctl kickstart -k gui/$UID/com.$USER.codex-job-ops.board-server");
    return null;
  }
  let detail = "";
  try{ detail = (await response.text()).match(/Message: ([^<]*)/)?.[1] || ""; }catch(_){}
  alert("The board rejected that change"+(detail?":\n\n"+detail:".")
    +"\n\nNothing was saved.");
  return null;
}

function notifyUnreachable(fallbackCommand){
  if (fallbackCommand){
    navigator.clipboard?.writeText(fallbackCommand);
    alert("The board server is not running, so nothing was saved.\n\n"
      + "Command copied, run it in the repo:\n\n"+fallbackCommand);
  } else {
    alert("The board server is not running, so nothing was saved.\n\n"
      + "Start it with:  python scripts/jobboard.py serve");
  }
}

/* Opening a row is what marks it seen, so "Unseen" means genuinely unread. */
async function markSeen(r){
  if (r.seen) return;
  r.seen = 1;
  await post("/api/triage",{key:r.k,seen:true},null);
}

document.getElementById("board").addEventListener("click", async e => {
  const tailor = e.target.closest("[data-tailor]");
  if (tailor){
    e.stopPropagation();
    const key = tailor.dataset.tailor;
    const row = ROWS.find(x => x.k === key);
    if (row && row.pdf){
      alert("A tailored resume already exists for this role:\n\n"+row.pdf);
      return;
    }
    const wanted = !(row && row.reqAt);
    tailor.disabled = true;
    const ok = await post("/api/request-resume", {key, wanted},
      `python scripts/jobboard.py queue ${key}`);
    tailor.disabled = false;
    if (!ok) return;
    if (row){
      row.reqAt = wanted ? Math.floor(now()) : null;
      if (wanted && row.st === "NEW") row.st = "EVALUATING";
    }
    render();
    return;
  }
  if (e.target.closest("a,input,label,select,textarea,button[type=submit]")) return;
  const copy = e.target.closest("[data-copy]");
  if (copy){
    navigator.clipboard?.writeText(copy.dataset.copy);
    const was = copy.textContent;
    copy.textContent = "Copied";
    setTimeout(()=>{copy.textContent = was;}, 1400);
    return;
  }
  const row = e.target.closest(".row");
  if (!row) return;
  const key = row.dataset.k;
  state.open = state.open === key ? null : key;
  if (state.open){
    const r = ROWS.find(x => x.k === key);
    if (r) markSeen(r);
  }
  render();
});

document.getElementById("board").addEventListener("keydown", e => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest(".row");
  if (!row || e.target.closest("a,input,label,select,button,textarea")) return;
  e.preventDefault();
  const key = row.dataset.k;
  state.open = state.open === key ? null : key;
  if (state.open){
    const r = ROWS.find(x => x.k === key);
    if (r) markSeen(r);
  }
  render();
});

document.getElementById("board").addEventListener("change", async e => {
  const select = e.target.closest("[data-status]");
  const applied = e.target.closest("[data-applied]");
  const saved = e.target.closest("[data-saved]");
  const radio = e.target.closest('input[type=radio][name^="rec-"]');
  if (!select && !applied && !saved && !radio) return;
  e.stopPropagation();

  if (radio){
    const fields = radio.closest(".rec-form").querySelector(".rec-fields");
    fields.hidden = radio.value === "none";
    return;
  }
  if (saved){
    const key = saved.dataset.saved;
    const row = ROWS.find(r => r.k === key);
    const want = saved.checked;
    const ok = await post("/api/triage",{key,saved:want},
      `python scripts/jobboard.py save ${key} ${want?"on":"off"}`);
    if (!ok){ saved.checked = !want; return; }
    if (row) row.saved = want ? 1 : 0;
    render();
    return;
  }

  const key = applied ? applied.dataset.applied : select.dataset.status;
  const status = applied ? (applied.checked ? "APPLIED" : "NEW") : select.value;
  const command = `python scripts/jobboard.py set-status ${key} ${status}`;
  const ok = await post("/api/status",{key,status},command);
  if (!ok){ if (applied) applied.checked = !applied.checked; return; }
  const row = ROWS.find(r => r.k === key);
  if (row){
    ROWS.filter(other => other.k === key || (row.pdf && other.pdf === row.pdf))
      .forEach(other => { other.st = status; });
  }
  refreshDoneKeys();
  if (status === "APPLIED" && state.done) state.open = null;
  render();
});

document.getElementById("board").addEventListener("submit", async e => {
  const form = e.target.closest(".rec-form");
  if (!form) return;
  e.preventDefault();
  const data = new FormData(form);
  const checked = form.querySelector('input[type=radio]:checked');
  const date = data.get("at");
  const payload = {
    key: form.dataset.reckey,
    id: form.dataset.recid ? Number(form.dataset.recid) : null,
    name: data.get("name") || "",
    profile_url: data.get("profile") || "",
    message_url: data.get("msg") || "",
    status: checked ? checked.value : "none",
    contacted_at: date ? Math.floor(new Date(date+"T12:00:00").getTime()/1000) : null,
    notes: data.get("notes") || "",
  };
  const result = await post("/api/recruiter", payload, null);
  if (!result) return;
  const row = ROWS.find(r => r.k === payload.key);
  if (row){
    row.rec = [{id:result.id, name:payload.name, profile:payload.profile_url,
                msg:payload.message_url, status:payload.status,
                at:payload.contacted_at, notes:payload.notes}];
  }
  render();
});

/* ---------- controls ---------- */
pills.addEventListener("click", e => {
  const b = e.target.closest(".pill"); if (!b || !b.dataset.chip) return;
  state.chip = b.dataset.chip;
  render();
});

function toggle(id, key){
  const el = document.getElementById(id);
  el.addEventListener("click", () => {
    state[key] = !state[key];
    el.setAttribute("aria-pressed", state[key]);
    render();
  });
}
toggle("fDesc","desc");
toggle("fUnseen","unseen");
toggle("fRec","rec");
toggle("fDone","done");

document.getElementById("sort").addEventListener("change", e => {
  state.sort = e.target.value; render();
});
document.getElementById("view").addEventListener("change", e => {
  state.view = e.target.value; render();
});

/* ---------- zoom ----------
   Scales the whole board, remembered per browser. The page is readable at the
   default if this is unsupported or storage is blocked, so every access is
   guarded and failure is silent. */
const ZOOM_STEPS = [0.85, 0.925, 1, 1.1, 1.2, 1.35, 1.5];
let zoomIdx = 2;
try{
  const saved = ZOOM_STEPS.indexOf(Number(localStorage.getItem("boardZoom")));
  if (saved > -1) zoomIdx = saved;
}catch(_){}
function applyZoom(){
  const z = ZOOM_STEPS[zoomIdx];
  document.querySelector(".wrap").style.zoom = z;
  document.getElementById("zoomNow").textContent = Math.round(z*100)+"%";
  try{ localStorage.setItem("boardZoom", String(z)); }catch(_){}
}
document.getElementById("zoomIn").addEventListener("click", () => {
  zoomIdx = Math.min(zoomIdx+1, ZOOM_STEPS.length-1); applyZoom();
});
document.getElementById("zoomOut").addEventListener("click", () => {
  zoomIdx = Math.max(zoomIdx-1, 0); applyZoom();
});
applyZoom();

const q = document.getElementById("q");
q.addEventListener("input", () => { state.q = q.value.trim().toLowerCase(); render(); });
document.addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement !== q){ e.preventDefault(); q.focus(); }
  if (e.key === "Escape" && state.open){ state.open = null; render(); }
});

/* ---------- diagnostics strip ----------
   Each feed reports when it last ran AND how old the freshest listing it
   returned is. A source that refreshes happily while its newest job ages is
   how a broken ingestion looks from the outside. */
document.getElementById("srcs").innerHTML = HEALTH.map(h => {
  const cls = h.ok ? (h.status === "ok" ? "" : " warn") : " bad";
  const newest = h.newest_h == null ? "newest: unknown"
    : h.newest_h < 24 ? "newest job: "+Math.max(1,Math.round(h.newest_h))+"h"
    : "newest job: "+Math.round(h.newest_h/24)+"d";
  const flag = h.ok && h.status !== "ok"
    ? '<div class="sm" style="color:var(--us);font-weight:600">'
      + esc(h.status==="degraded" ? "stale feed · nothing posted in "+h.stale_days+"d"
        : h.status==="undated" ? "unverifiable · no posting dates"
        : h.status==="empty" ? "empty · no matching listings"
        : "unchecked")+'</div>'
    : '';
  return '<div class="src'+cls+'"><span class="sd"></span><div>'
    + '<div class="sn">'+esc(h.name)+'</div>'
    + '<div class="sm">'+(h.ok ? h.kept+" listings · "+esc(h.age) : "Blocked")+'</div>'
    + '<div class="sm">'+esc(newest)+'</div>'
    + flag
    + (h.note ? '<div class="sm">'+esc(h.note)+'</div>' : '')
    + '</div></div>';
}).join("");

refreshDoneKeys();
render();
</script>
"""
