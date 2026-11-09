param([string]$Quotas = '2,5', [string]$Tag = 'q2q5')
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
Push-Location -LiteralPath $PSScriptRoot
try {
  $out=(Resolve-Path ../results/public-trace).Path
  $runner=Get-Process -Id $PID
  $before=$runner.ProcessorAffinity
  try {
    if (($before.ToInt64() -band 4) -eq 0) { throw 'Logical CPU 2 is unavailable' }
    $runner.ProcessorAffinity=[IntPtr]4
    foreach ($fork in 1..3) {
      mvn -o -B -ntp '-Dtest=PublicTraceQuotaReplayTest' "-Dquota.publicTraceQuotas=$Quotas" "-Dquota.publicTraceOutput=$out/replay-$Tag-$fork.csv" test *> "$out/replay-$Tag-$fork.log"
      if ($LASTEXITCODE -ne 0) { Get-Content "$out/replay-$Tag-$fork.log" -Tail 45; throw "Sweep replay $fork failed" }
    }
  } finally { $runner.ProcessorAffinity=$before }
} finally { Pop-Location }
