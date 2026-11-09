"""End-to-end replay with exact decisions and updates on accepted reservations."""

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
import random
from statistics import median, quantiles
from time import perf_counter_ns

import bridge_index as bridge
import event_index as event


WINDOW = 32
SLOTS = 16
REQUESTS = 160


def breakpoint_counts(w, reservation, hi):
    return {x for x, _ in event.jumps(w, reservation) if 0 < x < hi}


def background(n, rng):
    hi = 4 * n + WINDOW
    changes = defaultdict(int)
    counts = Counter()
    for i in range(n):
        reservation = (4 * i, 4 * (i + 1), rng.randrange(2, 14))
        for x, d in event.jumps(WINDOW, reservation):
            changes[x] += d
        counts.update(breakpoint_counts(WINDOW, reservation, hi))
    priority = random.Random(101)
    events = None
    for x, d in sorted(changes.items()):
        events = event.change(events, x, d, priority)
    points = [(x, event.value_at(events, x)) for x in sorted({0, hi, *counts})]

    def build(l, r):
        if r - l == 1:
            return bridge.leaf(*points[l])
        m = (l + r) // 2
        return bridge.join(build(l, m), build(m, r))

    root = build(0, len(points))
    quota = bridge.support(root)[1] + WINDOW * (SLOTS + 1)
    return events, root, counts, quota, hi


def change_bridge(root, counts, old, new, *, fused=False):
    old_keys = breakpoint_counts(WINDOW, old, root.hi) if old else set()
    new_keys = breakpoint_counts(WINDOW, new, root.hi) if new else set()
    for x in sorted(new_keys - old_keys):
        if not counts[x]:
            root = bridge.insert(root, x, bridge.value_at(root, x))
        counts[x] += 1
    if fused:
        root = bridge.add_pieces(root, tuple(bridge.delta_segments(WINDOW, old, new)))
    else:
        if old:
            a, b, demand = old
            root = bridge.apply_reservation(root, a, b, WINDOW, -demand)
        if new:
            root = bridge.apply_reservation(root, new[0], new[1], WINDOW, new[2])
    for x in sorted(old_keys - new_keys):
        counts[x] -= 1
        if not counts[x]:
            del counts[x]
            root = bridge.delete(root, x)
    return root


def make_stream(root, span, rejection_pct, quota, hi, rng):
    priority = random.Random(701)
    active = {}
    labels = [False] * (REQUESTS * rejection_pct // 100)
    labels += [True] * (REQUESTS - len(labels))
    rng.shuffle(labels)
    stream = []
    for accept in labels:
        ticket = rng.randrange(SLOTS)
        old = active.get(ticket)
        if accept and old and rng.randrange(6) == 0:
            new = None
        else:
            length = span + rng.randrange(max(1, span // 8))
            a = rng.randrange(hi - WINDOW - length + 1)
            b = a + length
            demand = 1
            if not accept:
                lo, upper = 1, quota // min(WINDOW, length) + 1
                while upper - lo > 1:
                    d = (lo + upper) // 2
                    pieces = bridge.delta_segments(WINDOW, old, (a, b, d))
                    if event.allowed(root, 0, hi, pieces, quota):
                        lo = d
                    else:
                        upper = d
                demand = upper
            new = a, b, demand
        assert event.allowed(root, 0, hi, bridge.delta_segments(WINDOW, old, new), quota) == accept
        if accept:
            root = event.commit(root, WINDOW, old, new, priority)
            if new is None:
                del active[ticket]
            else:
                active[ticket] = new
        stream.append((old, new, accept))
    return stream


def replay(events, root, counts, quota, hi, stream, rng):
    counts = counts.copy()
    priority = random.Random(701)
    for old, new, expected in stream:
        times = {}
        methods = ["event", "bridge"]
        rng.shuffle(methods)
        for method in methods:
            started = perf_counter_ns()
            if new is None:
                # Cancelling a nonnegative reservation can only reduce usage.
                decision = True
            else:
                pieces = bridge.delta_segments(WINDOW, old, new)
                decision = (event.allowed(events, 0, hi, pieces, quota) if method == "event"
                            else bridge.allowed(root, pieces, quota))
            checked = perf_counter_ns()
            if decision:
                if method == "event":
                    events = event.commit(events, WINDOW, old, new, priority)
                else:
                    root = change_bridge(root, counts, old, new)
            ended = perf_counter_ns()
            times[method] = checked - started, ended - checked
            assert decision == expected
        yield times
    for t in range(0, hi + 1, max(1, hi // 200)):
        assert event.value_at(events, t) == bridge.value_at(root, t)


def measure(out):
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("seed", "background_slots", "event_points", "span", "target_rejection_pct",
                         "request", "accepted", "old", "new", "method", "check_ns", "update_ns"))
        for seed in (261110, 261111):
            rng = random.Random(seed)
            for n in (2048, 16384):
                events, root, counts, quota, hi = background(n, rng)
                for name, span in (("short", 7), ("half", 2 * n)):
                    for rejection in (0, 50, 90, 99):
                        requests = make_stream(events, span, rejection, quota, hi, rng)
                        samples = replay(events, root, counts, quota, hi, requests, rng)
                        for i, ((old, new, accept), times) in enumerate(zip(requests, samples, strict=True)):
                            for method, (check, update) in times.items():
                                writer.writerow((seed, n, events.count, name, rejection, i, accept,
                                                 old, new, method, check, update))


def summarize(out):
    groups = defaultdict(list)
    with (out / "samples.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = tuple(row[k] for k in ("background_slots", "span", "target_rejection_pct", "method"))
            groups[key].append((int(row["check_ns"]), int(row["update_ns"]), row["accepted"] == "True"))
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("background_slots", "span", "target_rejection_pct", "method", "requests",
                         "accepted", "mean_total_us", "p50_total_us", "p95_total_us",
                         "mean_accept_total_us", "mean_reject_total_us"))
        for key, rows in sorted(groups.items()):
            all_times = [c + u for c, u, _ in rows]
            yes = [c + u for c, u, accepted in rows if accepted]
            no = [c + u for c, u, accepted in rows if not accepted]
            writer.writerow((*key, len(rows), len(yes), sum(all_times) / len(rows) / 1000,
                             median(all_times) / 1000,
                             quantiles(all_times, n=100, method="inclusive")[94] / 1000,
                             sum(yes) / len(yes) / 1000 if yes else "",
                             sum(no) / len(no) / 1000 if no else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    measure(args.out)
    summarize(args.out)
