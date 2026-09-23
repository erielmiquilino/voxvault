<#
.SYNOPSIS
    Stages the core's sources for the installer: src-tauri/recursos/nucleo.

.DESCRIPTION
    What the preparation builds the environment from, and nothing else: the
    package's .py files with their folders, pyproject.toml, uv.lock, README.md
    and the LICENSE. No bytecode, no tests, no caches -- a checkout that ran
    the suite has __pycache__ folders all over src, and a copy of the tree
    would carry them into every installer.

    The destination is emptied first, so a module removed from the core does
    not linger in the next installer. Called by preparar-recursos.ps1.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$app = Split-Path -Parent $PSScriptRoot
$raiz = Split-Path -Parent $app
$nucleo = Join-Path $raiz "voxvault-core"
$destino = Join-Path $app "src-tauri\recursos\nucleo"

if (Test-Path $destino) { Remove-Item -Recurse -Force $destino }
New-Item -ItemType Directory -Force $destino | Out-Null

foreach ($arquivo in "pyproject.toml", "uv.lock", "README.md") {
    Copy-Item (Join-Path $nucleo $arquivo) (Join-Path $destino $arquivo)
}
Copy-Item (Join-Path $raiz "LICENSE") (Join-Path $destino "LICENSE")

$fonte = Join-Path $nucleo "src"
$copiados = 0
Get-ChildItem $fonte -Recurse -File -Filter *.py | ForEach-Object {
    $relativo = $_.FullName.Substring($fonte.Length).TrimStart("\")
    $alvo = Join-Path (Join-Path $destino "src") $relativo
    New-Item -ItemType Directory -Force (Split-Path -Parent $alvo) | Out-Null
    Copy-Item $_.FullName $alvo
    $copiados++
}
Write-Host "nucleo: $copiados arquivos .py, pyproject, lock, README e LICENSE em $destino"
