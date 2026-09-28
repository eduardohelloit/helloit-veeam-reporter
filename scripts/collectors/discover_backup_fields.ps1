<#
============================================================================
 Veeam B&R - DESCOBERTA de campos de uma sessao de BACKUP
----------------------------------------------------------------------------
 NAO coleta em massa. Pega a sessao de backup MAIS RECENTE (ou de 1 job) e
 despeja TODAS as propriedades dela e dos objetos aninhados, para sabermos
 os nomes reais (ProcessedSize, TransferedSize, AvgSpeed, Bottleneck, ...)
 na SUA versao do Veeam.

 Gera um .txt com tudo. SOMENTE LEITURA.
 Rodar no servidor Veeam (ISE), PowerShell 5.1.
============================================================================
#>
# ----- CONFIG ---------------------------------------------------------------
$VbrServer = "localhost"
$OutputDir = "C:\temp\veeam-backups"
# Nome do JOB (exato ou parte dele). Vai direto pela rotina e pega a ULTIMA execucao
# via $job.FindLastSession() — rapido, NAO enumera todas as sessoes.
$JobName = "SRV-EXEMPLO - PROD"
# ----------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
$stamp   = (Get-Date).ToString("yyyyMMdd_HHmmss")
$outPath = Join-Path $OutputDir "discover_$stamp.txt"

# Buffer de saida (vai para arquivo e console)
$script:buf = New-Object System.Collections.Generic.List[string]
function W { param([string]$s = "") $script:buf.Add($s); Write-Host $s }

# Despeja TODAS as propriedades de um objeto: nome | tipo | valor (truncado)
function Dump-Object {
    param($Object, [string]$Title)
    W ""
    W "==================================================================="
    W ">> $Title"
    W "   (tipo .NET: $(if ($Object) { $Object.GetType().FullName } else { '<null>' }))"
    W "==================================================================="
    if ($null -eq $Object) { W "   <null>"; return }
    $props = $Object.PSObject.Properties | Sort-Object Name
    foreach ($p in $props) {
        $name = $p.Name
        $val  = $null
        $tname = ""
        try {
            $val = $p.Value
            if ($null -ne $val) { $tname = $val.GetType().Name }
        } catch { $val = "<erro ao ler: $($_.Exception.Message)>" }

        $disp = ""
        if ($null -eq $val) {
            $disp = "<null>"
        } elseif ($val -is [System.Collections.IEnumerable] -and $val -isnot [string]) {
            $cnt = 0; try { $cnt = @($val).Count } catch {}
            $disp = "[colecao: $cnt item(s)]"
        } else {
            $disp = "$val"
            if ($disp.Length -gt 140) { $disp = $disp.Substring(0,140) + "..." }
            $disp = $disp -replace "\r?\n", " "
        }
        W ("   {0,-34} | {1,-22} | {2}" -f $name, $tname, $disp)
    }
}

# =============================== CONEXAO ===================================
try {
    if (-not (Get-Module -Name Veeam.Backup.PowerShell)) {
        Import-Module Veeam.Backup.PowerShell -ErrorAction Stop
    }
} catch { Write-Error "Falha ao importar modulo Veeam. $($_.Exception.Message)"; return }
try {
    if (-not (Get-VBRServerSession -ErrorAction SilentlyContinue)) {
        Connect-VBRServer -Server $VbrServer
    }
} catch { Write-Error "Falha ao conectar no VBR. $($_.Exception.Message)"; return }

W "Veeam - descoberta de campos de sessao de BACKUP"
W "Gerado em: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')  |  Host: $env:COMPUTERNAME"

# ===================== ULTIMA SESSAO DO JOB (rapido) =======================
# NAO enumera todas as sessoes. Acha o job e usa $job.FindLastSession().
W ""
W "Localizando o job '$JobName'..."
$t0 = Get-Date

$job = $null
try { $job = Get-VBRJob -Name $JobName -ErrorAction SilentlyContinue } catch {}
if (-not $job) {
    # tenta por correspondencia parcial (ainda barato: jobs sao poucos)
    try { $job = Get-VBRJob | Where-Object { $_.Name -like "*$JobName*" } | Select-Object -First 1 } catch {}
}
if (-not $job) {
    W "Job '$JobName' nao encontrado. Jobs disponiveis (amostra):"
    try { Get-VBRJob | Select-Object -First 30 | ForEach-Object { W "   - $($_.Name)" } } catch {}
    $buf -join "`r`n" | Out-File $outPath -Encoding UTF8
    return
}
W ("Job encontrado: '{0}'  (levou {1:N1}s)" -f $job.Name, ((Get-Date)-$t0).TotalSeconds)

# Ultima sessao SEM enumerar tudo
$session = $null
$t1 = Get-Date
try { $session = $job.FindLastSession() } catch { W "FindLastSession() falhou: $($_.Exception.Message)" }
# Fallback: Get-VBRBackupSession filtrado por -Job (so se o metodo nao existir)
if (-not $session) {
    try { $session = Get-VBRBackupSession -Job $job -ErrorAction SilentlyContinue |
                     Sort-Object CreationTime -Descending | Select-Object -First 1 } catch {}
}
if (-not $session) { W "Nenhuma sessao encontrada para o job."; $buf -join "`r`n" | Out-File $outPath -Encoding UTF8; return }

W ("Ultima sessao: '{0}'  |  Result={1}  |  Inicio={2}  (levou {3:N1}s)" -f `
   $session.Name, $session.Result, $session.CreationTime, ((Get-Date)-$t1).TotalSeconds)

# ===================== DUMP DA SESSAO E ANINHADOS =========================
Dump-Object -Object $session            -Title "SESSION (objeto raiz)"
Dump-Object -Object $session.Info       -Title "SESSION.Info"
Dump-Object -Object $session.Progress   -Title "SESSION.Progress"
Dump-Object -Object $session.Info.Progress -Title "SESSION.Info.Progress"

# BackupStats / detalhes, se existirem
$stats = $null
try { $stats = $session.Info.BackupStats } catch {}
if (-not $stats) { try { $stats = $session.BackupStats } catch {} }
Dump-Object -Object $stats -Title "SESSION BackupStats (se houver)"

# Bottleneck (varios caminhos possiveis)
$bn = $null
foreach ($path in @('Progress.BottleneckInfo','Info.Progress.BottleneckInfo')) {
    try {
        $o = $session
        foreach ($p in $path.Split('.')) { $o = $o.$p }
        if ($o) { $bn = $o; break }
    } catch {}
}
Dump-Object -Object $bn -Title "SESSION BottleneckInfo (se houver)"

# ===================== TASK SESSIONS (por VM) =============================
# E quase sempre AQUI que ficam ProcessedSize/ReadSize/TransferedSize/AvgSpeed.
# Get-VBRTaskSession recebe a sessao especifica -> rapido (nao enumera tudo).
W ""
W "Buscando task sessions (execucao por VM) desta sessao..."
$task = $null
$t2 = Get-Date
try {
    $tasks = @(Get-VBRTaskSession -Session $session)
    $task  = $tasks | Select-Object -First 1
    W ("Task sessions encontradas: {0}  (levou {1:N1}s)" -f $tasks.Count, ((Get-Date)-$t2).TotalSeconds)
} catch {
    W "Get-VBRTaskSession falhou: $($_.Exception.Message)"
}
Dump-Object -Object $task                  -Title "TASK SESSION (1a VM) - objeto raiz"
Dump-Object -Object $task.Progress         -Title "TASK SESSION.Progress"
Dump-Object -Object $task.Info             -Title "TASK SESSION.Info"
try { Dump-Object -Object $task.Progress.BottleneckInfo -Title "TASK SESSION.Progress.BottleneckInfo" } catch {}

# =============================== GRAVA ====================================
$buf -join "`r`n" | Out-File -FilePath $outPath -Encoding UTF8
W ""
W "==================================================================="
W "Arquivo gerado: $outPath"
W "Me envie o conteudo desse .txt para mapearmos os campos certos."
W "==================================================================="
