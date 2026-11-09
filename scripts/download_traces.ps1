param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\results\public-trace'),
    [string]$KthFile = 'KTH-SP2-1996-2.1-cln.swf.gz',
    [string]$CeritFile = 'grid-jobs.swf',
    [switch]$Derive,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
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
        try {
            Invoke-WebRequest -Uri $kthUrl -OutFile $kthPath -UseBasicParsing -TimeoutSec 600
            if ((Get-Item -LiteralPath $kthPath).Length -lt 1MB) {
                Remove-Item -LiteralPath $kthPath -Force
                throw 'the archive returned an HTML page instead of the workload file'
            }
        } catch {
            Write-Warning "automatic download failed: $($_.Exception.Message)"
            Write-Host "Download $KthFile manually from:"
            Write-Host "  $kthPage"
            Write-Host "and place it in $OutDir, then rerun with -VerifyOnly."
        }
    }
}

foreach ($name in $expected.Keys) {
    $path = Join-Path $OutDir $name
    if (Test-Path -LiteralPath $path) {
        Verify-Hash $path $name
    } else {
        Write-Warning "missing $name in $OutDir"
    }
}

if ($Derive) {
    $project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    Push-Location $project
    try {
        python -B prepare_public_reservation_trace.py
    } finally {
        Pop-Location
    }
}
