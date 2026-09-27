"""routes_from_report.py — generate a measured routes.yaml from evalroute CSV.

The stable downstream interface is `evalroute.py report --csv` (see
evalroute/README.md "Integration notes"). This generator turns that CSV into
a routes.yaml whose rows carry `provenance: measured <n> tasks, <k> samples,
<date>` instead of the priors snapshot, so nothing measured masquerades as a
hypothesis or vice versa.

Mapping rules (documented in the plugin README):
- CSV rows are per (lane, model arm). Within each lane, the report's own
  routing rule already picked `route -> <model>`; we take that model and its
  effort column, with coverage+all-in stats as provenance.
- Keywords/match_hint/escalation are NOT in the CSV — they're classifier
  concerns, not measurement concerns. The generator PRESERVES them from the
  existing routes.yaml when the lane id matches, so classification quality
  survives regeneration.
- A lane with no measured data keeps its existing row untouched (and its
  provenance) — never silently delete a route the table already shipped.

Usage (inside the plugin repo):
  python routes_from_report.py --csv report.csv [--out data/routes.generated.yaml] \
      [--routes data/routes.yaml]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

# Columns hermes evalroute report --csv writes (see evalroute.py report()):
# lane, model, effort, n, pend, trunc, err, pass_, lo, hi, cov, passk,
# try_usd, succ_usd, vmin, allin, known, med_out, p50s

FIELDNAMES = ["lane", "model", "effort", "n", "pend", "trunc", "err", "pass_",
              "lo", "hi", "cov", "passk", "try_usd", "succ_usd", "vmin",
              "allin", "known", "med_out", "p50s"]

# CSV lane names (evalroute task "lane" strings) -> routes.yaml lane ids.
# Keep this mapping the single spelling authority between harness and plugin.
LANE_ALIASES = {
    "routine coding": "routine-coding",
    "coding": "routine-coding",
    "hard agentic coding": "hard-agentic-coding",
    "dl/ml research engineering": "dl-ml-research-engineering",
    "dl / ml research engineering": "dl-ml-research-engineering",
    "long-doc reading": "long-doc-reading",
    "long-doc reading, lit review": "long-doc-reading",
    "web research": "web-research",
    "web research, long docs": "web-research",
    "math": "math-first-principles",
    "math, first principles": "math-first-principles",
    "math-abstraction": "math-first-principles",
    "alignment": "alignment-reasoning",
    "alignment reasoning, paper claims": "alignment-reasoning",
    "prose": "prose",
    "orchestration": "orchestration",
    "orchestrator / subagents": "orchestration",
}

KEEP_FROM_EXISTING = ("keywords", "match_hint", "escalation", "notes")


def _slug(lane: str) -> str:
    """CSV lane string -> routes.yaml lane id via aliases, else a slug."""
    low = (lane or "").strip().lower()
    if low in LANE_ALIASES:
        return LANE_ALIASES[low]
    return low.replace(" ", "-").replace("/", "-").replace(",", "").strip("-")


def _route_row(lane_id: str, stats: dict, date: str, model_id: str) -> dict:
    """One routes.yaml lane row from the winning CSV row for a lane."""
    return {
        "id": lane_id,
        "label": stats.get("label") or lane_id.replace("-", " ").title(),
        "model": model_id,
        "effort": stats["effort"],
        "provenance": (f"measured {stats['n_tasks']} tasks, cov {stats['cov']}, "
                       f"all-in ${float(stats['allin']):.4f}/succ, {date}"),
    }


def load_model_ids(models_path: Path | None) -> dict[str, str]:
    """Map harness arm names (name@effort or plain name) -> routable model ids.

    The models.json used by the run is the authority; without it, arm names
    pass through unchanged (flagged in provenance as unverified ids)."""
    if not models_path or not models_path.exists():
        return {}
    out: dict[str, str] = {}
    for m in json.load(open(models_path, encoding="utf-8")):
        out[m["name"]] = m.get("model", m["name"])
    return out


def load_report(csv_path: Path) -> dict[str, dict]:
    """Parse report CSV; per lane keep the row with the lowest all-in $/succ
    among models within `--tol` of the best coverage — the report's own
    routing rule, re-implemented here so the generated table matches what
    `report` prints as `route ->`."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    lanes: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            r["cov_f"] = float(r["cov"]) if r["cov"] not in ("", "nan") else -1.0
            r["allin_f"] = float(r["allin"]) if r["allin"] not in ("", "inf") else float("inf")
            r["n_i"] = int(r["n"] or 0)
        except (ValueError, KeyError):
            continue
        lanes[r["lane"]].append(r)

    winners: dict[str, dict] = {}
    for lane, lrows in lanes.items():
        best_cov = max((r["cov_f"] for r in lrows if r["cov_f"] >= 0), default=-1.0)
        elig = [r for r in lrows if r["cov_f"] >= best_cov - 1e-9 and r["allin_f"] < float("inf")]
        pick = min(elig, key=lambda r: r["allin_f"]) if elig else None
        if pick is not None:
            winners[lane] = pick
    return winners


def generate(csv_path: Path, out_path: Path, existing_path: Path | None,
             models_path: Path | None = None, k: int = 1) -> int:
    existing: dict[str, dict] = {}
    if existing_path and existing_path.exists():
        raw = yaml.safe_load(existing_path.read_text(encoding="utf-8")) or {}
        existing = {l["id"]: l for l in (raw.get("lanes") or []) if isinstance(l, dict) and l.get("id")}

    model_ids = load_model_ids(models_path)
    winners = load_report(csv_path)
    if not winners:
        print("no routeable rows in CSV (all lanes failed or no data); nothing generated")
        return 1

    date = datetime.date.today().isoformat()
    lanes_out: list[dict] = []
    seen_ids: set[str] = set()

    # Measured lanes first, in stable order.
    for lane_name in sorted(winners):
        stats = winners[lane_name]
        stats["k"] = k
        stats["n_tasks"] = max(1, round(stats["n_i"] / k)) if k > 1 else stats["n_i"]
        lane_id = _slug(lane_name)
        if lane_id in seen_ids:
            continue
        seen_ids.add(lane_id)
        model_id = model_ids.get(stats["model"], stats["model"])
        row = _route_row(lane_id, stats, date, model_id)
        prev = existing.get(lane_id) or {}
        for key in KEEP_FROM_EXISTING:
            if prev.get(key):
                row[key] = prev[key]
        # A measured row must not carry stale priors-era caveats that
        # contradict it ("unmeasured", "verify with your own harness").
        if str(row.get("notes", "")).lower().lstrip().startswith(("unmeasured", "contested")):
            row.pop("notes", None)
        if prev.get("label"):
            row["label"] = prev["label"]
        lanes_out.append(row)

    # Unmeasured lanes keep their existing rows verbatim (with priors
    # provenance) so regeneration never deletes a route silently.
    for lane_id, prev in existing.items():
        if lane_id not in seen_ids:
            lanes_out.append(dict(prev))
            seen_ids.add(lane_id)

    doc = {
        "version": 1,
        "lanes": lanes_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    measured = sum(1 for l in lanes_out if str(l.get("provenance", "")).startswith("measured"))
    print(f"wrote {out_path}: {measured} measured lanes, {len(lanes_out) - measured} carried over")
    return 0


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Generate measured routes.yaml from evalroute report CSV")
    ap.add_argument("--csv", required=True, help="path to `evalroute.py report --csv` output")
    ap.add_argument("--out", default=str(here / "data" / "routes.generated.yaml"))
    ap.add_argument("--routes", default=str(here / "data" / "routes.yaml"),
                    help="existing routes.yaml whose classifier fields/unmeasured rows are preserved")
    ap.add_argument("--models", default=None,
                    help="models.json used by the run; maps arm names -> routable model ids")
    ap.add_argument("--k", type=int, default=1,
                    help="samples per task in the run; the CSV's n column counts samples, not tasks")
    a = ap.parse_args(argv)
    return generate(Path(a.csv), Path(a.out), Path(a.routes) if a.routes else None,
                     Path(a.models) if a.models else None, a.k)


if __name__ == "__main__":
    sys.exit(main())
