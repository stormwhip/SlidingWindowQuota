"""Summarize paired write-path timings and input change-point counts."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from pathlib import Path
from statistics import mean, median, quantiles


GROUP = ("background_slots", "span", "target_rejection_pct")
KEY = GROUP + ("seed", "request")
PHASES = ("total", "extract", "exact_check", "instant", "plan", "index")


def change_points(path):
    counts = {}
    with path.open(encoding="ascii") as file:
        workloads = int(file.readline())
        for _ in range(workloads):
            seed, slots, _, streams = file.readline().split()
            demands = list(map(int, file.readline().split()))
            assert len(demands) == int(slots)
            base = Counter()
            for i, demand in enumerate(demands):
                base[4 * i] += demand
                base[4 * (i + 1)] -= demand
            for _ in range(int(streams)):
                span, rejection, size = file.readline().split()
                events = base.copy()
                ordered = sorted(t for t, delta in events.items() if delta)
                for i in range(int(size)):
                    a, b, d, na, nb, nd, accepted, *_ = map(int, file.readline().split())
                    key = slots, span, rejection, seed, str(i)
                    counts[key] = (bisect_left(ordered, nb + 32) -
                                   bisect_left(ordered, na - 32)) if nd else None
                    if accepted:
                        events[a] -= d
                        events[b] += d
                        events[na] += nd
                        events[nb] -= nd
                        ordered = sorted(t for t, delta in events.items() if delta)
    return counts


def p95(values):
    return quantiles(values, n=100, method="inclusive")[94]


def write_summary(directory, input_path):
    h = change_points(input_path)
    samples = defaultdict(list)
    with (directory / "samples.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            samples[tuple(row[k] for k in KEY) + (row["mode"],)].append(row)
    groups = defaultdict(list)
    decisions = defaultdict(set)
    for key, rows in samples.items():
        assert sorted(int(r["repeat"]) for r in rows) == [0, 1, 2], key
        assert len({(r["accepted"], r["kind"], r["callbacks"]) for r in rows}) == 1
        for row in rows:
            assert all(int(row[f"{phase}_ns"]) >= 0 for phase in PHASES)
            assert sum(int(row[f"{phase}_ns"]) for phase in PHASES[1:]) <= int(row["total_ns"])
        decisions[key[:-1]].add(rows[0]["accepted"])
        assert key[:-1] in h, key
        groups[key[:3] + key[-1:]].append((rows, h[key[:-1]]))
    assert len(decisions) == len(h) == 2560 and len(samples) == 3 * len(h)
    assert all(len(decision) == 1 for decision in decisions.values())
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow((*GROUP, "mode", "requests", "rejected", "p50_input_change_points",
                         "p95_input_change_points", *(f"mean_{p}_us" for p in PHASES),
                         "p95_total_us", "raw_p95_total_us"))
        for key, requests in sorted(groups.items(), key=lambda g: (int(g[0][0]), *g[0][1:])):
            phased = {p: [median(int(row[f"{p}_ns"]) for row in rows)
                          for rows, _ in requests] for p in PHASES}
            counts = [count for _, count in requests if count is not None]
            rejected = sum(rows[0]["accepted"] == "false" for rows, _ in requests)
            writer.writerow((*key, len(requests), rejected, median(counts), round(p95(counts), 2),
                             *(round(mean(phased[p]) / 1000, 3) for p in PHASES),
                             round(p95(phased["total"]) / 1000, 3),
                             round(p95([int(row["total_ns"]) for rows, _ in requests for row in rows])
                                   / 1000, 3)))
    with (directory / "change-points.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow((*KEY, "input_change_points"))
        writer.writerows((*key, count) for key, count in h.items())


def write_multi(directory):
    groups = defaultdict(list)
    with (directory / "multi.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            key = int(row["segments"]), row["kind"], row["mode"]
            groups[key].append(row)
    assert len(groups) == 4 * 4 * 3
    with (directory / "multi-summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("segments", "kind", "mode", *(f"median_{p}_us" for p in PHASES)))
        for key, rows in sorted(groups.items()):
            assert sorted(int(r["repeat"]) for r in rows) == list(range(30))
            assert {r["accepted"] for r in rows} == {
                "false" if key[1] == "rejected_replace" else "true"}
            writer.writerow((*key, *(round(median(int(r[f"{p}_ns"]) for r in rows) / 1000, 3)
                                    for p in PHASES)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--input", type=Path,
                        default=Path(__file__).parent / "results/native-replay/input.txt")
    args = parser.parse_args()
    write_summary(args.directory, args.input)
    if (args.directory / "multi.csv").exists():
        write_multi(args.directory)
