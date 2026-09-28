<#
============================================================================
 Veeam B&R - Coletor/Exportador de Offload (Capacity Tier)  ->  NDJSON
----------------------------------------------------------------------------
 - Coleta sessoes 'ArchiveBackup' (offload do Capacity Tier) numa janela.
 - Coleta Success / Warning / Failed (configuravel).
 - Extrai o motivo BRUTO (reason_raw) de Failed/Warning a partir de .Log.
 - Exporta NDJSON (principal) + CSV (opcional, conferencia humana).
 - SOMENTE LEITURA. Nao altera nada no Veeam. Nao normaliza/classifica.
 - Compativel com Windows PowerShell 5.1. Rodar no servidor Veeam (ISE).
 Versao: 1.0
============================================================================
#>

# ----- CONFIGURACAO (defaults; pode passar por parametro) -------------------
param(
    [string]  $VbrServer = "localhost",
    [int]     $Hours     = 900,               # ~37 dias (garante o mes inteiro)
    [string[]]$Results   = @("Success", "Warning", "Failed"),
    [string]  $OutputDir = "C:\temp\veeam-offload",
    [bool]    $ExportCsv = $false             # CSV so p/ conferencia; o sistema so usa o NDJSON
)
# ----------------------------------------------------------------------------

$ScriptVersion = "1.0"
$SourceLabel   = "Veeam PowerShell - Get-VBRSession -Type ArchiveBackup"
$SourceType    = "ArchiveBackup"
$ErrorActionPreference = "Stop"

# ============================ FUNCOES AUXILIARES =============================

# Le o primeiro nome de propriedade existente (suporta caminho aninhado "Job.Id")
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

# ISO 8601 com timezone (ex.: 2025-01-15T12:22:25.0000000-03:00)
function Format-IsoDate {
    param($dt)
    if ($null -eq $dt) { return $null }
    try { return ([datetimeoffset]$dt).ToString("o") } catch { return $null }
}

# Extrai o motivo bruto a partir de .Log (sem normalizar)
function Get-OffloadReason {
    param($Session, [string]$Result)
    if ($Result -eq 'Success') { return $null }
    $logs = $Session.Log
    if (-not $logs) { return $null }

    # ordem de prioridade do status conforme o resultado da sessao
    $statusOrder = if ($Result -eq 'Warning') { @('Warning','Failed') } else { @('Failed','Warning') }

    foreach ($st in $statusOrder) {
        $entries = $logs | Where-Object { "$($_.Status)" -eq $st }
        if (-not $entries) { continue }
        # prioriza o registro com detalhe "Error:" ou "Warning:"
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

# OutputDir
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# Modulo Veeam
try {
    if (-not (Get-Module -Name Veeam.Backup.PowerShell)) {
        Import-Module Veeam.Backup.PowerShell -ErrorAction Stop
    }
} catch {
    Write-Error "Falha ao importar o modulo Veeam.Backup.PowerShell. Rode no servidor Veeam. Detalhe: $($_.Exception.Message)"
    return
}

# Conexao VBR
try {
    if (-not (Get-VBRServerSession -ErrorAction SilentlyContinue)) {
        Write-Host "Conectando em $VbrServer ..." -ForegroundColor Cyan
        Connect-VBRServer -Server $VbrServer
    }
} catch {
    Write-Error "Falha ao conectar no VBR ($VbrServer). Detalhe: $($_.Exception.Message)"
    return
}

# =============================== COLETA ====================================

$collectedAt = Format-IsoDate (Get-Date)
$since = (Get-Date).AddHours(-$Hours)

Write-Host "Coletando offloads (ArchiveBackup) das ultimas $Hours h..." -ForegroundColor Cyan
try {
    $sessions = Get-VBRSession -Type ArchiveBackup | Where-Object {
        ($_.CreationTime -ge $since -or $_.EndTime -ge $since) -and
        ($Results -contains $_.Result.ToString())
    }
} catch {
    Write-Error "Erro durante a coleta das sessoes. Detalhe: $($_.Exception.Message)"
    return
}

if (-not $sessions) {
    Write-Host "Nenhuma sessao de offload [$($Results -join ', ')] nas ultimas $Hours h. Nada a exportar." -ForegroundColor Yellow
    return
}

# =========================== MONTAGEM DOS REGISTROS =========================

$records = foreach ($s in $sessions) {
    $result = $s.Result.ToString()

    $sid   = Get-ObjectPropertyValue -Object $s -Names @("Id","Uid","SessionId")
    $jid   = Get-ObjectPropertyValue -Object $s -Names @("JobId","JobUid","JobGuid","Job.Id","Job.Uid","Job.Guid","JobInfo.Id")
    $name  = Get-ObjectPropertyValue -Object $s -Names @("Name","JobName")
    $state = Get-ObjectPropertyValue -Object $s -Names @("State")

    $start = $s.CreationTime
    $end   = $s.EndTime
    $dur   = $null
    if ($start -and $end) {
        try { $dur = [int][math]::Round(([datetime]$end - [datetime]$start).TotalSeconds) } catch {}
    }

    [ordered]@{
        session_id       = if ($sid)  { "$sid" }  else { $null }
        job_id           = if ($jid)  { "$jid" }  else { $null }
        job_name         = if ($name) { "$name" } else { $null }
        result           = $result
        state            = if ($state) { "$state" } else { $null }
        started_at       = Format-IsoDate $start
        ended_at         = Format-IsoDate $end
        duration_seconds = $dur
        reason_raw       = Get-OffloadReason -Session $s -Result $result
        collected_at     = $collectedAt
        source           = $SourceLabel
        vbr_server       = $VbrServer
        computer_name    = $env:COMPUTERNAME
        script_version   = $ScriptVersion
        window_hours     = $Hours
        source_type      = $SourceType
    }
}

$records = @($records)

# =============================== EXPORTACAO ================================

$stamp      = (Get-Date).ToString("yyyyMMdd_HHmmss")
$ndjsonPath = Join-Path $OutputDir "offload_$stamp.ndjson"
$csvPath    = Join-Path $OutputDir "offload_$stamp.csv"

# NDJSON (uma linha por execucao), UTF-8 sem BOM
foreach ($r in $records) { try { $r['collection_type'] = 'veeam_offload' } catch {} }
$lines = foreach ($r in $records) { $r | ConvertTo-Json -Depth 4 -Compress }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($ndjsonPath, [string[]]@($lines), $utf8NoBom)

# CSV opcional (conferencia humana)
if ($ExportCsv) {
    $records | ForEach-Object { [pscustomobject]$_ } |
        Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
}

# ================================ RESUMO ===================================

$total   = $records.Count
$cSucc   = @($records | Where-Object { $_.result -eq 'Success' }).Count
$cWarn   = @($records | Where-Object { $_.result -eq 'Warning' }).Count
$cFail   = @($records | Where-Object { $_.result -eq 'Failed'  }).Count

Write-Host "`n== OFFLOAD coletado nas ultimas $Hours h ==" -ForegroundColor Green
Write-Host ("Total:   {0}" -f $total)
Write-Host ("Success: {0}" -f $cSucc)
Write-Host ("Warning: {0}" -f $cWarn)
Write-Host ("Failed:  {0}" -f $cFail)

Write-Host "`n== Falhas/avisos agrupados por motivo bruto ==" -ForegroundColor Cyan
$records |
    Where-Object { ($_.result -eq 'Failed' -or $_.result -eq 'Warning') -and $_.reason_raw } |
    Group-Object { $_.reason_raw } | Sort-Object Count -Descending |
    ForEach-Object { Write-Host ("  [{0}] {1}" -f $_.Count, $_.Name) }

Write-Host "`nNDJSON exportado para: $ndjsonPath" -ForegroundColor Green
if ($ExportCsv) { Write-Host "CSV exportado para:    $csvPath" -ForegroundColor Green }