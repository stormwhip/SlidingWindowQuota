"""Compare legacy and exact validation latencies on matched decisions."""

import argparse
from collections import defaultdict
import csv
from pathlib import Path
from statistics import median, quantiles


def p95(values):
    return quantiles(values, n=100, method="inclusive")[94]


def summarize(directory):
    groups = defaultdict(list)
    forks = defaultdict(list)
    for fork in range(1, 4):
        with (directory / f"timings-{fork}.csv").open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                key = row["scenario"], row["accepted"]
                pair = int(row["original_ns"]), int(row["exact_ns"])
                groups[key].append(pair)
                forks[key, fork].append((int(row["repeat"]), pair))
    assert len(groups) == 10
    for rows in forks.values():
        assert sorted(rep for rep, _ in rows) == list(range(160))
    with (directory / "timing-summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("scenario", "accepted", "pairs", "original_p50_us", "exact_p50_us",
                         "original_p95_us", "exact_p95_us", "min_fork_p95_ratio",
                         "max_fork_p95_ratio"))
        for key, rows in sorted(groups.items()):
            a, b = zip(*rows)
            assert len(a) == 480
            ratios = []
            for fork in range(1, 4):
                fa, fb = zip(*(pair for _, pair in forks[key, fork]))
                ratios.append(p95(fa) / p95(fb))
            writer.writerow((*key, len(a), *(round(v / 1000, 3)
                                            for v in (median(a), median(b), p95(a), p95(b))),
                             round(min(ratios), 3), round(max(ratios), 3)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    summarize(parser.parse_args().directory)
