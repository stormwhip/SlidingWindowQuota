"""Aggregate native timings, first within each replayed request."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, quantiles


def rows(path):
    with path.open(newline="", encoding="utf-8") as file:
        yield from csv.DictReader(file)


def summarize(out, repeats):
    by_request = defaultdict(list)
    for row in rows(out / "samples.csv"):
        key = tuple(row[k] for k in ("background_slots", "span", "target_rejection_pct",
                                    "seed", "request"))
        by_request[key].append(row)
    groups = defaultdict(list)
    for key, samples in by_request.items():
        assert len(samples) == repeats and {int(r["repeat"]) for r in samples} == set(range(repeats))
        groups[key[:3]].append(samples)
    with (out / "summary.csv").open("x", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("background_slots", "span", "target_rejection_pct", "method", "requests",
                         "mean_total_us", "p95_total_us", "p50_query_us", "p50_accept_update_us",
                         "median_build_ms", "peak_node_bytes"))
        builds = list(rows(out / "builds.csv"))
        for key, requests in sorted(groups.items()):
            matched = [r for r in builds if tuple(r[k] for k in (
                "background_slots", "span", "target_rejection_pct")) == key]
            for method in ("scan", "hull"):
                total = [median(int(r[f"{method}_query_ns"]) + int(r[f"{method}_update_ns"])
                                for r in samples) for samples in requests]
                query = [median(int(r[f"{method}_query_ns"]) for r in samples)
                         for samples in requests if samples[0]["capacity_query"] == "1"]
                update = [median(int(r[f"{method}_update_ns"]) for r in samples)
                          for samples in requests if samples[0]["accepted"] == "1"]
                writer.writerow((*key, method, len(requests), round(mean(total) / 1000, 4),
                                 round(quantiles(total, n=100, method="inclusive")[94] / 1000, 4),
                                 round(median(query) / 1000, 4), round(median(update) / 1000, 4),
                                 round(median(int(r[f"{method}_build_ns"]) for r in matched) / 1e6, 4),
                                 max(int(r[f"{method}_peak_node_bytes"]) for r in matched)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repeats", type=int, default=15)
    args = parser.parse_args()
    summarize(args.directory, args.repeats)
