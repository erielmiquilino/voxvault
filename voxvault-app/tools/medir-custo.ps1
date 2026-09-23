<#
.SYNOPSIS
    Measures the cost of the whole VoxVault process tree against the ceiling.

.DESCRIPTION
    The measured set is declared, not guessed: the host process, every process
    of the embedded browser component and its descendants, the resident service
    and its descendants. Measuring only the host would produce a flattering and
    false number, because the webview runs in separate processes and that is
    exactly where the cost of a badly built interface shows up.

    The tree is re-enumerated on every tick. WebView2 spawns and retires
    renderer and GPU processes during a session, so a set captured once at the
    start would silently stop covering the processes that appeared later.

    CPU is reported as a share of the whole machine: the sum of each process's
    processor-time delta over the interval, divided by the interval times the
    number of logical processors. That is the same denominator the ceiling uses.

.PARAMETER Minutos
    How long to sample. The requirement is a 60-minute recording.

.PARAMETER IntervaloSegundos
    Sampling period. The requirement fixes this at 5 seconds.

.PARAMETER Janela
    "aberta" or "bandeja". The ceiling is different for each. Closing or
    minimizing the window collapses the app to the tray and destroys the
    webview, so "bandeja" measures the process with no window at all -- and it
    is the figure that matters most: for most of a meeting nobody looks at the
    window.

.PARAMETER Gravando
    Whether a recording runs during the measurement. In the tray the ceiling
    depends on it: 250 MB while recording, and 100 MB with at most 1% of
    processor on average while idle.

.PARAMETER Saida
    Optional path for a CSV of every sample.
#>
[CmdletBinding()]
param(
    [int] $Minutos = 60,
    [int] $IntervaloSegundos = 5,
    [ValidateSet("aberta", "bandeja")]
    [string] $Janela = "aberta",
    [switch] $Gravando,
    [string] $Saida = ""
)

$ErrorActionPreference = "Stop"

# Names that make up the measured set.
$NOME_HOSPEDEIRO = @("voxvault-app", "VoxVault")
$NOME_WEBVIEW = @("msedgewebview2")
# The resident service runs inside the core's managed interpreter.
$NOME_SERVICO = @("voxvault", "python", "pythonw")

$LIMITE_CPU_MEDIA = 8.0
$LIMITE_CPU_JANELA = 15.0
$LIMITE_MEM_ABERTA_MB = 700
$LIMITE_MEM_BANDEJA_GRAVANDO_MB = 250
$LIMITE_MEM_BANDEJA_OCIOSA_MB = 100
$LIMITE_CPU_BANDEJA_OCIOSA = 1.0

$processadores = [Environment]::ProcessorCount

Add-Type -Namespace VoxVault -Name Janela -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
'@

function Set-EstadoDaJanela {
    <#
      Drive the host into the state being measured, instead of asking the
      operator to remember to do it. A measurement labelled "tray" that was
      taken with the window open is worse than no measurement.

      For the tray, minimizing is enough: the app answers it by destroying the
      window, which is then checked to be really gone.
    #>
    param([string] $Estado)

    $hospedeiro = Get-Process -Name "voxvault-app" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $hospedeiro) { return $false }

    if ($Estado -eq "bandeja") {
        $janela = Get-Process -Name "voxvault-app" -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowTitle -eq "VoxVault" } | Select-Object -First 1
        if ($janela) {
            [void][VoxVault.Janela]::ShowWindow($janela.MainWindowHandle, 6)
            Start-Sleep -Seconds 3
        }
        # Collapsed means destroyed: no webview process left under the host.
        $webviews = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" |
            Where-Object { $_.ParentProcessId -eq $hospedeiro.Id })
        return $webviews.Count -eq 0
    }
    if ($hospedeiro.MainWindowHandle -eq 0) { return $false }
    [void][VoxVault.Janela]::ShowWindow($hospedeiro.MainWindowHandle, 9)
    Start-Sleep -Milliseconds 800
    return -not [VoxVault.Janela]::IsIconic($hospedeiro.MainWindowHandle)
}

function Get-Arvore {
    <#
      Every process in the measured set, by descent from the roots plus the
      resident service located by its executable path.
    #>
    $todos = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine

    $porPai = @{}
    foreach ($p in $todos) {
        if (-not $porPai.ContainsKey($p.ParentProcessId)) { $porPai[$p.ParentProcessId] = @() }
        $porPai[$p.ParentProcessId] += $p
    }

    $raizes = @($todos | Where-Object {
        $nome = [IO.Path]::GetFileNameWithoutExtension($_.Name)
        $NOME_HOSPEDEIRO -contains $nome
    })

    # The resident service is not a child of the app -- it is deliberately
    # detached, so that closing the window cannot take a queue down with it.
    # It is therefore located by path, not by descent: the checkout's
    # environment in development, the prepared one once installed. Matching
    # only the checkout measured an installed app without its service.
    $servico = @($todos | Where-Object {
        $nome = [IO.Path]::GetFileNameWithoutExtension($_.Name)
        ($NOME_SERVICO -contains $nome) -and
        $_.ExecutablePath -and
        (($_.ExecutablePath -like "*voxvault-core*") -or
         ($_.ExecutablePath -like "*\.voxvault\runtime\*"))
    })
    $raizes += $servico

    # Everything under the service's launcher belongs to the service: the
    # interpreter it starts runs from the managed Python install, not from
    # voxvault-core, and would otherwise be counted as the app's.
    $script:grupos = @{}
    $vistos = @{}
    $fila = [System.Collections.Queue]::new()
    foreach ($r in $raizes) {
        $grupo = if ($servico -contains $r) { "servico" } else { "aplicativo" }
        $fila.Enqueue(@($r, $grupo))
    }

    while ($fila.Count -gt 0) {
        $par = $fila.Dequeue()
        $atual = $par[0]
        if ($vistos.ContainsKey($atual.ProcessId)) { continue }
        $vistos[$atual.ProcessId] = $atual
        $script:grupos[$atual.ProcessId] = $par[1]
        if ($porPai.ContainsKey($atual.ProcessId)) {
            foreach ($filho in $porPai[$atual.ProcessId]) { $fila.Enqueue(@($filho, $par[1])) }
        }
    }

    # A WebView2 process that the descent missed would be a hole in the sum, so
    # it is reported rather than quietly dropped.
    $webviewsForaDaArvore = @($todos | Where-Object {
        $nome = [IO.Path]::GetFileNameWithoutExtension($_.Name)
        ($NOME_WEBVIEW -contains $nome) -and (-not $vistos.ContainsKey($_.ProcessId)) -and
        ($_.CommandLine -like "*voxvault*" -or $_.CommandLine -like "*VoxVault*")
    })
    foreach ($w in $webviewsForaDaArvore) {
        $vistos[$w.ProcessId] = $w
        $script:grupos[$w.ProcessId] = "aplicativo"
    }

    return $vistos
}

$limiteMemoria = if ($Janela -eq "aberta") { $LIMITE_MEM_ABERTA_MB }
    elseif ($Gravando) { $LIMITE_MEM_BANDEJA_GRAVANDO_MB }
    else { $LIMITE_MEM_BANDEJA_OCIOSA_MB }
$limiteCpuMedia = if ($Janela -eq "bandeja" -and -not $Gravando) { $LIMITE_CPU_BANDEJA_OCIOSA } else { $LIMITE_CPU_MEDIA }
$estadoOk = Set-EstadoDaJanela -Estado $Janela

Write-Host ""
Write-Host "VoxVault - medicao de custo do conjunto de processos"
Write-Host "  processadores logicos : $processadores"
Write-Host "  duracao               : $Minutos min"
Write-Host "  amostragem            : a cada $IntervaloSegundos s"
Write-Host "  janela                : $Janela, $(if ($Gravando) { 'gravando' } else { 'sem gravacao' }) (limite de memoria $limiteMemoria MB)"
if (-not $estadoOk) {
    Write-Host "  AVISO: nao foi possivel confirmar o estado da janela; o numero abaixo"
    Write-Host "         nao pode ser atribuido com seguranca ao estado '$Janela'."
}
[void](Get-Arvore)
if (-not ($script:grupos.Values -contains "servico")) {
    Write-Host "  AVISO: nenhum processo do servico residente foi encontrado; a soma"
    Write-Host "         abaixo e so a do aplicativo."
}
Write-Host ""

$anterior = @{}       # pid -> TotalProcessorTime ticks, as of $anteriorEm
$anteriorEm = $null   # high-resolution timestamp of that reading
$amostras = @()
$fim = (Get-Date).AddMinutes($Minutos)
$picoDetalhe = $null
$picoMem = 0.0
$frequencia = [Diagnostics.Stopwatch]::Frequency

while ((Get-Date) -lt $fim) {
    $arvore = Get-Arvore

    # The wall-clock interval must be the one between the two processor-time
    # readings, not the sleep. Timing the sleep instead would divide a delta
    # taken over one window by the length of a different one -- close enough
    # to look right and wrong by exactly the enumeration cost.
    $cpuTicks = 0.0
    $memBytes = 0.0
    # Tracked apart so the total stays decomposable. The ceiling covers the sum,
    # but a sum nobody can break down cannot be acted on when it is exceeded --
    # and on a machine where another process tree may also be exercising the
    # core, it is the only way to say whose cost was measured.
    $cpuTicksServico = 0.0
    $memBytesServico = 0.0
    $atual = @{}
    $detalhe = @()
    $agora = [Diagnostics.Stopwatch]::GetTimestamp()

    foreach ($processo in $arvore.Keys) {
        try {
            $p = Get-Process -Id $processo -ErrorAction Stop
        } catch {
            # A renderer that retired between enumeration and reading. Its
            # processor time up to that point is lost, which understates the
            # sample slightly; the alternative is to abort the sample entirely.
            continue
        }
        $ticks = $p.TotalProcessorTime.Ticks
        $atual[$processo] = $ticks
        $memBytes += $p.WorkingSet64
        $delta = if ($anterior.ContainsKey($processo)) {
            [Math]::Max(0, $ticks - $anterior[$processo])
        } else { 0 }
        $cpuTicks += $delta

        $ehServico = $script:grupos[$processo] -eq "servico"
        if ($ehServico) {
            $memBytesServico += $p.WorkingSet64
            $cpuTicksServico += $delta
        }

        $detalhe += [pscustomobject]@{
            Pid   = $processo
            Nome  = $arvore[$processo].Name
            Grupo = if ($ehServico) { "servico" } else { "aplicativo" }
            MB    = [Math]::Round($p.WorkingSet64 / 1MB, 1)
        }
    }

    if ($null -ne $anteriorEm) {
        $decorridoMs = (($agora - $anteriorEm) / $frequencia) * 1000.0
        # Ticks are 100 ns. CPU share = processor-ms used / (wall-ms * cores).
        $cpuMs = $cpuTicks / 10000.0
        $cpuPercent = if ($decorridoMs -gt 0) {
            ($cpuMs / ($decorridoMs * $processadores)) * 100.0
        } else { 0.0 }
        $memMB = $memBytes / 1MB

        $cpuServicoPct = if ($decorridoMs -gt 0) {
            (($cpuTicksServico / 10000.0) / ($decorridoMs * $processadores)) * 100.0
        } else { 0.0 }

        $amostras += [pscustomobject]@{
            Instante      = (Get-Date).ToString("o")
            Processos     = $arvore.Count
            CpuPct        = [Math]::Round($cpuPercent, 3)
            MemMB         = [Math]::Round($memMB, 1)
            CpuServicoPct = [Math]::Round($cpuServicoPct, 3)
            MemServicoMB  = [Math]::Round($memBytesServico / 1MB, 1)
        }

        if ($memMB -gt $picoMem) {
            $picoMem = $memMB
            $picoDetalhe = $detalhe | Sort-Object MB -Descending
        }

        Write-Host ("  {0}  processos {1,3}  cpu {2,6:N2}%  memoria {3,7:N1} MB" -f `
            (Get-Date -Format "HH:mm:ss"), $arvore.Count, $cpuPercent, $memMB)
    }

    $anterior = $atual
    $anteriorEm = $agora
    Start-Sleep -Seconds $IntervaloSegundos
}

if ($amostras.Count -eq 0) {
    Write-Host "Nenhuma amostra coletada: o aplicativo estava em execucao?"
    exit 1
}

$mediaCpu = ($amostras | Measure-Object CpuPct -Average).Average
$picoCpu = ($amostras | Measure-Object CpuPct -Maximum).Maximum
$mediaMem = ($amostras | Measure-Object MemMB -Average).Average
$picoMemMB = ($amostras | Measure-Object MemMB -Maximum).Maximum

# Worst rolling 60-second window, which is what the second limit constrains.
$porJanela = [Math]::Max(1, [int](60 / $IntervaloSegundos))
$piorJanela = 0.0
if ($amostras.Count -ge $porJanela) {
    for ($i = 0; $i -le $amostras.Count - $porJanela; $i++) {
        # Not `$janela`: PowerShell names are case-insensitive, that is the
        # -Janela parameter, and its ValidateSet refuses an array.
        $trecho = $amostras[$i..($i + $porJanela - 1)]
        $media = ($trecho | Measure-Object CpuPct -Average).Average
        if ($media -gt $piorJanela) { $piorJanela = $media }
    }
} else {
    $piorJanela = $mediaCpu
}

Write-Host ""
Write-Host "Resultado -------------------------------------------------------"
Write-Host ("  amostras                        : {0}" -f $amostras.Count)
Write-Host ("  processador, media do conjunto  : {0:N2}%   (limite {1}%)" -f $mediaCpu, $limiteCpuMedia)
Write-Host ("  processador, pior janela de 60s : {0:N2}%   (limite {1}%)" -f $piorJanela, $LIMITE_CPU_JANELA)
Write-Host ("  processador, pico instantaneo   : {0:N2}%" -f $picoCpu)
Write-Host ("  memoria residente, media        : {0:N1} MB" -f $mediaMem)
Write-Host ("     dos quais servico residente  : {0:N1} MB de media, {1:N2}% de processador" -f `
    ($amostras | Measure-Object MemServicoMB -Average).Average,
    ($amostras | Measure-Object CpuServicoPct -Average).Average)
Write-Host ("  memoria residente, pico         : {0:N1} MB  (limite {1} MB, janela {2})" -f `
    $picoMemMB, $limiteMemoria, $Janela)
Write-Host ""
Write-Host ("  veredito processador (media)    : {0}" -f $(if ($mediaCpu -le $limiteCpuMedia) { "DENTRO" } else { "ACIMA" }))
Write-Host ("  veredito processador (janela 60s): {0}" -f $(if ($piorJanela -le $LIMITE_CPU_JANELA) { "DENTRO" } else { "ACIMA" }))
Write-Host ("  veredito memoria                : {0}" -f $(if ($picoMemMB -le $limiteMemoria) { "DENTRO" } else { "ACIMA" }))

if ($picoDetalhe) {
    Write-Host ""
    Write-Host "Composicao do conjunto no pico de memoria:"
    $picoDetalhe | ForEach-Object {
        Write-Host ("    {0,-24} pid {1,-8} {2,7:N1} MB" -f $_.Nome, $_.Pid, $_.MB)
    }
}

if ($Saida) {
    $amostras | Export-Csv -Path $Saida -NoTypeInformation -Encoding UTF8
    Write-Host ""
    Write-Host "Amostras gravadas em $Saida"
}
