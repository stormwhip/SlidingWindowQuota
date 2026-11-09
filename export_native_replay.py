"""Reconstruct the selected background states and export exact Python witnesses."""

import argparse
from ast import literal_eval
from collections import defaultdict
import csv
from fractions import Fraction
from pathlib import Path
import random

from measure_online import background, make_stream


def reservation(text):
    return literal_eval(text) if text else (0, 0, 0)


def export(samples, out):
    chosen = defaultdict(list)
    with samples.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if (row["method"] == "scan" and row["span"] in ("short", "half")
                    and row["target_rejection_pct"] in ("0", "50")):
                chosen[int(row["seed"]), int(row["background_slots"])].append(row)
    with out.open("x", encoding="ascii", newline="\n") as file:
        print(len(chosen), file=file)
        for seed in (261114, 261115):
            rng = random.Random(seed)
            for n in (2048, 16384):
                shadow = random.Random()
                shadow.setstate(rng.getstate())
                demands = [shadow.randrange(2, 14) for _ in range(n)]
                events, _, _, quota, _ = background(n, rng)
                assert rng.getstate() == shadow.getstate()
                rows = chosen[seed, n]
                assert len(rows) == 640 and all(int(row["event_points"]) == events.count for row in rows)
                print(seed, n, quota, 4, file=file)
                print(*demands, file=file)
                for span in ("short", "half"):
                    for rejection in (0, 50):
                        stream = [row for row in rows if row["span"] == span
                                  and int(row["target_rejection_pct"]) == rejection]
                        assert [int(row["request"]) for row in stream] == list(range(160))
                        print(span, rejection, len(stream), file=file)
                        for row in stream:
                            limit = Fraction(row["limit"]) if row["limit"] else None
                            print(*reservation(row["old"]), *reservation(row["new"]),
                                  int(row["accepted"] == "True"),
                                  limit.numerator if limit is not None else 0,
                                  limit.denominator if limit is not None else 0,
                                  int(row["witness"]) if row["witness"] else 0, file=file)
                if n == 2048:
                    # Preserve the exact workload RNG state used before the larger background.
                    order = ["scan", "bridge"]
                    for span in (7, 257, 2 * n):
                        for rejection in (0, 50, 90, 99):
                            for _ in make_stream(events, span, rejection, quota, 4 * n + 32, rng):
                                rng.shuffle(order)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "results/capacity-three-way/samples.csv"
    export(source, args.out)
