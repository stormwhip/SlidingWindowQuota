param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\Results\PublicTrace'),
    [string]$KthFile = 'KTH-SP2-1996-2.1-cln.swf.gz',
    [string]$CeritFile = 'grid-jobs.swf',
    [switch]$Derive,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
if ($Derive -and (Split-Path -Leaf $OutDir) -cne 'PublicTrace') {
    throw 'Use a directory named PublicTrace when deriving requests.'
}
$null = New-Item -ItemType Directory -Force -Path $OutDir

$expected = @{
    $CeritFile = 'DD48CF7CA369D485B14ADD5D2BACA70A1001C19D9B447688FF7639812673DC7C'
    $KthFile = '3E152F201FD2E96808A735BCED6EA6EB0BB07FDC447689CAFB0750D87B264C0B'
}
$ceritUrl = 'https://jsspp.org/workload/logs/2017/cerit/grid-jobs.swf'
$kthPage = 'https://www.cs.huji.ac.il/labs/parallel/workload/l_kth_sp2/'
$kthUrl = 'https://www.cs.huji.ac.il/labs/parallel/workload/l_kth_sp2/KTH-SP2-1996-2.1-cln.swf.gz'

function Verify-Hash([string]$Path, [string]$Name) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
    if ($hash -ne $expected[$Name]) {
        throw "SHA-256 mismatch for $Name`: got $hash, expected $($expected[$Name])"
    }
    Write-Host "verified $Name ($hash)"
}

if (-not $VerifyOnly) {
    $ceritPath = Join-Path $OutDir $CeritFile
    if (-not (Test-Path -LiteralPath $ceritPath)) {
        Write-Host "downloading $ceritUrl"
        Invoke-WebRequest -Uri $ceritUrl -OutFile $ceritPath -UseBasicParsing -TimeoutSec 600
    } else {
        Write-Host "using existing $ceritPath"
    }

    $kthPath = Join-Path $OutDir $KthFile
    if (-not (Test-Path -LiteralPath $kthPath)) {
        Write-Host "downloading $kthUrl"
        $download = Join-Path $OutDir 'KthDownload.tmp'
        try {
            Invoke-WebRequest -Uri $kthUrl -OutFile $download -UseBasicParsing -TimeoutSec 600
            Verify-Hash $download $KthFile
            Move-Item -LiteralPath $download -Destination $kthPath
        } catch {
            Write-Warning "automatic download failed: $($_.Exception.Message)"
            Write-Host "Download $KthFile manually from:"
            Write-Host "  $kthPage"
            Write-Host "and place it in $OutDir, then rerun with -VerifyOnly."
        } finally {
            if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download }
        }
    }
}

foreach ($name in $expected.Keys) {
    $path = Join-Path $OutDir $name
    if (Test-Path -LiteralPath $path) {
        Verify-Hash $path $name
    } else {
        if ($Derive) { throw "missing $name in $OutDir" }
        Write-Warning "missing $name in $OutDir"
    }
}

if ($Derive) {
    $project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    Push-Location $project
    try {
        $previous = $env:SLIDING_WINDOW_QUOTA_RESULTS
        try {
            $env:SLIDING_WINDOW_QUOTA_RESULTS = Split-Path -Parent $OutDir
            python -B -m Experiments.PreparePublicReservationTrace
            if ($LASTEXITCODE -ne 0) { throw 'Trace derivation failed' }
        } finally { $env:SLIDING_WINDOW_QUOTA_RESULTS = $previous }
    } finally {
        Pop-Location
    }
}
