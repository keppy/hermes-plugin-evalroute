"""Aggregate flywheel labels into an observed-provenance route table.

The flywheel's labels.jsonl holds single-arm observational data. This module
turns it into per-lane statistics and an `observed` route table — deliberately
WEAKER than the harness's `measured` rows:

  observed N tasks, single-arm, pass R% (date)
  measured  N tasks, cov C, all-in $X/succ (date)

`merge_observed` applies observed rows to a routes.yaml ONLY where the lane
has no measured row: observational data can contest a priors row, never
overwrite a measured one. Escalation rates decide the recommended arm: if the
recommended arm failed and the escalation passed, the route flips to the
escalation with a note.

Usage:
  python routes_from_labels.py                    # print per-lane stats
  python routes_from_labels.py --apply            # write data/routes.observed.yaml
"""

from __future__ import annotations

import argparse
import datetime
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

try:
    from . import flywheel
except ImportError:  # pragma: no cover - standalone/plugin-root execution
    import flywheel  # type: ignore

MAX_STALE_DAYS = 90  # older than this, the model landscape has moved; ignore


def aggregate(labels: list[dict[str, Any]], max_age_days: int = MAX_STALE_DAYS) -> dict:
    """Per-lane arm statistics from labels. Pure function over the record list."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=max_age_days)).timestamp()
    routes = [r for r in labels if r.get("kind") == "route" and r.get("ts", 0) >= cutoff]
    outcomes = [o for o in labels if o.get("kind") == "outcome" and o.get("rated") in ("pass", "fail")
                and o.get("ts", 0) >= cutoff]
    corrections = [c for c in labels if c.get("kind") == "lane_correction"]

    # index routes by recency for correlation: outcome -> most recent route at/before it
    routes_sorted = sorted(routes, key=lambda r: r.get("ts", 0))
    lanes: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: dict(attempts=0, passes=0,
                                                   fails=0, escalations=0)))
    for out in outcomes:
        lane = out.get("route_lane") or "(unknown)"
        arm_key = f'{out.get("actual_model") or "?"}@{out.get("actual_effort") or "?"}'
        arm = lanes[lane][arm_key]
        arm["attempts"] += 1
        if out.get("rated") == "pass":
            arm["passes"] += 1
        else:
            arm["fails"] += 1

    lane_stats: dict[str, Any] = {
        lane: {
            "arms": {k: dict(v) for k, v in sorted(arms.items())},
            "outcomes": sum(a["attempts"] for a in arms.values()),
            "pass_rate": (sum(a["passes"] for a in arms.values())
                          / max(1, sum(a["attempts"] for a in arms.values()))),
        }
        for lane, arms in lanes.items()
    }

    # Facet aggregation: per-facet pass rates AND co-occurrence tuples. The
    # conjunction nodes (long-doc + domain-dlml etc.) validate the dominance
    # rule for free as labels accumulate; legacy records without facets are
    # simply not counted here.
    facet_single: dict[str, dict[str, int]] = defaultdict(lambda: dict(attempts=0, passes=0))
    facet_pairs: dict[str, dict[str, int]] = defaultdict(lambda: dict(attempts=0, passes=0))
    for out in outcomes:
        fs = sorted(out.get("facets") or [])
        passed = out.get("rated") == "pass"
        for fid in fs:
            facet_single[fid]["attempts"] += 1
            facet_single[fid]["passes"] += int(passed)
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                key = f"{fs[i]} + {fs[j]}"
                facet_pairs[key]["attempts"] += 1
                facet_pairs[key]["passes"] += int(passed)
    facet_stats = {fid: dict(v) for fid, v in sorted(facet_single.items())}
    pair_stats = {k: dict(v) for k, v in sorted(facet_pairs.items())}

    return {
        "routes": len(routes),
        "outcomes": len(outcomes),
        "lane_corrections": len(corrections),
        "corrections": [{"from": c.get("from_lane"), "to": c.get("to_lane")}
                        for c in corrections],
        "lanes": lane_stats,
        "facets": facet_stats,
        "facet_pairs": pair_stats,
        "stale_cutoff_days": max_age_days,
    }


def _observed_row(lane_id: str, stats: dict, prev: dict[str, Any], date: str) -> dict[str, Any]:
    """A routes.yaml lane row stamped observed."""
    row = dict(prev) if prev else {"id": lane_id, "label": lane_id.replace("-", " ").title()}
    row["id"] = lane_id
    n = stats["outcomes"]
    pr = stats["pass_rate"]
    row["provenance"] = f"observed {n} tasks, single-arm, pass {pr:.0%}, {date}"
    # gonogo decide(): the honest verdict on what n can support, when available.
    try:
        from . import adjudicate
    except ImportError:
        import adjudicate  # type: ignore
    passes = sum(a["passes"] for a in stats["arms"].values())
    verdict = adjudicate.observed_verdict(passes, n)
    if verdict:
        row["provenance"] = f'{row["provenance"]}; {verdict}'
    # Escalation flip: recommended arm failing while an escalation-tier model
    # passed is the one observational signal strong enough to move the route.
    prev_model = prev.get("model") if prev else None
    if prev_model:
        rec_arm = stats["arms"].get(f'{prev_model}@{prev.get("effort", "medium")}')
        if rec_arm and rec_arm["attempts"] >= 2 and rec_arm["passes"] == 0:
            passing = [(k, a) for k, a in stats["arms"].items()
                       if a["passes"] > 0 and not k.startswith(f"{prev_model}@")]
            if passing:
                best_k, best_a = max(passing, key=lambda ka: ka[1]["passes"])
                row["model"], row["effort"] = best_k.split("@", 1)
                row["notes"] = (f"observed flip: {prev_model} failed {rec_arm['attempts']}x "
                                f"while {best_k} passed; single-arm evidence, verify")
    return row


def merge_observed(routes_path: Path, labels: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """Apply observed rows to a routes table; measured rows are never overwritten."""
    raw = yaml.safe_load(routes_path.read_text(encoding="utf-8")) or {}
    existing = {l["id"]: l for l in (raw.get("lanes") or []) if isinstance(l, dict) and l.get("id")}
    stats = aggregate(labels)
    date = datetime.date.today().isoformat()
    out: list[dict] = []
    applied = 0
    seen: set[str] = set()
    for lane_id, lstats in sorted(stats["lanes"].items()):
        if not lane_id.startswith("("):  # (unknown) has no table row
            prev = existing.get(lane_id, {})
            if str(prev.get("provenance", "")).startswith("measured"):
                out.append(dict(prev))  # measured wins; observed does not overwrite
            else:
                out.append(_observed_row(lane_id, lstats, prev, date))
                applied += 1
            seen.add(lane_id)
    for lane_id, prev in existing.items():
        if lane_id not in seen:
            out.append(dict(prev))
    return out, applied


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Aggregate flywheel labels into observed route stats")
    ap.add_argument("--apply", action="store_true",
                    help="write data/routes.observed.yaml (observed rows only where not measured)")
    ap.add_argument("--routes", default=str(here / "data" / "routes.yaml"))
    a = ap.parse_args(argv)
    labels = flywheel.read_labels()
    if not labels:
        print("no labels yet - run /route and /rate in your daily sessions; "
              "labels live in <hermes home>/evalroute/labels.jsonl")
        return 1
    stats = aggregate(labels)
    print(f"{stats['routes']} routes, {stats['outcomes']} rated outcomes, "
          f"{stats['lane_corrections']} lane corrections "
          f"(stale cutoff {stats['stale_cutoff_days']}d)")
    for c in stats["corrections"]:
        print(f"  correction: {c['from']} -> {c['to']}")
    for lane, ls in sorted(stats["lanes"].items()):
        print(f"\nlane: {lane}   ({ls['outcomes']} outcomes, pass {ls['pass_rate']:.0%})")
        for arm, astat in ls["arms"].items():
            print(f"  {arm:<40} {astat['attempts']} tries, {astat['passes']} pass, {astat['fails']} fail")
    if stats.get("facets"):
        print("\nfacets (per-dimension outcomes):")
        for fid, fs in stats["facets"].items():
            pr = fs["passes"] / max(1, fs["attempts"])
            print(f"  {fid:<28} {fs['attempts']} outcomes, pass {pr:.0%}")
    if stats.get("facet_pairs"):
        print("\nfacet conjunctions (the graph nodes):")
        for key, fs in stats["facet_pairs"].items():
            pr = fs["passes"] / max(1, fs["attempts"])
            print(f"  {key:<44} {fs['attempts']} outcomes, pass {pr:.0%}")
    if a.apply:
        out_path = here / "data" / "routes.observed.yaml"
        lanes, applied = merge_observed(Path(a.routes), labels)
        out_path.write_text(yaml.safe_dump({"version": 1, "lanes": lanes},
                                           sort_keys=False, allow_unicode=True),
                            encoding="utf-8")
        print(f"\nwrote {out_path}: {applied} observed rows applied "
              "(measured rows untouched; priors rows contested only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
