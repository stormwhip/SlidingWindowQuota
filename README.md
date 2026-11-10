# SlidingWindowQuota

Research artifact for *Sliding-Window Quotas in Apache Hadoop YARN: Exact Admission and Flexible Placement*.

| Path | Contents |
| --- | --- |
| [Yarn](Yarn) | Java exact quota policy, pinned sampled baselines, and JUnit regression and measurement drivers |
| [Placement](Placement) | Python implementation of the feasible-start interval scan |
| [Experiments](Experiments) | Trace preparation, experiment launchers, summaries, and figure generation |
| [Results](Results) | Derived reservation streams, recorded measurements, and paper summaries |
| [Figures](Figures) | Vector figures for the missed peak and event-count scaling |
| [UpstreamPatches](UpstreamPatches) | Standalone YARN reproducer and exact-policy patch |

The exploratory implementations and research notes are available in the
[original artifact revision](https://github.com/stormwhip/SlidingWindowQuota/tree/19f1c6d6ca13cbd2d91873a558e81fe31d942113).

## Baselines

| Role | Pinned source |
| --- | --- |
| Runtime and Maven dependencies | Hadoop 3.5.0, [dbcc7cd797100e6b32cd84f85b53a5193a5f9af0](https://github.com/apache/hadoop/commit/dbcc7cd797100e6b32cd84f85b53a5193a5f9af0) |
| Sampled policy used in comparisons | YARN-12022 [PR 8815](https://github.com/apache/hadoop/pull/8815), [f99af959fd4e26cf0b359e6f341d560761d14101](https://github.com/apache/hadoop/commit/f99af959fd4e26cf0b359e6f341d560761d14101) |
| Unpatched policy used in the reproducer | Hadoop trunk, [1fdf777d777d74e9de04961e9a657e61107eadec](https://github.com/apache/hadoop/commit/1fdf777d777d74e9de04961e9a657e61107eadec) |

The YARN-12022 proposal bounds the merge horizon and accounts for replacements
by subtracting the old allocation. Its integral check uses sampled prefixes.
The exact policy retains YARN's instantaneous check and computes window totals
from rate changes at both edges.

## Reproduce

Use Python 3.11 or later, PowerShell 7, Maven 3.9, and JDK 17 or later.
Figure generation requires Matplotlib. The Java measurements in the artifact
were collected on Windows on an Intel Core i9-14900HX with JDK 26.0.1.
Run commands from the repository root.

### Correctness and published summaries

~~~powershell
mvn -B -ntp -f Yarn/pom.xml test
python -B -m Experiments.VerifySamplingBias
New-Item -ItemType Directory -Force Runs | Out-Null
Copy-Item Results Runs/Recorded -Recurse
$env:SLIDING_WINDOW_QUOTA_RESULTS = (Resolve-Path Runs/Recorded).Path
python -B -m Experiments.SummarizePublicReservationTrace
python -B -m Experiments.AnalyzeContinuousWorlds
python -B -m Experiments.ExportPaperTables
python -B -m Experiments.ReferenceReplay
python -B -m Experiments.ReferenceSensitivity all
python -B -m Experiments.QuotaConversionReplay --output Runs/QuotaConversionReplay.json
python -B -m Experiments.EvaluatePlacementIntervals
python -B -m Experiments.SummarizeRmQuota
python -B -m Experiments.PlotFigures --output Runs/Figures
Remove-Item Env:SLIDING_WINDOW_QUOTA_RESULTS
~~~

The reference runs reproduce the sampling and rounding comparison, quota
conversion, heterogeneous-memory checks, and replacements. The archived
same-state replay splits the four quotas into two sweeps. The summarizer also
accepts three fresh replay files that each cover all four quotas.

`QuotaConversionReplay` reproduces the continuous quota-conversion sensitivity
results in Section 7.5. It runs each policy on its own history under YARN's
limits and under limits rounded to the nearest resource unit, with halves
rounded upward. It checks the original-limit admission counts and peak
excesses against the archived Java replay before writing the result. The
recorded output is [Results/Reference/QuotaConversionReplay.json](Results/Reference/QuotaConversionReplay.json).

### Fresh measurements

~~~powershell
pwsh -File Experiments/RunExperiments.ps1 -Experiment PublicTrace -OutputDirectory Runs/Fresh
pwsh -File Experiments/RunExperiments.ps1 -Experiment ResourceManager -OutputDirectory Runs/Fresh
pwsh -File Experiments/RunExperiments.ps1 -Experiment EventScaling -OutputDirectory Runs/Fresh
$env:SLIDING_WINDOW_QUOTA_RESULTS = (Resolve-Path Runs/Fresh).Path
python -B -m Experiments.SummarizePublicReservationTrace
python -B -m Experiments.AnalyzeContinuousWorlds
python -B -m Experiments.ExportPaperTables
python -B -m Experiments.SummarizeRmQuota
python -B -m Experiments.PlotFigures --data Runs/Fresh/EventScaling --output Runs/FreshFigures
Remove-Item Env:SLIDING_WINDOW_QUOTA_RESULTS
~~~

Use a new output directory for a repeat. The event-count experiment times
accepted calls to the Java validation policies in three JVMs. Each JVM records
200 pairs per event count after 150 warm-up pairs. The line plots the median of
the per-JVM medians, and the band spans their minimum and maximum.

### Workload provenance and the guarded-retry experiment

The derived reservation stream is included at
[Results/PublicTrace/Requests.csv](Results/PublicTrace/Requests.csv). Obtain the
original KTH SP2 and CERIT-SC traces to regenerate that stream and to evaluate
guards on the later trace slices. The downloader verifies the archive hashes and
prints the KTH archive page when a manual download is needed.

~~~powershell
pwsh -File Experiments/DownloadTraces.ps1 -OutDir Runs/Recorded/PublicTrace
pwsh -File Experiments/DownloadTraces.ps1 -OutDir Runs/Recorded/PublicTrace -VerifyOnly -Derive
$env:SLIDING_WINDOW_QUOTA_RESULTS = (Resolve-Path Runs/Recorded).Path
python -B -m Experiments.EvaluatePlacementGuards
Remove-Item Env:SLIDING_WINDOW_QUOTA_RESULTS
~~~

Workload sources are the [Parallel Workloads Archive](https://www.cs.huji.ac.il/labs/parallel/workload/)
for Lars Malinowsky's KTH SP2 trace and the [JSSPP archive](https://jsspp.org/workload/)
for Dalibor Klusáček and Boris Parák's CERIT-SC trace. The upstream trace terms
apply to their files. Only the derived input and measurements are distributed here.

### Upstream reproducer

Apply [YarnQuotaMissedPeak.patch](UpstreamPatches/YarnQuotaMissedPeak.patch)
to the pinned trunk revision or
[YarnQuotaMissedPeakYarn12022.patch](UpstreamPatches/YarnQuotaMissedPeakYarn12022.patch)
to the pinned PR revision. Run the
TestCapacityOverTimePolicy#testRejectsQuotaPeakBetweenIntegralSamples test
in Hadoop's ResourceManager module. The test asserts rejection and exposes
the missed peak when the sampled policy accepts the reservation.

## License

The original software in this repository is available under [Apache License
2.0](LICENSE). Vendored Hadoop files retain their upstream license notices.
[NOTICE](NOTICE) records the Hadoop attribution.
