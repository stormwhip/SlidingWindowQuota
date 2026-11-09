$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$out=$PSScriptRoot
$name='TestCapacityOverTimePolicy.java'
$relative='hadoop-yarn-project/hadoop-yarn/hadoop-yarn-server/hadoop-yarn-server-resourcemanager/src/test/java/org/apache/hadoop/yarn/server/resourcemanager/reservation'
$check=(New-Item -ItemType Directory -Force "$out/apply-check/$relative").FullName
Copy-Item -LiteralPath "$out/base/$name" -Destination $check
Push-Location "$out/apply-check"
try {
  git -c core.longpaths=true apply --check "$out/yarn-quota-missed-peak.patch"
  if ($LASTEXITCODE -ne 0) { throw 'Patch did not apply' }
} finally { Pop-Location }
Push-Location (Join-Path $out '../../yarn-java')
try {
  foreach ($variant in @('trunk','patch','exact-control')) {
    $src=(New-Item -ItemType Directory -Force "$out/$variant-source").FullName
    Copy-Item -LiteralPath "$out/modified-test/$name","$out/base/BaseSharingPolicyTest.java" -Destination $src
    if ($variant -eq 'trunk') {
      Copy-Item -LiteralPath '../results/yarn-sampling/trunk-source/CapacityOverTimePolicy.java' -Destination $src
    } elseif ($variant -eq 'patch') {
      Copy-Item -LiteralPath 'upstream/patch/CapacityOverTimePolicy.java' -Destination $src
    } else {
      $body=Get-Content -Raw 'upstream/patch/CapacityOverTimePolicy.java'
      $start=$body.IndexOf('    //---- check for integral violations')
      $end=$body.IndexOf('  private Resource normalizeToResource', $start)
      if ($start -lt 0 -or $end -le $start) { throw 'Exact control insertion point missing' }
      $control=@'
    long limit = Math.multiplyExact(validWindow,
        Resources.multiply(plan.getTotalCapacity(), maxAvg).getMemorySize());
    if (ExactRleWindow.proposed(plan, reservation, validWindow, checkStart, checkEnd)
        .memoryTime() > limit) {
      throw new PlanningQuotaException("Exact memory-time quota exceeded");
    }
  }

'@
      $body.Substring(0,$start)+$control+$body.Substring($end) |
        Set-Content -Encoding utf8NoBOM "$src/CapacityOverTimePolicy.java"
    }
    $target="upstream-repro-$variant"
    $test='TestCapacityOverTimePolicy#testRejectsQuotaPeakBetweenIntegralSamples'
    mvn -o -B -ntp "-Dyarn.variant=$target" "-Dyarn.source=$src" "-Dtest=$test" test *> "$out/$variant.log"
    $status=$LASTEXITCODE
    $xml=Get-ChildItem "target/$target/surefire-reports" -Filter 'TEST-*TestCapacityOverTimePolicy.xml'
    if (-not $xml) { Get-Content "$out/$variant.log" -Tail 25; throw "Missing XML for $variant" }
    [xml]$result=Get-Content -Raw $xml.FullName
    $case=$result.testsuite.testcase
    if ($case.name -ne 'testRejectsQuotaPeakBetweenIntegralSamples') { throw "Wrong test for $variant" }
    if ($variant -eq 'exact-control') {
      if ($status -ne 0 -or $case.failure -or $case.error) { throw 'Exact control did not pass' }
    } elseif ($status -eq 0 -or $case.error -or $case.failure.message -notlike '*nothing was thrown*') {
      throw "Expected assertion failure absent for $variant"
    }
    "$variant exit=$status; $($case.failure.message)"
  }
} finally { Pop-Location }
