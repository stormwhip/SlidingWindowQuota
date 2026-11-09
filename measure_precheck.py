"""Compare read-only quota checks and speculative updates on fixed states."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
import argparse
import csv
from itertools import accumulate
from pathlib import Path
import random
from statistics import median, quantiles
from time import perf_counter_ns

from bridge_index import allowed, apply_reservation, delta_segments, insert, join, leaf, support, value_at


class EventScan:
    """Sorted slope jumps, with prefix moments for the two query boundaries."""

    def __init__(self, changes, lo, hi):
        self.lo, self.hi = lo, hi
        events = sorted((x, d) for x, d in changes.items() if d)
        self.times = [x for x, _ in events]
        self.slopes = list(accumulate(d for _, d in events))
        self.moments = list(accumulate(x * d for x, d in events))

    def value(self, t):
        i = bisect_right(self.times, t) - 1
        return self.times_value(i, t) if i >= 0 else 0

    def times_value(self, i, t):
        return self.slopes[i] * t - self.moments[i]

    def allowed(self, pieces, quota):
        times = self.times
        for l, r, a, b in pieces:
            l, r = max(l, self.lo), min(r, self.hi)
            if l > r:
                continue
            if self.value(l) + a * l + b > quota or self.value(r) + a * r + b > quota:
                return False
            start, stop = bisect_right(times, l), bisect_left(times, r)
            for i in range(start, stop):
                t = times[i]
                if self.times_value(i, t) + a * t + b > quota:
                    return False
        return True


def make_state(n, rng, window, demand_scale=1):
    changes = defaultdict(int)
    for i in range(n):
        a, b, d = 4 * i, 4 * i + 4, rng.randrange(2, 14) * demand_scale
        for x, sign in ((a, 1), (b, -1), (a + window, -1), (b + window, 1)):
            changes[x] += sign * d
    scan = EventScan(changes, 0, 4 * n + window)
    keys = sorted({scan.lo, scan.hi, *scan.times})

    def build(l, r):
        if r - l == 1:
            return leaf(keys[l], scan.value(keys[l]))
        m = (l + r) // 2
        return join(build(l, m), build(m, r))

    root = build(0, len(keys))
    return scan, root, len(keys)


def trial(root, a, b, d, w, quota, existing):
    for t in sorted({a, b, a + w, b + w} - existing):
        if root.lo < t < root.hi:
            root = insert(root, t, value_at(root, t))
    root = apply_reservation(root, a, b, w, d)
    return support(root)[1] <= quota


def proposals(scan, span, rng, w, quota, count):
    for index in range(count):
        length = span + rng.randrange(0, max(1, span // 8))
        a = rng.randrange(0, scan.hi - w - length + 1)
        b = a + length
        lo, hi = 0, quota // min(w, length) + 1
        while hi - lo > 1:
            d = (lo + hi) // 2
            if scan.allowed(tuple(delta_segments(w, new=(a, b, d))), quota):
                lo = d
            else:
                hi = d
        affected = bisect_right(scan.times, b + w) - bisect_left(scan.times, a)
        yield index, affected, a, b, lo, True
        yield index, affected, a, b, hi, False


def measure(path, repetitions, per_class, seed):
    rng = random.Random(seed)
    w = 32
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("state", "points", "span", "proposal", "affected", "accepted",
                         "a", "b", "demand", "window", "quota", "method", "repeat", "ns"))
        for n in (512, 4096, 32768):
            scan, root, points = make_state(n, rng, w)
            existing = {scan.lo, scan.hi, *scan.times}
            quota = support(root)[1] + w
            for label, span in (("short", 7), ("medium", 257), ("half", 2 * n)):
                batch = list(proposals(scan, span, rng, w, quota, per_class))
                for i, affected, a, b, d, accept in batch:
                    pieces = tuple(delta_segments(w, new=(a, b, d)))
                    tasks = [
                        ("scan", lambda: scan.allowed(pieces, quota)),
                        ("precheck", lambda: allowed(root, pieces, quota)),
                        ("trial", lambda: trial(root, a, b, d, w, quota, existing)),
                    ]
                    for _, run in tasks:
                        assert run() == accept
                    for rep in range(repetitions):
                        rng.shuffle(tasks)
                        for method, run in tasks:
                            started = perf_counter_ns()
                            result = run()
                            elapsed = perf_counter_ns() - started
                            assert result == accept
                            writer.writerow((n, points, label, i, affected, accept,
                                             a, b, d, w, quota, method, rep, elapsed))


def summarize(raw, path):
    repetitions = defaultdict(list)
    affected = defaultdict(dict)
    with raw.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            case = tuple(row[k] for k in ("state", "points", "span", "accepted"))
            proposal = row["proposal"]
            affected[case][proposal] = int(row["affected"])
            repetitions[case, proposal, row["method"]].append(int(row["ns"]))
    summaries = defaultdict(list)
    for (case, _, method), samples in repetitions.items():
        summaries[case, method].append(median(samples))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("state", "points", "span", "accepted", "median_affected",
                         "method", "proposals", "p50_us", "p95_us"))
        for (case, method), samples in sorted(summaries.items()):
            p95 = quantiles(samples, n=100, method="inclusive")[94]
            writer.writerow((*case, median(affected[case].values()), method, len(samples),
                             round(median(samples) / 1000, 3), round(p95 / 1000, 3)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=261108)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    raw = args.out / "samples.csv"
    measure(raw, 7, 32, args.seed)
    summarize(raw, args.out / "summary.csv")
