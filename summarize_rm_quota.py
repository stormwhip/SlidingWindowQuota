"""Summarize four loopback ResourceManager RPC runs by operation and policy."""

from collections import defaultdict
import csv
import json
from math import ceil
from pathlib import Path
from statistics import median


def summarize(root):
    result = defaultdict(dict)
    for fork in range(1, 5):
        buckets = defaultdict(dict)
        with (root / f"paired-rpc-{fork}.csv").open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                samples = buckets[row["operation"], row["policy"]]
                index = int(row["row"])
                assert index not in samples
                samples[index] = int(row["ns"])
        assert set(buckets) == {
            (operation, policy)
            for operation in ("submit", "update")
            for policy in ("legacy", "exact")
        }
        for (operation, policy), samples in buckets.items():
            assert set(samples) == set(range(400))
            values = sorted(samples.values())
            result[operation].setdefault(policy, []).append(values[ceil(.95 * len(values)) - 1] / 1e6)
    for by_policy in result.values():
        for policy, values in list(by_policy.items()):
            by_policy[policy] = {
                "fork_p95_ms": [round(v, 3) for v in values],
                "median_p95_ms": round(median(values), 3),
            }
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parent / "results" / "rm-quota"
    output = json.dumps(summarize(root), indent=2) + "\n"
    (root / "summary.json").write_text(output, encoding="utf-8")
    print(output, end="")
