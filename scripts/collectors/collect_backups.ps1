<#
============================================================================
 Veeam B&R - Coletor/Exportador de BACKUPS (jobs normais)  ->  NDJSON
----------------------------------------------------------------------------
 - Coleta sessoes de BACKUP (Get-VBRSession -Type Backup) numa janela.
 - ENRIQUECE com dados por VM (task session): duracao, tamanho lido/processado/
   transferido, velocidade media e GARGALO (Source/Proxy/Network/Target).
 - Emite UMA LINHA POR VM (task session). Para jobs de 1 VM, equivale a 1/sessao.
 - Coleta Success / Warning / Failed (configuravel).
 - Extrai o motivo BRUTO (reason_raw) de Failed/Warning.
 - Exporta NDJSON (principal) + CSV (opcional).
 - SOMENTE LEITURA. PowerShell 5.1. Rodar no servidor Veeam.
 Versao: 2.0  (caminhos de campo confirmados via discover_backup_fields.ps1)
============================================================================
#>
# ----- CONFIGURACAO (defaults; pode passar por parametro) -------------------
# ATENCAO: com EnrichWithVmDetails, janelas grandes demoram MUITO (varre todo o
# historico + 1 Get-VBRTaskSession por sessao). So aumente Hours apos validar.
param(
    [string]  $VbrServer = "localhost",
    [int]     $Hours     = 900,               # ~37 dias (garante o mes inteiro)
    [string[]]$Results   = @("Success", "Warning", "Failed"),
    [string]  $OutputDir = "C:\temp\veeam-backups",
    [bool]    $ExportCsv = $false,            # CSV so p/ conferencia; o sistema so usa o NDJSON
    [int]     $TopSlow   = 15,
    [bool]    $EnrichWithVmDetails = $true,   # dados ricos (gargalo/tamanho); mais lento
    [switch]  $Yes                            # pula a confirmacao de janela grande (uso automatico)
)

# Trava de seguranca: avisa e pede confirmacao se a janela for grande COM enriquecimento.
if ($EnrichWithVmDetails -and $Hours -gt 72 -and -not $Yes) {
    Write-Host ("AVISO: $Hours h COM enriquecimento por VM pode levar HORAS." ) -ForegroundColor Yellow
    $resp = Read-Host "Confirma? Digite S para continuar, qualquer outra tecla cancela"
    if ($resp -ne 'S' -and $resp -ne 's') { Write-Host "Cancelado." -ForegroundColor Yellow; return }
}
# ----------------------------------------------------------------------------
$ScriptVersion = "2.0"
$SourceLabel   = "Veeam PowerShell - Get-VBRSession -Type Backup + TaskSession"
$SourceType    = "Backup"
$ErrorActionPreference = "Stop"

# ============================ FUNCOES AUXILIARES =============================
function Get-ObjectPropertyValue {
    param($Object, [string[]]$Names)
    foreach ($n in $Names) {
        try {
            $val = $Object
            foreach ($part in $n.Split('.')) {
                if ($null -eq $val) { break }
                $prop = $val.PSObject.Properties[$part]
                if (-not $prop) { $val = $null; break }
                $val = $prop.Value
            }
            if ($null -ne $val -and "$val" -ne '') { return $val }
        } catch {}
    }
    return $null
}

function Format-IsoDate {
    param($dt)
    if ($null -eq $dt) { return $null }
    try {
        if ($dt -is [datetime] -and $dt.Year -le 1900) { return $null }   # 01/01/1900 = "vazio"
        return ([datetimeoffset]$dt).ToString("o")
    } catch { return $null }
}

function To-GB   { param($b) if ($null -eq $b) { return $null } try { return [math]::Round([double]$b / 1GB, 2) } catch { return $null } }
function To-MBps { param($b) if ($null -eq $b) { return $null } try { return [math]::Round([double]$b / 1MB, 1) } catch { return $null } }

# Motivo bruto (Failed/Warning) a partir do log do objeto (sessao OU task)
function Get-ReasonFromLog {
    param($Obj, [string]$Status)
    if ($Status -eq 'Success') { return $null }
    $logs = $null
    try { $logs = $Obj.Logger.GetLog().UpdatedRecords } catch {}
    if (-not $logs) { try { $logs = $Obj.Log } catch {} }
    if (-not $logs) { return $null }
    $order = if ($Status -eq 'Warning') { @('EWarning','Warning','EFailed','Failed') } else { @('EFailed','Failed','EWarning','Warning') }
    foreach ($st in $order) {
        $entries = $logs | Where-Object { "$($_.Status)" -eq $st }
        if (-not $entries) { continue }
        $rec = $entries | Where-Object { $_.Title -match 'Error:|Warning:' } | Select-Object -First 1
        if (-not $rec) { $rec = $entries | Select-Object -First 1 }
        if ($rec -and $rec.Title) {
            $msg = $rec.Title
            if ($msg -match '(?:Error|Warning):\s*(.+)$') { $msg = $Matches[1].Trim() }
            return $msg
        }
    }
    return $null
}

# =============================== PREPARACAO =================================
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
try {
    if (-not (Get-Module -Name Veeam.Backup.PowerShell)) {
        Import-Module Veeam.Backup.PowerShell -ErrorAction Stop
    }
} catch { Write-Error "Falha ao importar modulo Veeam. $($_.Exception.Message)"; return }
try {
    if (-not (Get-VBRServerSession -ErrorAction SilentlyContinue)) {
        Write-Host "Conectando em $VbrServer ..." -ForegroundColor Cyan
        Connect-VBRServer -Server $VbrServer
    }
} catch { Write-Error "Falha ao conectar no VBR. $($_.Exception.Message)"; return }

# =============================== COLETA ====================================
$collectedAt = Format-IsoDate (Get-Date)
$since = (Get-Date).AddHours(-$Hours)
Write-Host "Coletando sessoes de BACKUP das ultimas $Hours h..." -ForegroundColor Cyan
$t0 = Get-Date
try {
    $sessions = Get-VBRSession -Type Backup | Where-Object {
        ($_.CreationTime -ge $since -or $_.EndTime -ge $since) -and
        ($Results -contains $_.Result.ToString())
    }
} catch { Write-Error "Erro na coleta das sessoes. $($_.Exception.Message)"; return }
if (-not $sessions) {
    Write-Host "Nenhuma sessao de backup [$($Results -join ', ')] nas ultimas $Hours h." -ForegroundColor Yellow
    return
}
$sessCount = @($sessions).Count
Write-Host ("Sessoes encontradas: {0}  (em {1:N0}s)" -f $sessCount, ((Get-Date)-$t0).TotalSeconds) -ForegroundColor Cyan
if ($EnrichWithVmDetails) {
    Write-Host "Enriquecendo por VM (task sessions). Pode demorar em janelas grandes..." -ForegroundColor Yellow
}

# =========================== MONTAGEM DOS REGISTROS =========================
# Coleta no padrao @( foreach {...} ) -> array garantido (sem List/@(List), que
# dispara "Argument types do not match" no PowerShell 5.1).
$i = 0
$records = @( foreach ($s in $sessions) {
    $i++
    if ($i % 200 -eq 0) { Write-Host ("  ... {0}/{1} sessoes" -f $i, $sessCount) -ForegroundColor DarkGray }

    $result = $s.Result.ToString()
    $sid    = Get-ObjectPropertyValue -Object $s -Names @("Id","Uid","SessionId")
    $jid    = Get-ObjectPropertyValue -Object $s -Names @("JobId","JobUid","Job.Id")
    $name   = Get-ObjectPropertyValue -Object $s -Names @("Name","JobName")
    $start  = $s.CreationTime
    $end    = $s.EndTime
    $sessDur = $null
    if ($start -and $end -and ([datetime]$end).Year -gt 1900) {
        try { $sessDur = [int][math]::Round(([datetime]$end - [datetime]$start).TotalSeconds) } catch {}
    }

    # --- modo SEM enriquecimento: 1 linha por sessao (so duracao/result) ---
    if (-not $EnrichWithVmDetails) {
        [ordered]@{
            job_session_id = if ($sid) { "$sid" } else { $null }
            task_session_id= $null
            job_id         = if ($jid) { "$jid" } else { $null }
            job_name       = if ($name){ "$name" } else { $null }
            vm_name        = $null
            result         = $result
            started_at     = Format-IsoDate $start
            ended_at       = Format-IsoDate $end
            duration_seconds = $sessDur
            processed_gb=$null; read_gb=$null; transferred_gb=$null; avg_speed_mbps=$null
            bottleneck=$null; bn_source=$null; bn_proxy=$null; bn_network=$null; bn_target=$null
            backup_type=$null
            reason_raw     = Get-ReasonFromLog -Obj $s -Status $result
            collected_at=$collectedAt; source=$SourceLabel; vbr_server=$VbrServer
            computer_name=$env:COMPUTERNAME; script_version=$ScriptVersion
            window_hours=$Hours; source_type=$SourceType
        }
        continue
    }

    # --- modo COM enriquecimento: 1 linha por VM (task session) ---
    $tasks = @()
    try { $tasks = @(Get-VBRTaskSession -Session $s) } catch {}
    if ($tasks.Count -eq 0) {
        # sem task sessions -> grava a sessao "magra" mesmo
        [ordered]@{
            job_session_id=if($sid){"$sid"}else{$null}; task_session_id=$null
            job_id=if($jid){"$jid"}else{$null}; job_name=if($name){"$name"}else{$null}; vm_name=$null
            result=$result; started_at=Format-IsoDate $start; ended_at=Format-IsoDate $end
            duration_seconds=$sessDur
            processed_gb=$null; read_gb=$null; transferred_gb=$null; avg_speed_mbps=$null
            bottleneck=$null; bn_source=$null; bn_proxy=$null; bn_network=$null; bn_target=$null
            backup_type=$null; reason_raw=Get-ReasonFromLog -Obj $s -Status $result
            collected_at=$collectedAt; source=$SourceLabel; vbr_server=$VbrServer
            computer_name=$env:COMPUTERNAME; script_version=$ScriptVersion
            window_hours=$Hours; source_type=$SourceType
        }
        continue
    }

    foreach ($t in $tasks) {
        $tp     = $t.Progress
        $vmStat = "$($t.Status)"
        $tStart = Get-ObjectPropertyValue -Object $tp -Names @("StartTimeLocal")
        $tStop  = Get-ObjectPropertyValue -Object $tp -Names @("StopTimeLocal")
        $tDur   = $null
        $dProp  = Get-ObjectPropertyValue -Object $tp -Names @("Duration")
        if ($dProp -is [TimeSpan]) { $tDur = [int][math]::Round($dProp.TotalSeconds) }
        if (($null -eq $tDur) -and $tStart -and $tStop) {
            try { $tDur = [int][math]::Round(([datetime]$tStop - [datetime]$tStart).TotalSeconds) } catch {}
        }

        $bn = $null; try { $bn = $tp.BottleneckInfo } catch {}
        $bnName = if ($bn) { "$($bn.Bottleneck)" } else { $null }
        if ($bnName -eq 'NotDefined') { $bnName = $null }

        $isFull = Get-ObjectPropertyValue -Object $t -Names @("IsFullMode","Info.IsFullMode")
        $btype  = if ($null -ne $isFull) { if ([bool]$isFull) { "Full" } else { "Incremental" } } else { $null }

        [ordered]@{
            job_session_id   = if ($sid) { "$sid" } else { $null }
            task_session_id  = "$($t.Id)"
            job_id           = if ($jid) { "$jid" } else { $null }
            job_name         = if ($name){ "$name" } else { $null }
            vm_name          = "$($t.Name)"
            result           = $vmStat
            started_at       = Format-IsoDate $tStart
            ended_at         = Format-IsoDate $tStop
            duration_seconds = $tDur
            # --- performance (por VM) ---
            processed_gb     = To-GB   (Get-ObjectPropertyValue -Object $tp -Names @("ProcessedUsedSize","ProcessedSize"))
            read_gb          = To-GB   (Get-ObjectPropertyValue -Object $tp -Names @("ReadSize"))
            transferred_gb   = To-GB   (Get-ObjectPropertyValue -Object $tp -Names @("TransferedSize"))
            avg_speed_mbps   = To-MBps (Get-ObjectPropertyValue -Object $tp -Names @("AvgSpeed"))
            # --- gargalo ---
            bottleneck       = $bnName
            bn_source        = if ($bn) { [int]$bn.Source }  else { $null }
            bn_proxy         = if ($bn) { [int]$bn.Proxy }   else { $null }
            bn_network       = if ($bn) { [int]$bn.Network } else { $null }
            bn_target        = if ($bn) { [int]$bn.Target }  else { $null }
            backup_type      = $btype
            # --- motivo (por VM) ---
            reason_raw       = Get-ReasonFromLog -Obj $t -Status $vmStat
            # --- metadados ---
            collected_at=$collectedAt; source=$SourceLabel; vbr_server=$VbrServer
            computer_name=$env:COMPUTERNAME; script_version=$ScriptVersion
            window_hours=$Hours; source_type=$SourceType
        }
    }
} )

# =============================== EXPORTACAO ================================
$stamp      = (Get-Date).ToString("yyyyMMdd_HHmmss")
$ndjsonPath = Join-Path $OutputDir "backups_$stamp.ndjson"
$csvPath    = Join-Path $OutputDir "backups_$stamp.csv"
foreach ($r in $records) { try { $r['collection_type'] = 'veeam_backup_performance' } catch {} }
$lines = foreach ($r in $records) { $r | ConvertTo-Json -Depth 4 -Compress }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($ndjsonPath, [string[]]@($lines), $utf8NoBom)
if ($ExportCsv) {
    $records | ForEach-Object { [pscustomobject]$_ } | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
}

# ================================ RESUMO ===================================
$total = $records.Count
$cSucc = @($records | Where-Object { $_.result -eq 'Success' }).Count
$cWarn = @($records | Where-Object { $_.result -eq 'Warning' }).Count
$cFail = @($records | Where-Object { $_.result -eq 'Failed'  }).Count
Write-Host "`n== BACKUPS coletados nas ultimas $Hours h ==" -ForegroundColor Green
Write-Host ("Linhas (VM-execucoes): {0}  |  Success: {1}  Warning: {2}  Failed: {3}" -f $total,$cSucc,$cWarn,$cFail)

Write-Host "`n== Top $TopSlow mais LENTOS (por duracao) ==" -ForegroundColor Cyan
$records |
    Where-Object { $_.duration_seconds -ne $null } |
    Sort-Object { [int]$_.duration_seconds } -Descending |
    Select-Object -First $TopSlow |
    ForEach-Object {
        $h = [int][math]::Floor($_.duration_seconds / 3600)
        $m = [int][math]::Floor(($_.duration_seconds % 3600) / 60)
        $nm  = if ($_.vm_name) { "$($_.job_name) / $($_.vm_name)" } else { $_.job_name }
        $sz  = if ($_.transferred_gb -ne $null) { "$($_.transferred_gb) GB" } else { "-" }
        $spd = if ($_.avg_speed_mbps -ne $null) { "$($_.avg_speed_mbps) MB/s" } else { "-" }
        $bn  = if ($_.bottleneck) { $_.bottleneck } else { "-" }
        Write-Host ("  {0,2}h{1:00}m | {2,-50} | {3,-9} | {4,-10} | gargalo: {5}" -f $h,$m,$nm.Substring(0,[math]::Min(50,$nm.Length)),$sz,$spd,$bn)
    }

if ($EnrichWithVmDetails) {
    Write-Host "`n== Distribuicao de GARGALO ==" -ForegroundColor Cyan
    $records | Where-Object { $_.bottleneck } |
        Group-Object { $_.bottleneck } | Sort-Object Count -Descending |
        ForEach-Object { Write-Host ("  [{0,5}] {1}" -f $_.Count, $_.Name) }
}

Write-Host "`n== Falhas/avisos por motivo bruto ==" -ForegroundColor Cyan
$records | Where-Object { ($_.result -eq 'Failed' -or $_.result -eq 'Warning') -and $_.reason_raw } |
    Group-Object { $_.reason_raw } | Sort-Object Count -Descending |
    ForEach-Object { Write-Host ("  [{0}] {1}" -f $_.Count, $_.Name) }

Write-Host "`nNDJSON: $ndjsonPath" -ForegroundColor Green
if ($ExportCsv) { Write-Host "CSV:    $csvPath" -ForegroundColor Green }
