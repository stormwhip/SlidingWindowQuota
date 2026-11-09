"""Analyze YARN quota-attempt snapshots with an exact memory-window oracle."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def integer(row, field):
    value = row[field]
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def events(segments, start=None, end=None):
    result = Counter()
    for segment in segments:
        if len(segment) != 3 or any(type(x) is not int for x in segment):
            raise ValueError("Each segment must contain three integers")
        a, b, memory = segment
        if a >= b or memory < 0:
            raise ValueError("Invalid segment")
        if start is not None:
            a, b = max(a, start), min(b, end)
        if a < b and memory:
            result[a] += memory
            result[b] -= memory
    return result


def peak_of(events, window, first, last):
    slope_changes = Counter({first: 0, last: 0})
    for x, delta in events.items():
        slope_changes[x] += delta
        slope_changes[x + window] -= delta
    slope = value = 0
    previous = first - window
    best = -1
    witness = first
    for t, change in sorted(slope_changes.items()):
        if t > last:
            break
        value += (t - previous) * slope
        if t >= first and value > best:
            best, witness = value, t
        slope += change
        previous = t
    return best, witness


def classify(row):
    w = integer(row, "window_ms")
    q = integer(row, "quota_mib_ms")
    first, last = integer(row, "first_ms"), integer(row, "last_ms")
    if w <= 0 or q < 0 or first > last:
        raise ValueError("Invalid window, quota or check range")
    start = first - w
    if (row.get("snapshot_complete") is not True or
            integer(row, "coverage_start_ms") > start or
            integer(row, "coverage_end_ms") < last):
        raise ValueError("Incomplete history over the checked windows")
    raw = events(row["history"])
    h = sum(start < t < last and delta != 0 for t, delta in raw.items())
    retained = events(row["history"], start, last)
    retained.subtract(events(row["old"], start, last))
    current = 0
    for _, delta in sorted(retained.items()):
        current += delta
        if current < 0:
            raise ValueError("Old allocation exceeds the recorded history")
    retained.update(events(row["candidate"], start, last))
    maximum, witness = peak_of(retained, w, first, last)
    exact_accept = maximum <= q
    legacy_accept = row["legacy_outcome"] == "accept"
    if exact_accept == legacy_accept:
        label = "both_accept" if exact_accept else "both_reject"
    else:
        label = "false_accept" if legacy_accept else "false_reject"
    return label, h, maximum, witness


def summarize(rows, metadata):
    if metadata.get("kind") not in {"yarn_capture", "synthetic"} or not metadata.get("source"):
        raise ValueError("First row must declare kind and source")
    counts = defaultdict(Counter)
    changes = defaultdict(list)
    previous = None
    for row in rows:
        seq = integer(row, "seq")
        if previous is not None and seq <= previous:
            raise ValueError("Sequence numbers must be strictly increasing")
        previous = seq
        op, outcome = row["operation"], row["legacy_outcome"]
        if op not in {"submit", "replace", "cancel"}:
            raise ValueError(f"Unknown operation at sequence {seq}")
        if outcome not in {"accept", "integral_reject", "other_reject", "not_checked"}:
            raise ValueError(f"Unknown outcome at sequence {seq}")
        groups = ("all", op)
        if op == "cancel" or outcome in {"other_reject", "not_checked"}:
            label, h = "excluded", None
        else:
            if op == "submit" and row["old"]:
                raise ValueError(f"A submit has an old allocation at sequence {seq}")
            try:
                label, h, _, _ = classify(row)
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError(f"Sequence {seq}: {error}") from error
        for group in groups:
            counts[group][label] += 1
            if h is not None:
                changes[group].append(h)

    result = {}
    for group, c in counts.items():
        h = sorted(changes[group])
        n = len(h)
        result[group] = {
            "quota_attempts": n, "excluded": c["excluded"],
            **{label: c[label] for label in
               ("both_accept", "both_reject", "false_accept", "false_reject")},
            "false_accept_share": c["false_accept"] / n if n else None,
            "false_reject_share": c["false_reject"] / n if n else None,
            "history_changes_p50": h[(50 * n + 99) // 100 - 1] if n else None,
            "history_changes_p95": h[(95 * n + 99) // 100 - 1] if n else None,
        }
    return {"kind": metadata["kind"], "source": metadata["source"], "by_operation": result}


def self_test():
    common = dict(window_ms=4_000, first_ms=0, last_ms=12_000,
                  snapshot_complete=True, coverage_start_ms=-4_000,
                  coverage_end_ms=12_000, old=[], operation="submit")
    a = dict(common, seq=1, quota_mib_ms=48_000, legacy_outcome="accept",
             history=[[2_000, 4_000, 20]], candidate=[[4_000, 8_000, 10]])
    b = dict(common, seq=2, quota_mib_ms=100_000, legacy_outcome="integral_reject",
             history=[[0, 4_000, 20]], candidate=[[4_000, 7_000, 20]])
    c = dict(common, seq=3, quota_mib_ms=48_000, legacy_outcome="accept", operation="replace",
             history=[[2_000, 4_000, 20], [4_000, 8_000, 10]],
             old=[[4_000, 8_000, 10]], candidate=[[4_000, 8_000, 4]])
    d = dict(seq=4, operation="submit", legacy_outcome="other_reject")
    assert classify(a) == ("false_accept", 2, 60_000, 6_000)
    assert classify(b) == ("false_reject", 2, 80_000, 4_000)
    assert classify(c) == ("both_accept", 3, 48_000, 6_000)
    try:
        classify(dict(a, coverage_start_ms=-3_999))
    except ValueError:
        pass
    else:
        raise AssertionError("Accepted an incomplete snapshot")
    try:
        classify(dict(c, old=[[4_000, 8_000, 11]]))
    except ValueError:
        pass
    else:
        raise AssertionError("Accepted an inconsistent old allocation")
    output = summarize([a, b, c, d], {"kind": "synthetic", "source": "local regression fixtures"})
    assert output["by_operation"]["all"]["quota_attempts"] == 3
    assert output["by_operation"]["all"]["excluded"] == 1
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.input:
        with args.input.open(encoding="utf-8-sig") as stream:
            records = (json.loads(line) for line in stream if line.strip())
            metadata = next(records)
            print(json.dumps(summarize(records, metadata), ensure_ascii=False, indent=2))
    else:
        parser.error("provide a JSONL input or --self-test")
