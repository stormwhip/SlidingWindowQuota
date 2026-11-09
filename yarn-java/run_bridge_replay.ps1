param([Parameter(Mandatory = $true)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$out = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$runner = Get-Process -Id $PID
$oldAffinity = $runner.ProcessorAffinity
Push-Location -LiteralPath $PSScriptRoot
try {
    if (($oldAffinity.ToInt64() -band 4) -eq 0) { throw 'Logical CPU 2 is unavailable' }
    $runner.ProcessorAffinity = [IntPtr]4
    foreach ($fork in 1..3) {
        $log = Join-Path $out "fork-$fork.log"
        $samples = Join-Path $out "samples-$fork.csv"
        & mvn -o -B -ntp '-Dtest=JavaQuotaReplayTest' "-Dquota.javaSamples=$samples" test *> $log
        if ($LASTEXITCODE -ne 0) { throw "Replay failed. See $log" }
    }
} finally {
    $runner.ProcessorAffinity = $oldAffinity
    Pop-Location
}
