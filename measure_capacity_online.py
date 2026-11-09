"""Paired replay of exact one-scan and bridge demand-limit queries."""

import argparse
import csv
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
import random
from statistics import median, quantiles
from time import perf_counter_ns
from unittest.mock import patch

import bridge_index as bridge
import event_index as event
from demand_limit import maximum_demand
from event_capacity import scan_capacity
from measure_online import WINDOW, background, change_bridge, make_stream


def count_at_most(root, time):
    count = 0
    while root is not None:
        if time < root.x:
            root = root.left
        else:
            count += 1 + (root.left.count if root.left else 0)
            root = root.right
    return count


def replay(events, root, counts, quota, hi, stream, rng, compare_versions=False):
    versions = ({"bridge_old": (bridge.join_reference, False), "bridge_new": (bridge.join, True)}
                if compare_versions else {"bridge": (bridge.join_reference, False)})
    roots = dict.fromkeys(versions, root)
    references = {name: counts.copy() for name in versions}
    priority = random.Random(701)
    order = ["scan", *versions]
    if compare_versions:
        # Advance the workload RNG exactly as the original two-method replay.
        # Draw the new, three-method timing order from a separate RNG.
        legacy_order = ["scan", "bridge"]
        timing_rng = random.Random()
        timing_rng.setstate(rng.getstate())
    else:
        timing_rng = rng
    for old, new, expected in stream:
        times = {}
        affected = 0
        if new is not None:
            a, b, demand = new
            affected = count_at_most(events, min(b + WINDOW, hi)) - count_at_most(
                events, max(a, 0) - 1
            )
        if compare_versions:
            rng.shuffle(legacy_order)
        timing_rng.shuffle(order)
        for method in order:
            find, fused = versions.get(method, (None, False))
            with patch.object(bridge, "join", find) if find is not None else nullcontext():
                started = perf_counter_ns()
                answer = None
                if new is not None:
                    answer = (scan_capacity(events, 0, hi, a, b, WINDOW, quota, old) if method == "scan"
                              else maximum_demand(roots[method], a, b, WINDOW, quota, old))
                accepted = new is None or answer is None or demand <= answer[0]
                checked = perf_counter_ns()
                if accepted:
                    if method == "scan":
                        events = event.commit(events, WINDOW, old, new, priority)
                    else:
                        roots[method] = change_bridge(roots[method], references[method],
                                                      old, new, fused=fused)
                ended = perf_counter_ns()
            times[method] = answer, checked - started, ended - checked
            assert accepted == expected
        assert all(times["scan"][0] == times[method][0] for method in versions), (old, new, times)
        yield affected, times
    for t in range(0, hi + 1, max(1, hi // 200)):
        expected = event.value_at(events, t)
        assert all(expected == bridge.value_at(final, t) for final in roots.values())


def measure(out, compare_versions=False):
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("seed", "background_slots", "event_points", "span", "target_rejection_pct",
                         "request", "affected", "accepted", "old", "new", "limit", "witness",
                         "method", "query_ns", "update_ns"))
        for seed in (261114, 261115):
            rng = random.Random(seed)
            for n in (2048, 16384):
                events, root, counts, quota, hi = background(n, rng)
                for label, span in (("short", 7), ("medium", 257), ("half", 2 * n)):
                    for rejection in (0, 50, 90, 99):
                        stream = make_stream(events, span, rejection, quota, hi, rng)
                        results = replay(events, root, counts, quota, hi, stream, rng, compare_versions)
                        for i, ((old, new, yes), (affected, times)) in enumerate(zip(stream, results, strict=True)):
                            for method, (answer, query, update) in times.items():
                                limit, witness = answer if answer else ("", "")
                                writer.writerow((seed, n, events.count, label, rejection, i, affected,
                                                 yes, old, new, limit, witness, method, query, update))


def summary(out):
    groups = defaultdict(list)
    with (out / "samples.csv").open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            key = tuple(row[k] for k in ("background_slots", "span", "target_rejection_pct", "method"))
            groups[key].append(row)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("background_slots", "span", "target_rejection_pct", "method",
                         "requests", "capacity_queries", "median_affected", "p50_query_us",
                         "p95_query_us", "mean_total_us", "p95_total_us", "mean_accepted_update_us"))
        for key, rows in sorted(groups.items()):
            caps = [row for row in rows if row["new"]]
            query = [int(row["query_ns"]) for row in caps]
            total = [int(row["query_ns"]) + int(row["update_ns"]) for row in rows]
            updates = [int(row["update_ns"]) for row in rows if row["accepted"] == "True"]
            writer.writerow((*key, len(rows), len(caps), median(int(r["affected"]) for r in caps),
                             median(query) / 1000, quantiles(query, n=100, method="inclusive")[94] / 1000,
                             sum(total) / len(total) / 1000,
                             quantiles(total, n=100, method="inclusive")[94] / 1000,
                             sum(updates) / len(updates) / 1000))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare-versions", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    measure(args.out, args.compare_versions)
    summary(args.out)
