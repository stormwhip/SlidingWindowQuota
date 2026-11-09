# Artifact: Sliding-Window Quotas in Apache Hadoop YARN

Code and experimental data for the paper *Sliding-Window Quotas in Apache Hadoop
YARN: Exact Admission and Flexible Placement*.

The repository contains the Java exact quota validator, the Python prototypes and
replay drivers, the per-request measurements behind the four experimental tables,
and the scripts that rebuild the trace-derived inputs. The manuscript itself is
maintained separately.

## Contents

| Path | Description |
| --- | --- |
| `yarn-java/` | Java implementation on Hadoop 3.5.0 with the YARN-12022 baseline; policy, regression tests and ResourceManager replay drivers. |
| `prepare_public_reservation_trace.py` | Converts the KTH SP2 and CERIT-SC accounting logs into the reservation streams used by the replays. |
| `verify_bridge.py` | Correctness checks for the tangent index and reservation trial updates. |
| `measure_*.py`, `analyze_*.py`, `summarize_*.py` | Replay and measurement drivers for the reported experiments. |
| `results/` | Per-request timings, logs, and derived summaries. Raw third-party traces are not redistributed (see below). |
| `scripts/download_traces.ps1` | Downloads the CERIT-SC log, verifies both archive files by SHA-256, and can regenerate `requests.csv`. |
| `*.md` | Project notes for individual experiment rounds, including the trace-derived placement experiments. |

## Requirements

- Windows with PowerShell 7 (the replay drivers use PowerShell).
- Python 3.11; the replay scripts use the standard library only.
- JDK 17 or newer (tested with OpenJDK 26.0.1), Maven 3.9, and network access to
  Maven Central for the Hadoop 3.5.0 dependencies on the first build.

## Data

The raw workloads are public but are **not** redistributed here:

- **KTH SP2** (Lars Malinowsky, Parallel Workloads Archive), file
  `KTH-SP2-1996-2.1-cln.swf.gz`, SHA-256
  `3e152f201fd2e96808a735bced6ea6eb0bb07fdc447689cafb0750d87b264c0b`.
- **CERIT-SC** (Dalibor Klusáček and Boris Parák, JSSPP workload archive), file
  `grid-jobs.swf`, SHA-256
  `dd48cf7ca369d485b14add5d2baca70a1001c19d9b447688ff7639812673dc7c`.

Run

```powershell
pwsh -File scripts/download_traces.ps1
```

to fetch the CERIT-SC log into `results/public-trace/` and verify both hashes.
The KTH archive blocks automated clients; the script reports the archive page for
a manual download. After both files are present, run with `-Derive` to regenerate
`results/public-trace/requests.csv`. The derived file is included in the
repository, so the replays can also be run without the raw traces.

Please acknowledge the archives as requested by their maintainers:

- KTH SP2: Parallel Workloads Archive, https://www.cs.huji.ac.il/labs/parallel/workload/.
- CERIT-SC: D. Klusáček and B. Parák, "Analysis of Mixed Workloads from Shared
  Cloud Infrastructure", JSSPP 2017; JSSPP workload archive,
  https://jsspp.org/workload/.

## Reproduction

Python checks and trace-derived replays (from the repository root):

```powershell
python -B verify_bridge.py
python -B prepare_public_reservation_trace.py      # after placing both raw traces
python -B measure_online.py --out results/online-repeat
python -B measure_capacity_online.py --out results/capacity-online-repeat
```

Java policy, replay and ResourceManager experiments:

```powershell
cd yarn-java
mvn -o -B -ntp test
.\run_bridge_replay.ps1 -OutputDirectory ..\results\java-bridge
python ..\summarize_java_replay.py ..\results\java-bridge
.\run_plan_replay.ps1 -OutputDirectory ..\results\yarn-plan-replay
.\run_rm_quota.ps1 -OutputDirectory ..\results\rm-quota
```

Use a new output directory for each run. Timing results in `results/` were
collected on one machine (i9-14900HX, Windows, OpenJDK 26.0.1, logical CPU 2) and
are sensitive to the host; the included samples are the measurements reported in
the paper.

## Notes

- Measurement scripts pin the process to logical CPU 2 where supported.
- `results/public-trace/` contains the derived `requests.csv` and the per-request
  replay outputs; the two raw archive files are ignored by Git.
- Build outputs (`yarn-java/target/`) and Python caches are ignored.

## License

No license has been assigned to this repository yet. Contact the author before
reusing the code beyond reviewing and reproducing the reported experiments.
