$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Push-Location -LiteralPath $PSScriptRoot
try {
  $out = (New-Item -ItemType Directory -Force ../results/rm-quota).FullName
  foreach ($fork in 1..4) {
    $log = "$out/paired-rpc-$fork.log"
    mvn -B -ntp '-Dtest=ResourceManagerQuotaTest' "-Dquota.rmFork=$fork" `
      "-Dquota.rmOutput=$out/paired-rpc-$fork.csv" test *> $log
    if ($LASTEXITCODE -ne 0) {
      Get-Content $log -Tail 45
      throw "ResourceManager test run $fork failed"
    }
  }
  python -B ../summarize_rm_quota.py
  if ($LASTEXITCODE -ne 0) { throw 'ResourceManager timing summary failed' }
} finally {
  Pop-Location
}
