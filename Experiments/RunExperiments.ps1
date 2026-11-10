param(
    [Parameter(Mandatory)]
    [ValidateSet('PublicTrace', 'ResourceManager', 'EventScaling')]
    [string]$Experiment,
    [Parameter(Mandatory)]
    [string]$OutputDirectory,
    [string]$TraceInput = (Join-Path $PSScriptRoot '../Results/PublicTrace/Requests.csv')
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$out = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) $Experiment
if (Test-Path -LiteralPath $out) { throw "Output already exists at $out" }
if ($Experiment -eq 'PublicTrace') { $TraceInput = (Resolve-Path -LiteralPath $TraceInput).Path }
New-Item -ItemType Directory -Path $out | Out-Null

function RunTest([string]$Log, [string[]]$Arguments) {
    & mvn -B -ntp @Arguments test *> (Join-Path $out $Log)
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath (Join-Path $out $Log) -Tail 35
        throw "Maven test failed. See $out/$Log"
    }
}

Push-Location -LiteralPath (Join-Path $PSScriptRoot '../Yarn')
try {
    switch ($Experiment) {
        'PublicTrace' {
            Copy-Item -LiteralPath $TraceInput -Destination (Join-Path $out 'Requests.csv')
            $common = @('-Dtest=PublicTraceQuotaReplayTest', '-Dquota.publicTraceQuotas=2,5,10,25',
                        "-Dquota.publicTraceInput=$TraceInput")
            foreach ($fork in 1..3) {
                RunTest "Replay$fork.log" ($common + "-Dquota.publicTraceOutput=$out/Replay$fork.csv")
            }
            RunTest 'Worlds1.log' ($common + "-Dquota.worldsOutput=$out/Worlds1.csv")
        }
        'ResourceManager' {
            foreach ($fork in 1..4) {
                RunTest "PairedRpc$fork.log" @('-Dtest=ResourceManagerQuotaTest',
                    "-Dquota.rmFork=$fork", "-Dquota.rmOutput=$out/PairedRpc$fork.csv")
            }
        }
        'EventScaling' {
            foreach ($fork in 1..3) {
                RunTest "Fork$fork.log" @('-Dtest=EventScalingTest', "-Dquota.eventScalingFork=$fork",
                    "-Dquota.eventScalingOutput=$out/Samples$fork.csv")
            }
        }
    }
} finally {
    Pop-Location
}
