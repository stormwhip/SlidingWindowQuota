"""Compare direct exact capacity with binary search over integer demand."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
import random
from statistics import median
from time import perf_counter_ns

from bridge_index import allowed, delta_segments, support, value_at
from demand_limit import maximum_demand, overlap
from measure_precheck import make_state


def binary_limit(root, a, b, window, quota, peak, peak_time):
    overlap_max = min(window, b - a)
    lo = (quota - peak) // overlap_max
    at_peak = overlap(peak_time, a, b, window)
    if at_peak == overlap_max:
        return lo, 0
    at_full_overlap = a + overlap_max
    hi = (quota - value_at(root, at_full_overlap)) // overlap_max + 1
    if at_peak:
        hi = min(hi, (quota - peak) // at_peak + 1)
    probes = 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if allowed(root, delta_segments(window, new=(a, b, mid)), quota):
            lo = mid
        else:
            hi = mid
        probes += 1
    return lo, probes


def measure(out):
    window = 32
    groups = defaultdict(list)
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("seed", "points", "demand_scale", "span", "peak_shortcut", "a", "b", "quota", "limit",
                         "witness", "binary_probes", "repeat", "method", "ns"))
        for seed in (261112, 261113):
            for n in (4096, 32768):
                for scale in (1, 10**12):
                    rng = random.Random(seed)
                    scan, root, points = make_state(n, rng, window, scale)
                    peak_time, peak = support(root)
                    quota = peak + 4 * window * scale
                    for label, length in (("short", 7), ("half", 2 * n)):
                        for _ in range(32):
                            a = rng.randrange(scan.hi - window - length + 1)
                            b = a + length
                            answer, witness = maximum_demand(root, a, b, window, quota)
                            known_peak = peak_time, peak
                            assert maximum_demand(root, a, b, window, quota, known_peak=known_peak) == (
                                answer, witness
                            )
                            shortcut = overlap(peak_time, a, b, window) == min(window, length)
                            integer, probes = binary_limit(root, a, b, window, quota, peak, peak_time)
                            assert integer == answer.numerator // answer.denominator
                            methods = [
                                ("direct", lambda: maximum_demand(root, a, b, window, quota,
                                                                  known_peak=known_peak), (answer, witness)),
                                ("binary", lambda: binary_limit(root, a, b, window, quota, peak, peak_time),
                                 (integer, probes)),
                            ]
                            timings = defaultdict(list)
                            for rep in range(7):
                                if rep % 2:
                                    methods.reverse()
                                for method, run, expected in methods:
                                    started = perf_counter_ns()
                                    actual = run()
                                    ns = perf_counter_ns() - started
                                    assert actual == expected
                                    timings[method].append(ns)
                                    writer.writerow((seed, points, scale, label, shortcut, a, b, quota, answer,
                                                     witness, probes, rep, method, ns))
                            groups[n, scale, label].append(
                                (median(timings["direct"]), median(timings["binary"]), probes)
                            )
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("background_slots", "demand_scale", "span", "proposals",
                         "direct_median_us", "binary_median_us", "binary_median_probes"))
        for (n, scale, label), rows in sorted(groups.items()):
            direct, binary, probes = zip(*rows)
            writer.writerow((n, scale, label, len(rows), median(direct) / 1000,
                             median(binary) / 1000, median(probes)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    measure(args.out)
