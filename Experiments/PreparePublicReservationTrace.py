"""Map public SWF accounting jobs to fixed-window YARN replay candidates."""

import csv
import gzip
import hashlib
import json
from Experiments.ArtifactPaths import RESULTS


ROOT = RESULTS / "PublicTrace"
SOURCES = {
    "kth": ("KTH-SP2-1996-2.1-cln.swf.gz", 100),
    "cerit": ("grid-jobs.swf", 256),
}
WARMUP = 250
MEASURED = 1200


def convert(source, capacity, count=WARMUP + MEASURED):
    opener = gzip.open if source.suffix == ".gz" else open
    rows = []
    with opener(source, "rt", encoding="utf-8", errors="replace") as file:
        for line in file:
            if not line.strip() or line.lstrip().startswith(";"):
                continue
            job, submit, wait, duration, cores, _, _, _, _, _, status, user = map(
                int, line.split(maxsplit=12)[:12])
            if submit >= 0 and wait >= 0 and duration > 0 and 0 < cores <= capacity \
                    and status == 1 and user >= 0:
                rows.append((submit, job, wait, duration, cores, user))
    rows.sort()
    if len(rows) < count:
        raise ValueError(f"Insufficient eligible jobs in {source.name}")
    return rows[:count], len(rows)


if __name__ == "__main__":
    metadata = {}
    with (ROOT / "Requests.csv").open("w", encoding="ascii", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("dataset", "row", "user", "submit_seconds", "start_seconds",
                         "duration_seconds", "cores", "capacity", "measured"))
        for dataset, (name, capacity) in SOURCES.items():
            source = ROOT / name
            rows, eligible = convert(source, capacity)
            origin = rows[0][0]
            users = {}
            for i, (submit, _, wait, duration, cores, user) in enumerate(rows):
                alias = users.setdefault(user, len(users))
                writer.writerow((dataset, i, alias, submit - origin, submit + wait - origin,
                                 duration, cores, capacity, int(i >= WARMUP)))
            with source.open("rb") as raw:
                digest = hashlib.file_digest(raw, "sha256").hexdigest()
            metadata[dataset] = dict(sha256=digest,
                                     eligible_jobs=eligible, users_in_replay=len(users),
                                     selected_jobs=len(rows), measured_jobs=MEASURED, warmup_jobs=WARMUP,
                                     capacity=capacity, first_submit=origin)
    print(json.dumps(metadata, indent=2))
