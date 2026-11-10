"""Replay the trace-derived reservation stream through exact and sampled policies.

Protocol (mirrors PublicTraceQuotaReplayTest.continuousWorldsDiverge): one plan
per dataset and quota, all 1,450 rows in file order, no archiving, uniform
1024 MiB/core, 100% instantaneous limit.

The attribution mode keeps the *exact* plan as ground truth and asks what each
sampled variant would decide against that history.  This separates sampling
error from the Math.round normalisation without feedback between policies.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from Experiments import ReferenceQuota as yq
from Experiments.ArtifactPaths import RESULTS

TRACE = RESULTS / "PublicTrace" / "Requests.csv"
QUOTAS = (2, 5, 10, 25)


def load_trace() -> dict[str, list[tuple[int, yq.Allocation]]]:
    out: dict[str, list[tuple[int, yq.Allocation]]] = defaultdict(list)
    with TRACE.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            start = yq.BASE_MS + 1000 * int(row["start_seconds"])
            duration = 1000 * int(row["duration_seconds"])
            out[row["dataset"]].append(
                (int(row["user"]), yq.Allocation(start, start + duration, int(row["cores"])))
            )
    return out


def replay(trace, capacity, quota, variant):
    limit_c, limit_m = yq.quota_limits(capacity, quota)
    cap_m = yq.MEM_PER_CORE * capacity
    plan: dict[int, list[yq.Allocation]] = defaultdict(list)
    everything: list[yq.Allocation] = []
    admits = 0
    instant = 0
    for user, cand in trace:
        if not yq.instant_ok(everything, cand, capacity, cap_m):
            instant += 1
            continue
        hist = plan[user]
        if variant == "exact":
            ok = yq.exact_accept(hist, cand, None, limit_cores=limit_c, limit_mem=limit_m)
        else:
            opts = {} if variant == "sampled" else variant
            ok = yq.sampled_accept(hist, cand, None, limit_cores=limit_c,
                                   limit_mem=limit_m, dimensions="both", **opts)
        if ok:
            hist.append(cand)
            everything.append(cand)
            admits += 1
    return {"admits": admits, "instant": instant}


def attribution(trace, capacity, quota):
    limit_c, limit_m = yq.quota_limits(capacity, quota)
    cap_m = yq.MEM_PER_CORE * capacity
    plan: dict[int, list[yq.Allocation]] = defaultdict(list)
    everything: list[yq.Allocation] = []
    variants = {
        "sampled": dict(),
        "sampled_float": dict(rounding=False),
        "half": dict(density=2),
        "quarter": dict(density=4),
    }
    counts = {name: {"fa": 0, "fr": 0} for name in variants}
    exact_accepts = 0
    instant = 0
    flips = 0
    for user, cand in trace:
        if not yq.instant_ok(everything, cand, capacity, cap_m):
            instant += 1
            continue
        hist = plan[user]
        exact_ok = yq.exact_accept(hist, cand, None, limit_cores=limit_c, limit_mem=limit_m)
        decisions = {}
        for name, opts in variants.items():
            decisions[name] = yq.sampled_accept(
                hist, cand, None, limit_cores=limit_c, limit_mem=limit_m,
                dimensions="both", **opts
            )
        if decisions["sampled"] != decisions["sampled_float"]:
            flips += 1
        for name, ok in decisions.items():
            if ok and not exact_ok:
                counts[name]["fa"] += 1
            elif exact_ok and not ok:
                counts[name]["fr"] += 1
        if exact_ok:
            exact_accepts += 1
            hist.append(cand)
            everything.append(cand)
    return {
        "exact_accepts": exact_accepts,
        "instant": instant,
        "rounding_flips": flips,
        **{f"{name}_{k}": v for name, d in counts.items() for k, v in d.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("continuous", "attribution", "both"), default="both")
    ap.add_argument("--out", type=Path, default=RESULTS / "Reference")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    trace = load_trace()
    rows = []
    for dataset, jobs in trace.items():
        capacity = 100 if dataset == "kth" else 256
        for quota in QUOTAS:
            row = {"dataset": dataset, "quota": quota, "capacity": capacity}
            if args.mode in ("continuous", "both"):
                for name, variant in (("exact", "exact"), ("sampled", "sampled")):
                    r = replay(jobs, capacity, quota, variant)
                    row[f"{name}_admits"] = r["admits"]
                    row[f"{name}_instant"] = r["instant"]
            if args.mode in ("attribution", "both"):
                row.update(attribution(jobs, capacity, quota))
            rows.append(row)
            print(json.dumps(row), flush=True)
    (args.out / "Replay.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
