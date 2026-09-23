<#
.SYNOPSIS
    Fails when the tag does not match the version every component declares.

.DESCRIPTION
    A release named "VoxVault 0.2.0" with a 0.1.0 installer inside, or with a
    core that reports 0.1.0 to the MCP client, would be a lie nobody notices
    until a bug report quotes the wrong number. The tag is compared with the
    five places a version is declared, and every divergent one is named.

    Run before anything is built:

        pwsh voxvault-app/tools/conferir-versao.ps1 v0.1.0
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Tag
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$esperada = $Tag -replace '^v', ''
if ($esperada -notmatch '^\d+\.\d+\.\d+$') {
    throw "a tag '$Tag' nao tem a forma vX.Y.Z"
}

function Ler([string] $relativo) {
    Get-Content (Join-Path $raiz $relativo) -Raw
}

function Primeira([string] $texto, [string] $padrao) {
    $achado = [regex]::Match($texto, $padrao, "Multiline")
    if ($achado.Success) { $achado.Groups[1].Value } else { "(nao encontrada)" }
}

$componentes = [ordered]@{
    "configuracao do aplicativo (voxvault-app/src-tauri/tauri.conf.json)" =
        (Ler "voxvault-app/src-tauri/tauri.conf.json" | ConvertFrom-Json).version
    "pacote Rust (voxvault-app/src-tauri/Cargo.toml)" =
        Primeira (Ler "voxvault-app/src-tauri/Cargo.toml") '^\[package\][\s\S]*?^version\s*=\s*"([^"]+)"'
    "pacote da interface (voxvault-app/package.json)" =
        (Ler "voxvault-app/package.json" | ConvertFrom-Json).version
    "pacote do nucleo (voxvault-core/pyproject.toml)" =
        Primeira (Ler "voxvault-core/pyproject.toml") '^\[project\][\s\S]*?^version\s*=\s*"([^"]+)"'
    "modulo do nucleo (voxvault-core/src/voxvault/__init__.py)" =
        Primeira (Ler "voxvault-core/src/voxvault/__init__.py") '^__version__\s*=\s*"([^"]+)"'
}

$divergentes = @()
foreach ($nome in $componentes.Keys) {
    $valor = $componentes[$nome]
    if ($valor -ne $esperada) {
        $divergentes += "  - $nome declara $valor"
    } else {
        Write-Host "ok  $nome = $valor"
    }
}

if ($divergentes.Count -gt 0) {
    Write-Host ""
    Write-Host "A tag $Tag pede a versao $esperada, mas:"
    $divergentes | ForEach-Object { Write-Host $_ }
    throw "versao divergente em $($divergentes.Count) componente(s); nada foi compilado nem publicado"
}
Write-Host "versao $esperada confirmada nos cinco componentes"
