"""Check trace verdicts by direct integration and summarize paired YARN runs."""

from collections import Counter, defaultdict
import csv
import glob
import json
import math
import re
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parent / "results" / "public-trace"
WINDOW = 86_400
MEMORY_PER_CORE = 1024
OUTCOMES = {
    ("ACCEPT", "ACCEPT"): "both_accept",
    ("INTEGRAL", "INTEGRAL"): "both_integral",
    ("INSTANT", "INSTANT"): "both_instant",
    ("ACCEPT", "INTEGRAL"): "false_accept",
    ("INTEGRAL", "ACCEPT"): "false_reject",
}
FIELDS = ("dataset", "row", "quota_percent", "measured", "history_changes", "legacy", "exact")


def read_rows(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def quota_limits(capacity, percent):
    """Replicate Resources.multiply(Resource, float) truncation in YARN."""
    import struct
    factor = struct.unpack("f", struct.pack("f", percent / 100.0))[0]
    return int(capacity * factor), int(MEMORY_PER_CORE * capacity * factor)


def percentile(values, q):
    values = sorted(values)
    return values[math.ceil(q * len(values)) - 1]


def fmt(values, q):
    return round(percentile(values, q), 3) if values else None


def peak(history, start, end):
    """Sum overlaps independently at each original or shifted endpoint."""
    first, last = start, end + WINDOW
    relevant = [s for s in history if s[1] > first - WINDOW and s[0] < last]
    endpoints = {first, last}
    for a, b, _ in relevant:
        endpoints.update(t for t in (a, b, a + WINDOW, b + WINDOW) if first <= t <= last)
    return max(
        (sum(cores * max(0, min(t, b) - max(t - WINDOW, a)) for a, b, cores in relevant), -t)
        for t in endpoints
    )


def load_runs():
    runs = {}
    for path in sorted(ROOT.glob("replay-*.csv")):
        fork = int(re.search(r"-(\d+)\.csv$", path.name).group(1))
        for row in read_rows(path.name):
            key = (row["dataset"], int(row["row"]), int(row["quota_percent"]), fork)
            assert key not in runs, (path.name, key)
            runs[key] = row
    return runs


def main():
    requests = {(r["dataset"], int(r["row"])): r for r in read_rows("requests.csv")}
    runs = load_runs()
    groups = sorted({(d, q) for d, _, q, _ in runs})
    summary = []
    history = defaultdict(lambda: defaultdict(list))
    for dataset, quota in groups:
        group_rows = sorted((k for k in runs if k[0] == dataset and k[2] == quota),
                            key=lambda k: (k[1], k[3]))
        counts = Counter()
        margins = defaultdict(lambda: defaultdict(list))
        utilization = []
        changes = []
        latencies = defaultdict(lambda: {"legacy": [], "exact": []})
        examples = {}
        seen = set()
        for row_index, quota_value in sorted({(k[1], k[2]) for k in group_rows}):
            if (row_index, quota_value) in seen:
                continue
            seen.add((row_index, quota_value))
            base = runs[(dataset, row_index, quota_value, 1)]
            replicates = [runs[(dataset, row_index, quota_value, f)] for f in (1, 2, 3)]
            assert all(all(r[f] == base[f] for f in FIELDS) for r in replicates), base
            request = requests[(dataset, row_index)]
            assert (base["measured"] == "true") == (request["measured"] == "1"), base
            users = history[(dataset, quota_value)]
            outcome = OUTCOMES[base["legacy"], base["exact"]]
            if base["measured"] == "true":
                counts[outcome] += 1
                changes.append(int(base["history_changes"]))
                for policy in ("legacy", "exact"):
                    latencies[outcome][policy].append(tuple(int(r[f"{policy}_ns"]) for r in replicates))
            if base["exact"] == "INSTANT":
                continue
            start = int(request["start_seconds"])
            end = start + int(request["duration_seconds"])
            cores = int(request["cores"])
            core_cap, memory_cap = quota_limits(int(request["capacity"]), quota_value)
            quota_integral = core_cap * WINDOW
            memory_quota_integral = memory_cap * WINDOW
            prior = users[request["user"]]
            candidate = (start, end, cores)
            max_integral, _ = peak(prior + [candidate], start, end)
            fits = max_integral <= quota_integral and MEMORY_PER_CORE * max_integral <= memory_quota_integral
            assert fits == (base["exact"] == "ACCEPT"), base
            if base["measured"] == "true":
                if base["exact"] == "ACCEPT":
                    utilization.append(max_integral / quota_integral)
                if outcome == "false_accept":
                    excess = max_integral - quota_integral
                    margins[outcome]["excess_core_s"].append(excess)
                    margins[outcome]["excess_pct"].append(100 * excess / quota_integral)
                    examples.setdefault(outcome, {"row": row_index, "excess_core_s": excess})
                elif outcome == "false_reject":
                    slack = quota_integral - max_integral
                    margins[outcome]["slack_core_s"].append(slack)
                    margins[outcome]["slack_pct"].append(100 * slack / quota_integral)
                    examples.setdefault(outcome, {"row": row_index, "slack_core_s": slack})
            if fits:
                prior.append(candidate)
        measured = sum(counts.values())
        assert measured == 1200, (dataset, quota, measured)
        row = {
            "dataset": dataset,
            "quota_percent": quota,
            **{name: counts[name] for name in OUTCOMES.values()},
            "exact_reject_rate": round((counts["both_integral"] + counts["false_accept"]) / measured, 4),
            "misjudge_rate": round((counts["false_accept"] + counts["false_reject"]) / measured, 4),
            "history_changes_p50": percentile(changes, .50),
            "history_changes_p95": percentile(changes, .95),
            "utilization_p50": fmt(utilization, .50),
            "utilization_p95": fmt(utilization, .95),
            "margins": {k: {name: {"p50": fmt(v, .50), "p95": fmt(v, .95), "max": round(max(v), 3)}
                            for name, v in d.items()} for k, d in margins.items()},
            "examples": examples,
            "latency": {},
        }
        for outcome, series in latencies.items():
            row["latency"][outcome] = {}
            for policy, samples in series.items():
                per_request = [median(s) for s in samples]
                row["latency"][outcome][policy] = {
                    "p50_us": round(median(per_request) / 1000, 1),
                    "p95_us": round(percentile(per_request, .95) / 1000, 1),
                    "fork_p95_us": [round(percentile([s[f] for s in samples], .95) / 1000, 1)
                                    for f in range(3)],
                }
        summary.append(row)
    output = json.dumps(summary, indent=2) + "\n"
    (ROOT / "summary.json").write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
