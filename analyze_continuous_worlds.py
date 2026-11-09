"""Compare continuous legacy and exact worlds reconstructed from replay decisions."""

from collections import defaultdict
import csv
import json
import math
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "results" / "public-trace"
WINDOW = 86_400
MEMORY_PER_CORE = 1024


def rows(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def limits(capacity, percent):
    factor = struct.unpack("f", struct.pack("f", percent / 100.0))[0]
    return int(capacity * factor), int(MEMORY_PER_CORE * capacity * factor)


def peak(history, start, end):
    first, last = start, end + WINDOW
    relevant = [s for s in history if s[1] > first - WINDOW and s[0] < last]
    endpoints = {first, last}
    for a, b, _ in relevant:
        endpoints.update(t for t in (a, b, a + WINDOW, b + WINDOW) if first <= t <= last)
    return max(
        sum(cores * max(0, min(t, b) - max(t - WINDOW, a)) for a, b, cores in relevant)
        for t in endpoints)


def full_peak(history):
    start = min(s[0] for s in history)
    end = max(s[1] for s in history)
    return peak(history, start, end)


def main():
    requests = {(r["dataset"], int(r["row"])): r for r in rows("requests.csv")}
    world = {(r["dataset"], int(r["quota_percent"]), int(r["row"])): r for r in rows("worlds-1.csv")}
    groups = sorted({(d, q) for d, q, _ in world})
    summary = []
    for dataset, quota in groups:
        legacy = defaultdict(list)
        exact = defaultdict(list)
        stats = defaultdict(int)
        max_admit_excess = 0.0
        rows_sorted = [requests[(dataset, row)] for row in range(1450)]
        for request in rows_sorted:
            row = int(request["row"])
            decision = world[(dataset, quota, row)]
            start = int(request["start_seconds"])
            end = start + int(request["duration_seconds"])
            cores = int(request["cores"])
            capacity = int(request["capacity"])
            core_cap, mem_cap = limits(capacity, quota)
            quota_integral = core_cap * WINDOW
            memory_quota = mem_cap * WINDOW
            candidate = (start, end, cores)
            user = request["user"]

            p = peak(legacy[user] + [candidate], start, end)
            over = p > quota_integral or MEMORY_PER_CORE * p > memory_quota
            if decision["legacy"] == "ACCEPT":
                stats["legacy_accept"] += 1
                if over:
                    stats["legacy_violating_admits"] += 1
                    ratio = max(p / quota_integral,
                                MEMORY_PER_CORE * p / memory_quota) - 1
                    max_admit_excess = max(max_admit_excess, 100 * ratio)
                legacy[user].append(candidate)

            q = peak(exact[user] + [candidate], start, end)
            exact_over = q > quota_integral or MEMORY_PER_CORE * q > memory_quota
            if decision["exact"] == "ACCEPT":
                stats["exact_accept"] += 1
                assert not exact_over, (dataset, quota, row)
                exact[user].append(candidate)
            elif decision["exact"] == "INTEGRAL":
                assert exact_over, (dataset, quota, row)
            if decision["legacy"] != decision["exact"]:
                stats["divergent"] += 1

        def final_ratio(plan):
            best = 0.0
            for user_history in plan.values():
                if not user_history:
                    continue
                p = full_peak(user_history)
                core_cap, mem_cap = limits(int(rows_sorted[0]["capacity"]), quota)
                best = max(best, p / (core_cap * WINDOW),
                           MEMORY_PER_CORE * p / (mem_cap * WINDOW))
            return best

        summary.append({
            "dataset": dataset,
            "quota_percent": quota,
            "legacy_accept": stats.get("legacy_accept", 0),
            "legacy_violating_admits": stats.get("legacy_violating_admits", 0),
            "exact_accept": stats.get("exact_accept", 0),
            "divergent": stats.get("divergent", 0),
            "legacy_violating_rate": round(stats.get("legacy_violating_admits", 0) / max(stats.get("legacy_accept", 1), 1), 4),
            "legacy_max_admit_excess_pct": round(max_admit_excess, 1),
            "legacy_final_over_quota_pct": round(100 * (final_ratio(legacy) - 1), 1),
            "exact_final_over_quota_pct": round(100 * (final_ratio(exact) - 1), 1),
        })
    output = json.dumps(summary, indent=2) + "\n"
    (ROOT / "worlds-summary.json").write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
