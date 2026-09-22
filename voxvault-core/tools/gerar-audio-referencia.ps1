<#
    Gera o áudio de referência da bancada de avaliação a partir dos textos em
    tests/assets, usando as vozes pt-BR do Windows.

    O ponto de ter texto de referência conhecido é poder comparar lado a lado
    o que o modelo devolveu com o que foi efetivamente dito. Sem isso, "a
    transcrição parece boa" é impressão, não medição.

    Cada fala é seguida de um silêncio longo de propósito: é exatamente ali
    que o Whisper costuma inventar texto aprendido de legendas, e a bancada
    precisa provocar esse defeito para poder verificar que ele não ocorre.
#>
[CmdletBinding()]
param(
    [string] $Destino = 'D:\VoxVault\bench',
    [int]    $SilencioSegundos = 6
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$raiz = Split-Path -Parent $PSScriptRoot
$assets = Join-Path $raiz 'tests\assets'

New-Item -ItemType Directory -Force $Destino | Out-Null

$formato = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)

function Gerar {
    param([string] $Voz, [string] $Origem, [string] $Saida)

    $linhas = [IO.File]::ReadAllLines($Origem, [Text.UTF8Encoding]::new($false)) |
        Where-Object { $_.Trim() -ne '' }

    $sintetizador = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $sintetizador.SelectVoice($Voz)
    $sintetizador.SetOutputToWaveFile($Saida, $formato)

    $prompt = New-Object System.Speech.Synthesis.PromptBuilder
    $prompt.AppendBreak([TimeSpan]::FromSeconds(2))
    foreach ($linha in $linhas) {
        $prompt.AppendText($linha)
        $prompt.AppendBreak([TimeSpan]::FromSeconds($SilencioSegundos))
    }
    $sintetizador.Speak($prompt)
    $sintetizador.SetOutputToNull()
    $sintetizador.Dispose()

    $tamanho = [math]::Round((Get-Item $Saida).Length / 1KB)
    $duracao = [math]::Round((Get-Item $Saida).Length / 32000, 1)
    Write-Host "  $Saida  -- $tamanho KB, ~$duracao s, $($linhas.Count) falas"
}

Write-Host 'Gerando audio de referencia:'
Gerar -Voz 'Microsoft Daniel' -Origem (Join-Path $assets 'referencia-mic.txt') `
      -Saida (Join-Path $Destino 'ref-mic.wav')
Gerar -Voz 'Microsoft Maria'  -Origem (Join-Path $assets 'referencia-sistema.txt') `
      -Saida (Join-Path $Destino 'ref-sistema.wav')

Copy-Item (Join-Path $assets 'referencia-mic.txt')      (Join-Path $Destino 'ref-mic.txt')      -Force
Copy-Item (Join-Path $assets 'referencia-sistema.txt')  (Join-Path $Destino 'ref-sistema.txt')  -Force
Write-Host 'Pronto.'
