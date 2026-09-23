<#
.SYNOPSIS
    Puts the third-party components the installer carries in place, verified.

.DESCRIPTION
    Runs before every bundle, from the beforeBuildCommand. It downloads the uv
    release pinned in tools/recursos.json, checks its SHA-256 against the pinned
    one -- a mismatch stops the build and names the component, and nothing is
    extracted -- and extracts only uv.exe into src-tauri/recursos/uv/, which git
    ignores. A copy already in place for the same version is reused without a
    download.

    Then it stages the core's sources in src-tauri/recursos/nucleo (see
    preparar-nucleo.ps1) and writes src-tauri/recursos/preparo.json, the sizes
    and thresholds the preparation screen shows, generated from
    voxvault-core/uv.lock by tools/gerar-manifesto-preparo.py with the very uv
    it just verified.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$app = Split-Path -Parent $PSScriptRoot
$recursos = Get-Content (Join-Path $PSScriptRoot "recursos.json") -Raw | ConvertFrom-Json
$uv = $recursos.uv
$destino = Join-Path $app "src-tauri\recursos\uv"
$uvExe = Join-Path $destino "uv.exe"
$marca = Join-Path $destino "versao.txt"

New-Item -ItemType Directory -Force $destino | Out-Null

$emDia = (Test-Path $uvExe) -and (Test-Path $marca) -and ((Get-Content $marca -Raw).Trim() -eq "$($uv.versao) $($uv.sha256)")
if ($emDia) {
    Write-Host "uv $($uv.versao): ja presente e conferido"
} else {
    $temporario = Join-Path ([IO.Path]::GetTempPath()) "voxvault-$($uv.arquivo)"
    Write-Host "uv $($uv.versao): baixando $($uv.url)"
    Invoke-WebRequest -Uri $uv.url -OutFile $temporario -UseBasicParsing
    $soma = (Get-FileHash $temporario -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($soma -ne $uv.sha256.ToLowerInvariant()) {
        Remove-Item $temporario -Force
        throw "uv $($uv.versao): a soma SHA-256 do arquivo baixado ($soma) nao confere com a fixada em tools/recursos.json ($($uv.sha256)). O componente foi recusado e o build interrompido."
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($temporario)
    try {
        $entrada = $zip.Entries | Where-Object { $_.Name -eq "uv.exe" } | Select-Object -First 1
        if (-not $entrada) { throw "uv $($uv.versao): o arquivo nao contem uv.exe" }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entrada, $uvExe, $true)
    } finally {
        $zip.Dispose()
        Remove-Item $temporario -Force
    }
    Set-Content -Path $marca -Value "$($uv.versao) $($uv.sha256)" -NoNewline
    Write-Host "uv $($uv.versao): conferido ($($uv.sha256)) e extraido"
}

# The core's sources, staged clean: only what the preparation builds from.
& (Join-Path $PSScriptRoot "preparar-nucleo.ps1")

$manifesto = Join-Path $app "src-tauri\recursos\preparo.json"
# The generator resolves the lock with this same uv, never whatever is on PATH.
$env:VOXVAULT_UV = $uvExe
& $uvExe run --no-project --python 3.12 --with packaging --quiet python (Join-Path $PSScriptRoot "gerar-manifesto-preparo.py") $manifesto
if ($LASTEXITCODE -ne 0) { throw "a geracao de preparo.json falhou (codigo $LASTEXITCODE)" }
Write-Host "preparo.json gerado em $manifesto"
