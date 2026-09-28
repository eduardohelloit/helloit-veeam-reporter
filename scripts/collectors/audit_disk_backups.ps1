<#
=============================================================================
 audit_disk_backups.ps1  -  Inventario de backups EM DISCO + correlacao de VMs
-----------------------------------------------------------------------------
 Responde:
   - O que existe de backup em disco (por backup/chain e por VM).
   - Mesma VM presente em MAIS DE UM backup (duplicidade em disco).
   - Backups ORFAOS (a rotina/job nao existe mais) -> arquivos que "sobraram".
   - VMs com ponto de restauracao ANTIGO (possivel leftover de rotina removida).

 Ex.: SRV-EXEMPLO tinha um backup de 2025 de uma rotina removida; o arquivo
 ficou em disco. Este script encontra esse tipo de caso.

 SOMENTE LEITURA. Nao remove/move nada (nenhum Remove-/Delete-).
 Requer PowerShell 7 (modulo Veeam exige pwsh). Rodar no servidor VBR.
=============================================================================
#>
param(
    [string]$VbrServer = "localhost",
    [int]$StaleDays    = 60,                                   # ponto mais novo mais antigo que isso = "antigo"
    [string]$OutputDir = "C:\temp\veeam-disk-backup-audit"
)

$ErrorActionPreference = "Stop"

function First-NonNull { param($o,[string[]]$names)
    foreach($n in $names){ try{ $p=$o.PSObject.Properties[$n]; if($p -and $null -ne $p.Value -and "$($p.Value)" -ne ""){ return $p.Value } }catch{} }
    return $null
}

# ---- conexao --------------------------------------------------------------
try { Import-Module Veeam.Backup.PowerShell -ErrorAction Stop *> $null } catch { Write-Host "Falha ao importar modulo Veeam (use pwsh / PS7): $($_.Exception.Message)" -ForegroundColor Red; return }
try { Connect-VBRServer -Server $VbrServer -ErrorAction Stop | Out-Null } catch { if("$($_.Exception.Message)" -notmatch 'already'){ Write-Host "Falha ao conectar: $($_.Exception.Message)" -ForegroundColor Red; return } }

if (-not (Test-Path -LiteralPath $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

Write-Host "Coletando jobs ativos..." -ForegroundColor Cyan
$activeJobIds = @{}
try { foreach($j in (Get-VBRJob)){ $activeJobIds["$($j.Id)"] = $j.Name } } catch {}

Write-Host "Coletando backups em disco (Get-VBRBackup)..." -ForegroundColor Cyan
$backups = @()
try { $backups = @(Get-VBRBackup) } catch { Write-Host "Get-VBRBackup falhou: $($_.Exception.Message)" -ForegroundColor Red; return }
Write-Host ("  {0} backup(s) encontrados. Lendo pontos de restauracao..." -f $backups.Count) -ForegroundColor DarkGray

# ---- monta linhas: uma por (VM, backup) -----------------------------------
$rows = New-Object System.Collections.Generic.List[object]
$i = 0
foreach ($b in $backups) {
    $i++
    Write-Host ("  [{0}/{1}] {2}" -f $i, $backups.Count, $b.Name) -ForegroundColor DarkGray

    $jobId   = "$(First-NonNull $b @('JobId'))"
    $jobName = First-NonNull $b @('JobName')
    $jobExists = $activeJobIds.ContainsKey($jobId)
    $repoName = $null
    try { $repoName = (First-NonNull ($b.GetRepository()) @('Name')) } catch {}
    $btype = "$(First-NonNull $b @('TypeToString','JobType'))"

    $rps = @()
    try { $rps = @(Get-VBRRestorePoint -Backup $b) } catch {}

    # agrupa por VM (nome do objeto)
    $byVm = @{}
    foreach ($rp in $rps) {
        $vm = First-NonNull $rp @('Name','VmName')
        if (-not $vm) { continue }
        $ct = First-NonNull $rp @('CreationTime','CreationTimeUtc')
        if (-not $byVm.ContainsKey($vm)) { $byVm[$vm] = New-Object System.Collections.Generic.List[datetime] }
        if ($ct) { $byVm[$vm].Add([datetime]$ct) }
    }

    foreach ($vm in $byVm.Keys) {
        $dates = @($byVm[$vm] | Sort-Object)
        $oldest = if($dates.Count){ $dates[0] } else { $null }
        $newest = if($dates.Count){ $dates[-1] } else { $null }
        $ageNewest = if($newest){ [int]((New-TimeSpan -Start $newest -End (Get-Date)).TotalDays) } else { $null }
        $rows.Add([pscustomobject]@{
            VM           = $vm
            Backup       = $b.Name
            Repositorio  = $repoName
            Tipo         = $btype
            JobAtivo     = $jobExists
            JobNome      = $jobName
            Pontos       = $dates.Count
            MaisAntigo   = if($oldest){ $oldest.ToString('yyyy-MM-dd') } else { '' }
            MaisNovo     = if($newest){ $newest.ToString('yyyy-MM-dd') } else { '' }
            DiasDesdeUltimo = $ageNewest
        })
    }
}

# ---- exporta CSV completo --------------------------------------------------
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csv = Join-Path $OutputDir ("disk_backups_{0}.csv" -f $stamp)
$rows | Sort-Object VM, MaisNovo | Export-Csv -Path $csv -NoTypeInformation -Encoding UTF8

# ---- 1) VMs em MAIS DE UM backup (duplicidade em disco) -------------------
Write-Host ""
Write-Host "==================== VMs EM MAIS DE UM BACKUP (disco) ====================" -ForegroundColor Yellow
$dups = $rows | Group-Object VM | Where-Object { ($_.Group.Backup | Sort-Object -Unique).Count -gt 1 }
if (-not $dups) { Write-Host "  Nenhuma VM em mais de um backup." -ForegroundColor Green }
else {
    foreach ($g in ($dups | Sort-Object Name)) {
        Write-Host ("  {0}" -f $g.Name) -ForegroundColor White
        foreach ($r in ($g.Group | Sort-Object MaisNovo)) {
            $flag = if (-not $r.JobAtivo) { " [ORFAO: job nao existe]" } elseif ($r.DiasDesdeUltimo -ge $StaleDays) { " [ANTIGO]" } else { "" }
            Write-Host ("      - {0}  | pts:{1} | {2} -> {3} ({4}d){5}" -f $r.Backup, $r.Pontos, $r.MaisAntigo, $r.MaisNovo, $r.DiasDesdeUltimo, $flag)
        }
    }
}

# ---- 2) Backups ORFAOS (job removido) -------------------------------------
Write-Host ""
Write-Host "==================== BACKUPS ORFAOS (rotina/job nao existe mais) ====================" -ForegroundColor Yellow
$orf = $rows | Where-Object { -not $_.JobAtivo } | Group-Object Backup
if (-not $orf) { Write-Host "  Nenhum backup orfao." -ForegroundColor Green }
else {
    foreach ($g in ($orf | Sort-Object Name)) {
        $vms = ($g.Group.VM | Sort-Object -Unique)
        $maxNew = ($g.Group | Sort-Object MaisNovo)[-1].MaisNovo
        Write-Host ("  {0}  | VMs:{1} | ultimo ponto:{2} | repo:{3}" -f $g.Name, $vms.Count, $maxNew, ($g.Group[0].Repositorio))
        Write-Host ("      VMs: {0}" -f ($vms -join ', ')) -ForegroundColor DarkGray
    }
}

# ---- 3) VMs com ponto ANTIGO (possivel leftover) --------------------------
Write-Host ""
Write-Host ("==================== VMs COM ULTIMO PONTO > {0} DIAS (possivel leftover) ====================" -f $StaleDays) -ForegroundColor Yellow
$stale = $rows | Where-Object { $_.DiasDesdeUltimo -ne $null -and $_.DiasDesdeUltimo -ge $StaleDays } | Sort-Object DiasDesdeUltimo -Descending
if (-not $stale) { Write-Host "  Nenhuma VM com ponto antigo." -ForegroundColor Green }
else {
    foreach ($r in $stale) {
        $flag = if (-not $r.JobAtivo) { " [ORFAO]" } else { "" }
        Write-Host ("  {0,-28} | {1,-40} | ultimo:{2} ({3}d){4}" -f $r.VM, $r.Backup, $r.MaisNovo, $r.DiasDesdeUltimo, $flag)
    }
}

Write-Host ""
Write-Host ("Resumo: {0} linhas (VM x backup) | CSV: {1}" -f $rows.Count, $csv) -ForegroundColor Cyan
Write-Host "Concluido (somente leitura - nada foi removido)." -ForegroundColor Green
