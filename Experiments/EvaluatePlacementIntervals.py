"""Check interval projection and replay fixed YARN decisions with bounded delay."""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from math import ceil
from random import Random
from statistics import median
from time import perf_counter_ns
from Experiments.ArtifactPaths import RESULTS

from Experiments.AnalyzeContinuousWorlds import MEMORY_PER_CORE, WINDOW, limits, peak, rows
from Experiments.AnalyzeYarnReservationRecords import events, peak_of
from Placement.PlacementIntervals import allowed_starts, capacity_forbidden, changes, quota_forbidden, window_pieces

OUTPUT = RESULTS / "Placement"
DELAYS = (900, 3600, 14400)


def project(user, total, start, last, length, demand, window, quota, capacity):
    quota_blocks = quota_forbidden(user, start, last, length, demand, window, quota)
    physical_blocks = capacity_forbidden(total, start, last, length, demand, capacity)
    return allowed_starts(quota_blocks, physical_blocks, start, last)


def check_small():
    rng = Random(721)
    disconnected = 0
    for _ in range(20_000):
        window, length, demand = (rng.randrange(1, 9) for _ in range(3))
        start, last = rng.randrange(-5, 3), rng.randrange(4, 12)
        left, right = -8, 19
        own = {t: rng.randrange(5) for t in range(left, right)}
        total = {t: own[t] + rng.randrange(7) for t in own}
        history = [(t, t + 1, rate) for t, rate in own.items() if rate]
        committed = [(t, t + 1, rate) for t, rate in total.items() if rate]
        background_peak = max(sum(own.get(x, 0) for x in range(t - window, t))
                              for t in range(left, right + window + 1))
        quota = background_peak + rng.randrange(window * demand + 1)
        capacity = max(total.values()) + rng.randrange(demand + 1)
        actual = project(changes(history), changes(committed), start, last, length,
                         demand, window, quota, capacity)
        expected = []
        for s in range(start, last + 1):
            # Unit cells give an independent integral oracle.
            fits = all(
                sum(own.get(x, 0) + demand * (s <= x < s + length)
                    for x in range(t - window, t)) <= quota
                for t in range(s, s + length + window + 1)
            )
            if fits and all(total.get(t, 0) + demand <= capacity for t in range(s, s + length)):
                expected.append(s)
        assert [s for a, b in actual for s in range(a, b + 1)] == expected
        disconnected += len(actual) > 1
    return {"random_cases": 20_000, "cases_with_disjoint_feasible_ranges": disconnected}


def check_witnesses():
    # Average quota alone can leave two separated sets of legal starts.
    history = changes([(4, 6, 4)])
    assert project(history, history, 0, 10, 2, 3, 4, 8, 100) == [(0, 0), (8, 10)]

    # The same profile as the ResourceManager RPC regression, in hours.
    history = changes([(0, 12, 40), (52, 64, 40)])
    assert project(history, history, 12, 28, 24, 20, 24, 600, 100) == [(18, 22)]

    for length in (2, 8, 64, 512):
        history = changes([(0, length, 1)])
        shifted = project(history, history, length, 2 * length, length, 1,
                          2 * length, length, 1)
        assert shifted == [(2 * length, 2 * length)]
        occupied = changes([(0, length, 1), (2 * length, 3 * length, 1)])
        other_user = []
        for t in range(2 * length, 3 * length):
            assert project(changes(other_user), history, t, t, 1, 1,
                           2 * length, length, 1) == [(t, t)]
            assert project([], occupied, t, t, 1, 1, 2 * length, length, 1) == []
            other_user.append((t, t + 1, 1))


def fits_at(own, committed, start, length, demand, quota, capacity):
    end = start + length
    if peak(own + [(start, end, demand)], start, end) > quota:
        return False
    return all(
        sum(rate for a, b, rate in committed if a <= t < b) + demand <= capacity
        for t in {start, *(a for a, _, _ in committed if start < a < end)}
    )


def verify_commit(history, total_edges, start, length, demand, quota, capacity):
    end = start + length
    profile = events(history + [(start, end, demand)], start - WINDOW, end + WINDOW)
    assert peak_of(profile, WINDOW, start, end + WINDOW)[0] <= quota
    load = 0
    for t, delta in total_edges:
        if t >= end:
            break
        if t > start:
            assert load + demand <= capacity
        load += delta
    assert load + demand <= capacity


def first_on_grid(ranges, origin, step):
    for a, b in ranges:
        candidate = origin + (a - origin + step - 1) // step * step
        if candidate <= b:
            return candidate
    return None


def cached_grid(own, committed, start, last, length, demand, quota, capacity, step):
    """Enumerate starts with cached physical feasibility and cached history integrals."""
    physical = capacity_forbidden(changes(committed), start, last, length, demand, capacity)
    ranges = allowed_starts([], physical, start, last)
    pieces = list(window_pieces(changes(own), WINDOW, start, last + length + WINDOW))
    times = [left for left, _, _, _ in pieces]

    def fits(s):
        tail = s + length + WINDOW
        small, large = sorted((length, WINDOW))
        for t, overlap in ((s, 0), (s + small, small), (s + large, small), (tail, 0)):
            left, _, value, slope = pieces[bisect_right(times, t) - 1]
            if value + (t - left) * slope + demand * overlap > quota:
                return False
        for i in range(bisect_right(times, s), bisect_left(times, tail)):
            t, _, value, _ = pieces[i]
            if value + demand * min(small, t - s, tail - t) > quota:
                return False
        return True

    for a, b in ranges:
        first = start + (a - start + step - 1) // step * step
        for s in range(first, b + 1, step):
            if fits(s):
                return s
    return None


def trial_bench(cases):
    rng = Random(8815)
    timings = defaultdict(list)
    for own, committed, start, length, demand, quota, capacity in cases:
        step, last = 60, start + 3600

        reference = next((s for s in range(start, last + 1, step)
                          if fits_at(own, committed, s, length, demand, quota, capacity)), None)

        def scan():
            return cached_grid(own, committed, start, last, length, demand, quota, capacity, step)

        def intervals():
            ranges = project(changes(own), changes(committed), start, last, length,
                             demand, WINDOW, quota, capacity)
            return first_on_grid(ranges, start, step)

        measured = defaultdict(list)
        for _ in range(3):
            calls = [("enumerate", scan), ("projection", intervals)]
            rng.shuffle(calls)
            answers = {}
            for name, operation in calls:
                before = perf_counter_ns()
                answers[name] = operation()
                measured[name].append(perf_counter_ns() - before)
            assert answers["enumerate"] == answers["projection"] == reference
        for name, values in measured.items():
            timings[name].append(median(values) / 1000)
    return {"cases": len(cases), "grid_seconds": 60, "delay_seconds": 3600,
            **{f"{name}_p95_us": round(sorted(values)[ceil(len(values) * .95) - 1], 1)
               for name, values in timings.items()}}


def trace_replay():
    requests = {(row["dataset"], int(row["row"])): row for row in rows("Requests.csv")}
    decisions = defaultdict(list)
    for row in rows("Worlds1.csv"):
        decisions[row["dataset"], int(row["quota_percent"])].append(row)
    results, benches = [], []
    for (dataset, quota_percent), world in sorted(decisions.items()):
        history = defaultdict(list)
        total = []
        counts, saves, delays = Counter(), Counter(), []
        bench = []
        first_example = None
        for row in sorted(world, key=lambda r: int(r["row"])):
            request = requests[dataset, int(row["row"])]
            start, length, demand, capacity = (int(request[key]) for key in
                ("start_seconds", "duration_seconds", "cores", "capacity"))
            user = request["user"]
            decision = row["exact"]
            if decision == "ACCEPT":
                allocation = (start, start + length, demand)
                total.append(allocation)
                history[user].append(allocation)
            if request["measured"] != "1":
                continue
            counts[decision] += 1
            if decision != "INTEGRAL":
                continue
            cpu_quota, memory_quota = limits(capacity, quota_percent)
            quota = min(cpu_quota * WINDOW, memory_quota * WINDOW // MEMORY_PER_CORE)
            if demand * min(length, WINDOW) > quota:
                counts["standalone_violation"] += 1
                continue
            last = start + max(DELAYS)
            own = [(a, b, d) for a, b, d in history[user]
                   if b > start - WINDOW and a < last + length + WINDOW]
            committed = [(a, b, d) for a, b, d in total if b > start and a < last + length]
            case = own, committed, start, length, demand, quota, capacity
            assert not fits_at(*case)
            legal = project(changes(own), changes(committed), start, last, length,
                            demand, WINDOW, quota, capacity)
            if legal:
                earliest = legal[0][0]
                delay = earliest - start
                assert delay > 0
                assert fits_at(own, committed, earliest, length, demand, quota, capacity)
                assert not fits_at(own, committed, earliest - 1, length, demand, quota, capacity)
                for bound in DELAYS:
                    saves[bound] += delay <= bound
                delays.append(delay)
                if first_example is None:
                    first_example = {"row": int(row["row"]), "start": start, "duration": length,
                                     "cores": demand, "earliest": earliest, "ranges": legal}
            if len(bench) < 30:
                bench.append(case)
        assert sum(counts[key] for key in ("ACCEPT", "INTEGRAL", "INSTANT")) == 1200
        benches.extend(bench)
        results.append({
            "dataset": dataset, "quota_percent": quota_percent,
            "accepted_fixed": counts["ACCEPT"], "integral_rejected": counts["INTEGRAL"],
            "instant_rejected": counts["INSTANT"], "intrinsically_over_quota": counts["standalone_violation"],
            "recovered_within_seconds": {bound: saves[bound] for bound in DELAYS},
            "median_delay_seconds_at_4h": median(delays) if delays else None,
            "example": first_example,
        })
    return results, trial_bench(benches)


def replay_continuous(requests, percent, slack, *, max_duration=None, max_work=None):
    """Yield the initial verdict and committed start for each request."""
    history, committed = defaultdict(list), []
    for request in requests:
        start, length, demand, capacity = (int(request[k]) for k in
            ("start_seconds", "duration_seconds", "cores", "capacity"))
        retry = (request["measured"] == "1"
                 and (max_duration is None or length <= max_duration)
                 and (max_work is None or demand * length <= max_work))
        last = start + (slack if retry else 0)
        user = request["user"]
        cpu, memory = limits(capacity, percent)
        quota = min(cpu * WINDOW, memory * WINDOW // MEMORY_PER_CORE)
        local = [(a, b, d) for a, b, d in history[user]
                 if b > start - WINDOW and a < last + length + WINDOW]
        total = [(a, b, d) for a, b, d in committed if b > start and a < last + length]
        own_edges, total_edges = changes(local), changes(total)
        placement = None
        if capacity_forbidden(total_edges, start, start, length, demand, capacity):
            decision = "INSTANT"
        elif quota_forbidden(own_edges, start, start, length, demand, WINDOW, quota):
            decision = "INTEGRAL"
            if last > start:
                legal = project(own_edges, total_edges, start, last, length, demand,
                                WINDOW, quota, capacity)
                if legal:
                    placement = legal[0][0]
                    verify_commit(local, total_edges, placement, length, demand, quota, capacity)
        else:
            decision = "ACCEPT"
            placement = start
        if placement is not None:
            allocation = (placement, placement + length, demand)
            history[user].append(allocation)
            committed.append(allocation)
        yield decision, placement


def continuous():
    requests = {(row["dataset"], int(row["row"])): row for row in rows("Requests.csv")}
    groups = defaultdict(list)
    for row in rows("Worlds1.csv"):
        groups[row["dataset"], int(row["quota_percent"])].append(row)
    results = []
    for (dataset, percent), decisions in sorted(groups.items()):
        ordered = sorted(decisions, key=lambda r: int(r["row"]))
        jobs = [requests[dataset, int(row["row"])] for row in ordered]
        for slack in (0, 3600, 14400):
            accepts = shifted = 0
            for row, request, (decision, placement) in zip(
                    ordered, jobs, replay_continuous(jobs, percent, slack), strict=True):
                measured = request["measured"] == "1"
                if not slack or not measured:
                    assert decision == row["exact"], (dataset, percent, row, decision)
                if measured and placement is not None:
                    accepts += 1
                    shifted += placement != int(request["start_seconds"])
            results.append({"dataset": dataset, "quota_percent": percent, "delay_seconds": slack,
                            "accepted": accepts, "accepted_after_shift": shifted})
    return results


if __name__ == "__main__":
    check_witnesses()
    checked = check_small()
    checked["deterministic_witnesses"] = 6
    print(checked, flush=True)
    trace, timing = trace_replay()
    result = {"validation": checked, "same_history": trace, "search_timing": timing,
              "continuous": continuous()}
    OUTPUT.mkdir(exist_ok=True)
    payload = json.dumps(result, indent=2) + "\n"
    (OUTPUT / "Summary.json").write_text(payload, encoding="utf-8")
    print(payload, end="")
