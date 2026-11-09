$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
Push-Location -LiteralPath $PSScriptRoot
try {
  $out=(Resolve-Path ../results/public-trace).Path
  $src=(New-Item -ItemType Directory -Force "$out/exact-upstream-source").FullName
  Copy-Item -LiteralPath 'upstream/patch/CapacityOverTimePolicy.java' -Destination $src
  $test=Get-Content -Raw 'upstream/patch/TestCapacityOverTimePolicy.java'
  $test=$test.Replace('CapacityOverTimePolicy policy = new CapacityOverTimePolicy();','ExactCapacityOverTimePolicy policy = new ExactCapacityOverTimePolicy();')
  $repro=Get-Content -Raw '../results/yarn-upstream-repro/modified-test/TestCapacityOverTimePolicy.java'
  $first=$repro.LastIndexOf('  @Test',$repro.IndexOf('  public void testRejectsQuotaPeakBetweenIntegralSamples'))
  $last=$repro.LastIndexOf('}')
  $test=$test.Insert($test.LastIndexOf('}'),$repro.Substring($first,$last-$first))
  $test=$test.Replace('import java.util.Collection;',('import java.util.Collection;' + [Environment]::NewLine + 'import java.util.Collections;'))
  Set-Content -Encoding utf8NoBOM "$src/TestCapacityOverTimePolicy.java" $test
  mvn -o -B -ntp '-Dyarn.variant=exact-upstream-unadjusted' "-Dyarn.source=$src" '-Dtest=TestCapacityOverTimePolicy,ExactCapacityOverTimePolicyTest' test *> "$out/exact-upstream-unadjusted.log"
  $status=$LASTEXITCODE
  $report=Get-ChildItem 'target/exact-upstream-unadjusted/surefire-reports' -Filter 'TEST-*TestCapacityOverTimePolicy.xml'
  if (-not $report) { throw 'Missing upstream regression report' }
  [xml]$xml=Get-Content -Raw $report.FullName
  $failures=@($xml.testsuite.testcase | Where-Object { $_.failure -or $_.error })
  if ($status -eq 0 -or $failures.Count -ne 2 -or
      @($failures | Where-Object { $_.error -or $_.name -notmatch '\[(19|22)\]$' }).Count -ne 0) {
    Get-Content "$out/exact-upstream-unadjusted.log" -Tail 40
    throw 'Unexpected differences in upstream tests'
  }
  # In two old cases the rate equals the quota for 25 hours; every 24h window fits.
  $test=$test.Replace('{25 * ONEHOUR, 0.25, 1, null, PlanningQuotaException.class }',
    '{25 * ONEHOUR, 0.25, 1, null, null }')
  $test=$test.Replace('{25 * ONEHOUR, 0.05, 5, null, PlanningQuotaException.class }',
    '{25 * ONEHOUR, 0.05, 5, null, null }')
  Set-Content -Encoding utf8NoBOM "$src/TestCapacityOverTimePolicy.java" $test
  mvn -o -B -ntp '-Dyarn.variant=exact-upstream' "-Dyarn.source=$src" '-Dtest=TestCapacityOverTimePolicy,ExactCapacityOverTimePolicyTest' test *> "$out/exact-upstream.log"
  if ($LASTEXITCODE -ne 0) { Get-Content "$out/exact-upstream.log" -Tail 40; throw 'Exact regression failed' }

  $runner=Get-Process -Id $PID
  $before=$runner.ProcessorAffinity
  try {
    if (($before.ToInt64() -band 4) -eq 0) { throw 'Logical CPU 2 is unavailable' }
    $runner.ProcessorAffinity=[IntPtr]4
    foreach ($fork in 1..3) {
      mvn -o -B -ntp '-Dtest=PublicTraceQuotaReplayTest' "-Dquota.publicTraceOutput=$out/replay-$fork.csv" test *> "$out/replay-$fork.log"
      if ($LASTEXITCODE -ne 0) { Get-Content "$out/replay-$fork.log" -Tail 45; throw "Replay $fork failed" }
    }
  } finally { $runner.ProcessorAffinity=$before }
  mvn -o -B -ntp test *> "$out/full-tests.log"
  if ($LASTEXITCODE -ne 0) { Get-Content "$out/full-tests.log" -Tail 45; throw 'Full regression failed' }
} finally { Pop-Location }
