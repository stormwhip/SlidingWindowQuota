param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$IncludeMulti
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$out = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$runner = Get-Process -Id $PID
$oldAffinity = $runner.ProcessorAffinity
Push-Location -LiteralPath $PSScriptRoot
try {
    if (($oldAffinity.ToInt64() -band 4) -eq 0) { throw 'Logical CPU 2 is unavailable' }
    $runner.ProcessorAffinity = [IntPtr]4
    & mvn -o -B -ntp '-Dtest=YarnPlanReplayTest' "-Dquota.planSamples=$out/samples.csv" test *> "$out/measurement.log"
    if ($LASTEXITCODE -ne 0) { throw "Plan replay failed. See $out/measurement.log" }
    if ($IncludeMulti) {
        & mvn -o -B -ntp '-Dtest=YarnPlanReplayTest#multiSegmentWrites' "-Dquota.planMultiSamples=$out/multi.csv" test *> "$out/multi.log"
        if ($LASTEXITCODE -ne 0) { throw "Multi-segment replay failed. See $out/multi.log" }
    }
} finally {
    $runner.ProcessorAffinity = $oldAffinity
    Pop-Location
}
