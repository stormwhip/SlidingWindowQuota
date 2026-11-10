"""Quota conversion, heterogeneous memory, and replacement experiments."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from Experiments import ReferenceQuota as yq
from Experiments import ReferenceReplay as replay
from Experiments.ArtifactPaths import RESULTS

OUT = RESULTS / "Reference"


def admit_all(trace, capacity, quota, variant="exact", memory_ratio=None):
    limit_c, limit_m = yq.quota_limits(capacity, quota)
    cap_m = yq.MEM_PER_CORE * capacity
    plan: dict[int, list[yq.Allocation]] = defaultdict(list)
    everything: list[yq.Allocation] = []
    decisions = []
    for i, (user, cand) in enumerate(trace):
        if memory_ratio is not None:
            cand = yq.Allocation(cand.start, cand.end, cand.cores,
                                 memory=memory_ratio[i] * cand.cores)
        instant = yq.instant_ok(everything, cand, capacity, cap_m)
        if not instant:
            decisions.append((False, "instant"))
            continue
        if variant == "exact":
            ok = yq.exact_accept(plan[user], cand, None, limit_cores=limit_c, limit_mem=limit_m)
        else:
            ok = yq.sampled_accept(plan[user], cand, None, limit_cores=limit_c,
                                   limit_mem=limit_m, dimensions="both")
        decisions.append((ok, "integral"))
        if ok:
            plan[user].append(cand)
            everything.append(cand)
    return decisions, plan, everything


def truncation():
    rows = []
    tr = replay.load_trace()
    for dataset, capacity in (("kth", 100), ("cerit", 256)):
        for quota in (2, 5, 10, 25):
            eff_c, eff_m = yq.quota_limits(capacity, quota)
            nom_c = round(capacity * quota / 100)
            nom_m = round(yq.MEM_PER_CORE * capacity * quota / 100)
            eff = admit_all(tr[dataset], capacity, quota)[0]
            nom = _exact_with(tr[dataset], capacity, nom_c, nom_m, quota)
            rows.append({
                "dataset": dataset, "quota": quota,
                "eff_cores": eff_c, "eff_mem": eff_m,
                "nom_cores": nom_c, "nom_mem": nom_m,
                "eff_admits": sum(1 for ok, _ in eff if ok),
                "nom_admits": nom,
            })
    (OUT / "Truncation.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r)


def _exact_with(trace, capacity, limit_c, limit_m, quota):
    plan = defaultdict(list)
    everything = []
    admits = 0
    for user, cand in trace:
        if not yq.instant_ok(everything, cand, capacity, yq.MEM_PER_CORE * capacity):
            continue
        if yq.exact_accept(plan[user], cand, None, limit_cores=limit_c, limit_mem=limit_m):
            plan[user].append(cand)
            everything.append(cand)
            admits += 1
    return admits


MEM_RATIOS = (512, 1024, 2048, 4096)


def multiresource():
    tr = replay.load_trace()
    rows = []
    for dataset, capacity in (("kth", 100), ("cerit", 256)):
        trace = tr[dataset]
        for quota in (2, 5, 10, 25):
            base, _, _ = admit_all(trace, capacity, quota)
            ratio = [MEM_RATIOS[(i * 2654435761) % 4] for i in range(len(trace))]
            hetero, _, _ = admit_all(trace, capacity, quota, memory_ratio=ratio)
            samp, _, _ = admit_all(trace, capacity, quota, "sampled", memory_ratio=ratio)
            limit_c, limit_m = yq.quota_limits(capacity, quota)
            # attribution against the exact plan under heterogeneous memory
            fa = fr = checks = 0
            plan = defaultdict(list)
            everything = []
            for i, (user, cand) in enumerate(trace):
                c = yq.Allocation(cand.start, cand.end, cand.cores, memory=ratio[i] * cand.cores)
                if not yq.instant_ok(everything, c, capacity, yq.MEM_PER_CORE * capacity):
                    continue
                checks += 1
                exact_ok = yq.exact_accept(plan[user], c, None, limit_cores=limit_c, limit_mem=limit_m)
                sampled_ok = yq.sampled_accept(plan[user], c, None, limit_cores=limit_c,
                                               limit_mem=limit_m, dimensions="both")
                if sampled_ok and not exact_ok:
                    fa += 1
                elif exact_ok and not sampled_ok:
                    fr += 1
                if exact_ok:
                    plan[user].append(c)
                    everything.append(c)
            # how many exact rejections are memory-bound: CPU fits, memory does not
            mem_bound = 0
            plan = defaultdict(list)
            everything = []
            for i, (user, cand) in enumerate(trace):
                c = yq.Allocation(cand.start, cand.end, cand.cores, memory=ratio[i] * cand.cores)
                if not yq.instant_ok(everything, c, capacity, yq.MEM_PER_CORE * capacity):
                    continue
                pc, pm = yq.exact_peak(plan[user], c, None)
                if pc <= limit_c * yq.DAY_MS and pm > limit_m * yq.DAY_MS:
                    mem_bound += 1
                if yq.exact_accept(plan[user], c, None, limit_cores=limit_c, limit_mem=limit_m):
                    plan[user].append(c)
                    everything.append(c)
            rows.append({
                "dataset": dataset, "quota": quota,
                "homogeneous_admits": sum(1 for ok, _ in base if ok),
                "heterogeneous_admits": sum(1 for ok, _ in hetero if ok),
                "memory_bound_checks": mem_bound,
                "hetero_sampled_admits": sum(1 for ok, _ in samp if ok),
                "hetero_checks": checks, "hetero_sampled_fa": fa, "hetero_sampled_fr": fr,
            })
    (OUT / "MultiResource.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r)


def replacement():
    tr = replay.load_trace()
    rows = []
    for dataset, capacity in (("kth", 100), ("cerit", 256)):
        for quota in (2, 5, 10, 25):
            limit_c, limit_m = yq.quota_limits(capacity, quota)
            plan: dict[int, list[yq.Allocation]] = defaultdict(list)
            everything: list[yq.Allocation] = []
            accepted = []
            for user, cand in tr[dataset]:
                if not yq.instant_ok(everything, cand, capacity, yq.MEM_PER_CORE * capacity):
                    continue
                if yq.exact_accept(plan[user], cand, None, limit_cores=limit_c, limit_mem=limit_m):
                    plan[user].append(cand)
                    everything.append(cand)
                    accepted.append((user, cand))
            fa = fr = checks = 0
            for k, (user, old) in enumerate(accepted):
                if k % 10:
                    continue
                checks += 1
                new = yq.Allocation(old.start, old.end + (old.end - old.start), min(capacity, old.cores + 2))
                hist = plan[user]
                exact_ok = yq.exact_accept(hist, new, old, limit_cores=limit_c, limit_mem=limit_m)
                sampled_ok = yq.sampled_accept(hist, new, old, limit_cores=limit_c,
                                               limit_mem=limit_m, dimensions="both")
                if sampled_ok and not exact_ok:
                    fa += 1
                elif exact_ok and not sampled_ok:
                    fr += 1
            rows.append({"dataset": dataset, "quota": quota, "checks": checks,
                         "sampled_fa": fa, "sampled_fr": fr})
    (OUT / "Replacement.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("truncation", "multiresource", "replacement", "all"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    todo = ("truncation", "multiresource", "replacement") if args.what == "all" else (args.what,)
    for name in todo:
        print(f"== {name}")
        globals()[name]()
