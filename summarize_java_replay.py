"""Report request-level medians across repetitions and fresh JVMs."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, quantiles


GROUP = ("background_slots", "span", "target_rejection_pct")
KEY = GROUP + ("seed", "request")


def load(path):
    requests = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            requests[tuple(row[k] for k in KEY)].append(row)
    assert len(requests) == 2560, path
    for samples in requests.values():
        assert sorted(int(r["repeat"]) for r in samples) == list(range(15)), path
    return requests


def write(path, forks):
    groups = defaultdict(list)
    for key in forks[0]:
        samples = [row for fork in forks for row in fork[key]]
        assert len({(r["accepted"], r["capacity_query"]) for r in samples}) == 1
        groups[key[:3]].append(samples)
    with path.open("w", encoding="utf-8", newline="") as file:
        out = csv.writer(file)
        out.writerow((*GROUP, "method", "requests", "rejected", "mean_total_us", "p95_total_us",
                      "p50_query_us", "p50_accept_update_us", "raw_p95_total_us"))
        for key, requests in sorted(groups.items(), key=lambda g: (int(g[0][0]), *g[0][1:])):
            for method in ("scan", "hull"):
                q, u = f"{method}_query_ns", f"{method}_update_ns"
                totals = [[int(r[q]) + int(r[u]) for r in samples] for samples in requests]
                totals_by_request = [median(t) for t in totals]
                query = [median(int(r[q]) for r in s) for s in requests
                         if s[0]["capacity_query"] == "true"]
                update = [median(int(r[u]) for r in s) for s in requests
                          if s[0]["accepted"] == "true"]
                rejected = sum(s[0]["accepted"] == "false" for s in requests)
                out.writerow((*key, method, len(requests), rejected, round(mean(totals_by_request) / 1000, 3),
                              round(p95(totals_by_request) / 1000, 3), round(median(query) / 1000, 3),
                              round(median(update) / 1000, 3),
                              round(p95([v for t in totals for v in t]) / 1000, 3)))


def p95(values):
    return quantiles(values, n=100, method="inclusive")[94]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory
    forks = [load(directory / f"samples-{i}.csv") for i in range(1, 4)]
    write(directory / "summary.csv", forks)
    for i, fork in enumerate(forks, 1):
        write(directory / f"summary-{i}.csv", [fork])
