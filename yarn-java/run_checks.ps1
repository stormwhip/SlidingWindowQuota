param([Parameter(Mandatory = $true)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$out = (New-Item -ItemType Directory -Path $OutputDirectory).FullName
Push-Location -LiteralPath $PSScriptRoot
try {
    $className = 'org.apache.hadoop.yarn.server.resourcemanager.reservation.TestCapacityOverTimePolicy'
    $probeName = 'org.apache.hadoop.yarn.server.resourcemanager.reservation.PolicyProbeTest'
    $probes = Join-Path $out 'validate-samples.csv'
    $runner = Get-Process -Id $PID
    $oldAffinity = $runner.ProcessorAffinity
    try {
        if (($oldAffinity.ToInt64() -band 4) -ne 0) { $runner.ProcessorAffinity = [IntPtr]4 }
        & mvn -B -ntp '-Dtest=TestCapacityOverTimePolicy,PolicyProbeTest' "-Dquota.timingFile=$probes" test *> (Join-Path $out 'patch.log')
        $patchStatus = $LASTEXITCODE
    } finally {
        $runner.ProcessorAffinity = $oldAffinity
    }
    if ($patchStatus -ne 0) { throw "Patched tests failed. See patch.log in $out" }
    foreach ($name in @($className, $probeName)) {
        Copy-Item -LiteralPath "target/patch/surefire-reports/TEST-$name.xml" -Destination $out
    }
    $trunkSource = (New-Item -ItemType Directory -Path (Join-Path $out 'trunk-source')).FullName
    Copy-Item -LiteralPath 'upstream/trunk/CapacityOverTimePolicy.java' -Destination $trunkSource
    Copy-Item -LiteralPath 'upstream/patch/TestCapacityOverTimePolicy.java' -Destination $trunkSource
    $methods = 'TestCapacityOverTimePolicy#testUserAllocationInProgressAtEndOfCheckedWindow+testPeriodicAllocationCrossingPeriodBoundary+testUpdateReservationWithinQuota'
    & mvn -B -ntp '-Dyarn.variant=trunk-regression' "-Dyarn.source=$trunkSource" "-Dtest=$methods" test *> (Join-Path $out 'trunk.log')
    if ($LASTEXITCODE -eq 0) { throw 'Expected the three regression cases to reproduce on the trunk policy' }
    $xml = "target/trunk-regression/surefire-reports/TEST-$className.xml"
    [xml]$report = Get-Content -Raw -LiteralPath $xml
    $cases = @($report.testsuite.testcase)
    $quotaErrors = @($cases | Where-Object { $_.error.type -like '*.PlanningQuotaException' })
    if ($cases.Count -ne 3 -or $quotaErrors.Count -ne 3) {
        throw 'Trunk results did not match the three expected quota exceptions'
    }
    Copy-Item -LiteralPath $xml -Destination (Join-Path $out 'trunk-regressions.xml')
    Write-Output "Patched tests passed; three trunk regressions reproduced. Results: $out"
} finally {
    Pop-Location
}
