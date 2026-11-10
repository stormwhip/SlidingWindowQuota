"""Compare guarded retries in the original replay and later chronological slices."""

from collections import defaultdict
import json
from math import ceil
from statistics import median

from Experiments.AnalyzeContinuousWorlds import WINDOW, rows
from Experiments.EvaluatePlacementIntervals import OUTPUT, replay_continuous
from Experiments.PreparePublicReservationTrace import MEASURED, ROOT, SOURCES, WARMUP, convert

QUOTAS = (2, 5, 10, 25)
BLOCKS = 4
HOUR = 3600
BLOCK_SIZE = WARMUP + MEASURED
# Name, delay budget, maximum duration, maximum share of one plan-hour in core-hours.
RULES = (
    ("fixed", 0, None, None),
    ("retry_1h", HOUR, None, None),
    ("retry_4h", 4 * HOUR, None, None),
    ("short_15m", 4 * HOUR, HOUR // 4, None),
    ("short_1h", 4 * HOUR, HOUR, None),
    ("short_small", 4 * HOUR, HOUR, 4),
)


def blocks(dataset, name, capacity):
    jobs, _ = convert(ROOT / name, capacity, BLOCKS * BLOCK_SIZE)
    for block in range(BLOCKS):
        selected = jobs[block * BLOCK_SIZE:(block + 1) * BLOCK_SIZE]
        origin = selected[0][0]
        users = {}
        yield [
            {"dataset": dataset, "row": str(i), "user": str(users.setdefault(user, len(users))),
             "submit_seconds": str(submit - origin), "start_seconds": str(submit + wait - origin),
             "duration_seconds": str(length), "cores": str(cores), "capacity": str(capacity),
             "measured": str(int(i >= WARMUP))}
            for i, (submit, _, wait, length, cores, user) in enumerate(selected)
        ]


def score(dataset, block, quota, rule, jobs, baseline, outcomes):
    accepted = short = work = new = lost = short_new = short_lost = 0
    delays = []
    for request, (_, fixed_start), (_, chosen) in zip(jobs, baseline, outcomes, strict=True):
        length = int(request["duration_seconds"])
        small = length <= HOUR
        if fixed_start is None and chosen is not None:
            new += 1
            short_new += small
        if fixed_start is not None and chosen is None:
            lost += 1
            short_lost += small
        if chosen is not None:
            accepted += 1
            short += small
            work += length * int(request["cores"])
            shift = chosen - int(request["start_seconds"])
            if shift:
                delays.append(shift)
    return {"dataset": dataset, "block": block, "quota": quota, "rule": rule,
            "accepted": accepted, "short_accepted": short, "work_core_seconds": work,
            "new": new, "lost": lost, "short_new": short_new, "short_lost": short_lost,
            "shifted": len(delays), "delays": delays}


def aggregate(results):
    groups = defaultdict(list)
    for record in results:
        split = "later" if record["block"] else "original"
        groups[split, record["rule"]].append(record)
    summary = []
    for (split, rule), records in groups.items():
        delays = sorted(d for record in records for d in record["delays"])
        totals = {k: sum(r[k] for r in records) for k in (
            "accepted", "short_accepted", "work_core_seconds", "new", "lost",
            "short_new", "short_lost", "shifted")}
        summary.append({
            "split": split, "rule": rule, "configurations": len(records), **totals,
            "net": totals["new"] - totals["lost"],
            "short_net": totals["short_new"] - totals["short_lost"],
            "positive_configs": sum(r["new"] > r["lost"] for r in records),
            "negative_configs": sum(r["new"] < r["lost"] for r in records),
            "delay_median_seconds": median(delays) if delays else None,
            "delay_p95_seconds": delays[ceil(len(delays) * .95) - 1] if delays else None,
        })
    return summary


def check_guard_counterexample():
    """Even a one-second retry can block a full batch of unit-demand requests."""
    for capacity in (4, 100, 256, 1024):
        def job(user, start, duration, demand, measured="1"):
            return {"user": user, "start_seconds": start, "duration_seconds": duration,
                    "cores": demand, "capacity": capacity, "measured": measured}

        jobs = [job("a", 0, WINDOW // 4, capacity, "0"),
                job("a", WINDOW - 1, 1, capacity)]
        jobs.extend(job("b", WINDOW, 1, 1) for _ in range(capacity))
        baseline = list(replay_continuous(jobs, 25, 0))
        assert baseline[1] == ("INTEGRAL", None)
        assert sum(s is not None for _, s in baseline) == capacity + 1
        for _, slack, duration, divisor in RULES[1:]:
            limit = capacity * HOUR // divisor if divisor else None
            retry = list(replay_continuous(jobs, 25, slack,
                                          max_duration=duration, max_work=limit))
            assert retry[1] == ("INTEGRAL", WINDOW)
            assert all(verdict == "INSTANT" and s is None for verdict, s in retry[2:])
    print("Guard counterexample verified at capacities 4, 100, 256 and 1024.", flush=True)


def main():
    check_guard_counterexample()
    original = defaultdict(list)
    for request in rows("Requests.csv"):
        original[request["dataset"]].append(request)
    java = {(r["dataset"], int(r["quota_percent"]), int(r["row"])): r["exact"]
            for r in rows("Worlds1.csv")}
    results = []
    for dataset, (name, capacity) in SOURCES.items():
        for block, jobs in enumerate(blocks(dataset, name, capacity)):
            if block == 0:
                assert jobs == original[dataset]
            for quota in QUOTAS:
                baseline = list(replay_continuous(jobs, quota, 0))
                if block == 0:
                    assert all(verdict == java[dataset, quota, i]
                               for i, (verdict, _) in enumerate(baseline))
                for rule, slack, duration, divisor in RULES:
                    limit = capacity * HOUR // divisor if divisor else None
                    outcomes = (list(replay_continuous(jobs, quota, slack, max_duration=duration,
                                                       max_work=limit)) if slack else baseline)
                    assert outcomes[:WARMUP] == baseline[:WARMUP]
                    results.append(score(dataset, block, quota, rule, jobs[WARMUP:],
                                         baseline[WARMUP:], outcomes[WARMUP:]))
            print(f"{dataset} block {block} completed", flush=True)
    summary = aggregate(results)
    artifact = {"rules": RULES, "summary": summary, "configurations": results}
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "GuardSummary.json").write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    for r in summary:
        print(r["split"], r["rule"], r["accepted"], r["net"], r["short_net"],
              r["shifted"], r["delay_p95_seconds"])


if __name__ == "__main__":
    main()
