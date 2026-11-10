"""Replay both policies with YARN's and rounded average quota limits."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from Experiments import ReferenceQuota as quota_model
from Experiments.AnalyzeContinuousWorlds import full_peak
from Experiments.ArtifactPaths import RESULTS
from Experiments.ReferenceReplay import load_trace


def replay(trace, capacity, core_limit, memory_limit, policy):
    plans = defaultdict(list)
    accepted = []
    for user, candidate in trace:
        if not quota_model.instant_ok(
            accepted, candidate, capacity, capacity * quota_model.MEM_PER_CORE
        ):
            continue
        if policy == "exact":
            fits = quota_model.exact_accept(
                plans[user], candidate, None,
                limit_cores=core_limit, limit_mem=memory_limit,
            )
        else:
            fits = quota_model.sampled_accept(
                plans[user], candidate, None,
                limit_cores=core_limit, limit_mem=memory_limit, dimensions="both",
            )
        if fits:
            plans[user].append(candidate)
            accepted.append(candidate)

    peak_cores = max(
        full_peak([(a.start // 1000, a.end // 1000, a.cores) for a in history])
        for history in plans.values() if history
    )
    window_seconds = quota_model.DAY_MS // 1000
    peak_ratio = max(
        peak_cores / (core_limit * window_seconds),
        quota_model.MEM_PER_CORE * peak_cores / (memory_limit * window_seconds),
    )
    return {
        "admissions": len(accepted),
        "peak_excess_percent": round(100 * (peak_ratio - 1), 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("Runs/QuotaConversionReplay.json"))
    args = parser.parse_args()

    traces = load_trace()
    with (RESULTS / "PublicTrace" / "WorldsSummary.json").open(encoding="utf-8") as source:
        archived = {(row["dataset"], row["quota_percent"]): row for row in json.load(source)}

    results = []
    for dataset, capacity in (("kth", 100), ("cerit", 256)):
        for percent in (2, 5, 10, 25):
            yarn_limits = quota_model.quota_limits(capacity, percent)
            rounded_limits = (
                (capacity * percent + 50) // 100,
                (capacity * quota_model.MEM_PER_CORE * percent + 50) // 100,
            )
            row = {
                "dataset": dataset,
                "quota_percent": percent,
                "capacity_cores": capacity,
                "yarn_limits": {"cores": yarn_limits[0], "memory_mib": yarn_limits[1]},
                "rounded_limits": {"cores": rounded_limits[0], "memory_mib": rounded_limits[1]},
            }
            recorded = archived[(dataset, percent)]
            for policy, prefix in (("sampled", "legacy"), ("exact", "exact")):
                original = replay(traces[dataset], capacity, *yarn_limits, policy)
                expected = {
                    "admissions": recorded[f"{prefix}_accept"],
                    "peak_excess_percent": recorded[f"{prefix}_final_over_quota_pct"],
                }
                if original != expected:
                    raise ValueError(f"{dataset} {percent}% {policy}: {original} != {expected}")
                rounded = replay(traces[dataset], capacity, *rounded_limits, policy)
                row[policy] = {"yarn_limits": original, "rounded_limits": rounded}
            results.append(row)
            print(json.dumps(row), flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

