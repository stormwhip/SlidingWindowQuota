"""Compare sequential and fused updates on identical accepted streams."""

import argparse
import csv
from pathlib import Path
import random
from time import perf_counter_ns
from unittest.mock import patch

import bridge_index as bridge
from bridge_index import support, value_at
from measure_online import background, change_bridge, make_stream


def measure(out, compare_search=False):
    methods = (("nested", bridge.join_reference, True), ("descent", bridge.join, True)) if compare_search else (
        ("sequential", bridge.join_reference, False), ("fused", bridge.join_reference, True)
    )
    with out.open("x", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("seed", "background_slots", "span", "request", "kind",
                         "old", "new", *(f"{name}_ns" for name, _, _ in methods)))
        for seed in (261116, 261117):
            rng = random.Random(seed)
            for n in (2048, 16384):
                events, root, counts, quota, hi = background(n, rng)
                for label, span in (("short", 7), ("half", 2 * n)):
                    stream = make_stream(events, span, 0, quota, hi, rng)
                    roots = [root, root]
                    references = [counts.copy(), counts.copy()]
                    order = [0, 1]
                    for i, (old, new, accept) in enumerate(stream):
                        assert accept
                        rng.shuffle(order)
                        times = [0, 0]
                        for variant in order:
                            _, find, fused = methods[variant]
                            with patch.object(bridge, "join", find):
                                started = perf_counter_ns()
                                roots[variant] = change_bridge(roots[variant], references[variant],
                                                               old, new, fused=fused)
                                times[variant] = perf_counter_ns() - started
                        assert support(roots[0]) == support(roots[1])
                        kind = "replace" if old and new else "insert" if new else "cancel"
                        writer.writerow((seed, n, label, i, kind, old, new, *times))
                    assert references[0] == references[1]
                    for t in range(0, hi + 1, max(1, hi // 200)):
                        assert value_at(roots[0], t) == value_at(roots[1], t)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compare-search", action="store_true")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    measure(args.out, args.compare_search)
