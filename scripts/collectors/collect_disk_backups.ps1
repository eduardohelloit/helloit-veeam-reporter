<#
=============================================================================
 collect_disk_backups.ps1  -  Uso de DISCO por VM + vencimento de IMUTABILIDADE
-----------------------------------------------------------------------------
 Responde (por repositorio / job / VM):
   - Quanto de espaco em DISCO cada servidor (VM) consome  (Stats.BackupSize)
   - QUANDO a imutabilidade de cada arquivo/ponto vence
   - QUANTO espaco vai liberar por faixa de vencimento (ja expirado / ate 7 /
     ate 30 / ate 60 / ate 90 dias)  = planejar o "move to capacity tier"

 Saidas:
   1) NDJSON  (uma linha por ponto de restauracao)  -> pronto p/ ingestao futura
   2) Resumo no console: por job/VM (GB) + por repositorio (liberavel por faixa)
   3) Arquivo _discovery: membros de 1 restore point + 1 storage e QUALQUER
      propriedade cujo nome contenha "immut" -> confirma o campo na sua versao

 SOMENTE LEITURA. Nao remove/move/altera nada (nenhum Remove-/Move-/Set-).
 Requer PowerShell 7 (modulo Veeam exige pwsh). Rodar no servidor VBR.

 Uso:
   pwsh -File .\collect_disk_backups.ps1
   pwsh -File .\collect_disk_backups.ps1 -JobFilter "TMS - SERVER ADM"
=============================================================================
#>
param(
    [string]$VbrServer = "localhost",
    [string]$JobFilter = "",                                   # filtra por nome do job (contains); vazio = todos
    [string]$OutputDir = "C:\temp\veeam-disk-usage",
    [switch]$WithExtent                                        # detalhe extent/tier por ponto (mais lento; ver nota)
)

$ErrorActionPreference = "Stop"

function First-NonNull { param($o,[string[]]$names)
    foreach($n in $names){ try{ $p=$o.PSObject.Properties[$n]; if($p -and $null -ne $p.Value -and "$($p.Value)" -ne ""){ return $p.Value } }catch{} }
    return $null
}

# converte o wrapper Veeam.DateTimeUtc (ou string/datetime) em [datetime] ou $null
function To-DateOrNull { param($v)
    if ($null -eq $v) { return $null }
    try { $p = $v.PSObject.Properties['Value']; if ($p) { $v = $p.Value } } catch {}
    if ($v -is [datetime]) { return $v }
    $dt = [datetime]::MinValue
    if ([datetime]::TryParse("$v", [ref]$dt)) { return $dt }
    return $null
}

# ---- conexao --------------------------------------------------------------
try { Import-Module Veeam.Backup.PowerShell -ErrorAction Stop *> $null } catch { Write-Host "Falha ao importar modulo Veeam (use pwsh / PS7): $($_.Exception.Message)" -ForegroundColor Red; return }
try { Connect-VBRServer -Server $VbrServer -ErrorAction Stop | Out-Null } catch { if("$($_.Exception.Message)" -notmatch 'already'){ Write-Host "Falha ao conectar: $($_.Exception.Message)" -ForegroundColor Red; return } }

if (-not (Test-Path -LiteralPath $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$now   = Get-Date

Write-Host "Coletando backups (Get-VBRBackup)..." -ForegroundColor Cyan
$backups = @(Get-VBRBackup)
if ($JobFilter) { $backups = @($backups | Where-Object { "$($_.JobName)" -like "*$JobFilter*" -or "$($_.Name)" -like "*$JobFilter*" }) }
Write-Host ("  {0} backup(s) a inspecionar." -f $backups.Count) -ForegroundColor DarkGray

$rows = New-Object System.Collections.Generic.List[object]
$discoveryDone = $false
$discoveryPath = Join-Path $OutputDir ("_discovery_{0}.txt" -f $stamp)

$bi = 0
foreach ($b in $backups) {
    $bi++
    Write-Host ("  [{0}/{1}] {2}" -f $bi, $backups.Count, $b.Name) -ForegroundColor DarkGray
    $jobName = "$(First-NonNull $b @('JobName'))"
    $repoName = $null
    try { $repoName = "$(First-NonNull ($b.GetRepository()) @('Name'))" } catch {}

    Write-Host "      buscando pontos (Get-VBRRestorePoint)..." -ForegroundColor DarkGray
    $rps = @()
    try { $rps = @(Get-VBRRestorePoint -Backup $b) }
    catch { Write-Host ("      Get-VBRRestorePoint FALHOU: {0}" -f $_.Exception.Message) -ForegroundColor Red }
    Write-Host ("      {0} pontos a ler..." -f $rps.Count) -ForegroundColor DarkGray

    $pi = 0
    $sw2 = [System.Diagnostics.Stopwatch]::StartNew()
    foreach ($rp in $rps) {
        $pi++
        if ($pi % 1000 -eq 0) {
            Write-Host ("        {0}/{1} pontos ({2:n0}/s)" -f $pi, $rps.Count, ($pi / [math]::Max(1,$sw2.Elapsed.TotalSeconds))) -ForegroundColor DarkGray
        }
        $vm = "$(First-NonNull $rp @('Name','VmName'))"
        $ct = First-NonNull $rp @('CreationTime','CreationTimeUtc')

        # storage (arquivo em disco do ponto): tamanho, taxas e imutabilidade
        $storage = $null
        try { $storage = $rp.GetStorage() } catch {}
        $sizeBytes = $null; $dataSize = $null; $dedup = $null; $compr = $null
        try { $sizeBytes = [int64]$storage.Stats.BackupSize }     catch {}
        try { $dataSize  = [int64]$storage.Stats.DataSize }        catch {}
        try { $dedup     = [double]$storage.Stats.DedupRatio }     catch {}
        try { $compr     = [double]$storage.Stats.CompressRatio }  catch {}

        # ID unico do ponto -> deduplicar (o mesmo ponto aparece em 2 backups)
        $rpId = "$(First-NonNull $rp @('Id'))"

        $isFull = $null
        try { $isFull = [bool]$rp.IsFull } catch {}
        $tipo = First-NonNull $rp @('Type','Algorithm')

        # imutabilidade (campos confirmados na v12): ImmutableTillUtc + IsImmutable
        $immUntil = $null
        try { $immUntil = To-DateOrNull $storage.ImmutableTillUtc } catch {}
        $immFlag = $null
        try { $immFlag = [bool]$storage.IsImmutable } catch {}

        # EXTENT(S) de performance DESTE ponto/cadeia. Na v12:
        #   - [CStorage].GetRepository()  NAO EXISTE (retorna null);
        #   - $rp.GetRepository()          devolve o SOBR, nao o extent;
        #   - $rp.FindChainRepositories()  devolve os EXTENTS onde a cadeia deste ponto
        #                                  tem arquivos  <-- e o que usamos.
        # IMPORTANTE: resolvemos POR PONTO, SEM cache por VM. A mesma VM pode ter um
        # FULL no SRV-EXEMPLO e outro no SRV-EXEMPLO (quando um extent enche, o full novo
        # cai noutro extent -> sem block cloning). Cachear por VM colapsaria justamente
        # esse caso. Na analise, agrupa-se por VM a UNIAO dos extents dos pontos: 2+ =
        # fulls espalhados / clone perdido. So extents de PERFORMANCE (object storage
        # nao faz clone). NUNCA usamos FindCapacityTierOib (travava a coleta).
        $extent = $null; $tier = "n/d"; $inCap = $false
        $perf = New-Object System.Collections.Generic.List[string]
        try {
            foreach ($cr in @($rp.FindChainRepositories())) {
                $crn = "$($cr.Name)"; $crt = "$($cr.Type)"
                if ($crt -match 'S3|Azure|Blob|Amazon|Google|Wasabi|Object|Capacity|Archive') { continue }
                if ($crn -and -not $perf.Contains($crn)) { $perf.Add($crn) }
            }
        } catch {}
        $extent = (@($perf | Sort-Object) -join ', ')
        if ($extent) { $tier = "Performance" }

        # GFS (ponto de retencao estendida): GfsPeriod (None|Weekly|Monthly|Yearly)
        $gfs = $null
        try { $gp = "$($storage.GfsPeriod)"; if ($gp -and $gp -ne 'None') { $gfs = $gp } } catch {}

        # ---- DISCOVERY: uma vez, despeja membros p/ confirmar os campos ------
        if (-not $discoveryDone -and $storage) {
            "===== RESTORE POINT (Get-Member) =====" | Out-File $discoveryPath -Encoding UTF8
            ($rp | Get-Member | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8
            "===== STORAGE (Get-Member) =====" | Out-File $discoveryPath -Append -Encoding UTF8
            ($storage | Get-Member | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8
            "===== PROPRIEDADES COM 'immut' (rp + storage) =====" | Out-File $discoveryPath -Append -Encoding UTF8
            foreach ($src in @($rp,$storage)) {
                try { foreach ($p in $src.PSObject.Properties) { if ($p.Name -match 'immut') {
                    $val = $null; try { $val = $p.Value } catch { $val = '(erro)' }
                    ("  {0} = {1}" -f $p.Name, $val) | Out-File $discoveryPath -Append -Encoding UTF8
                } } } catch {}
            }
            "===== Stats (storage) =====" | Out-File $discoveryPath -Append -Encoding UTF8
            try { ($storage.Stats | Format-List * | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8 } catch {}
            $discoveryDone = $true
        }

        $rows.Add([pscustomobject]@{
            collection_type   = "veeam_disk_backups"
            job_name          = $jobName
            backup_name       = "$($b.Name)"
            rp_id             = $rpId
            vm_name           = $vm
            repository        = $repoName
            extent_name       = $extent
            tier              = $tier
            restore_point     = if ($ct) { ([datetime]$ct).ToString('o') } else { $null }
            is_full           = $isFull
            type              = "$tipo"
            gfs_period        = $gfs
            size_bytes        = $sizeBytes
            data_size_bytes   = $dataSize
            dedup_ratio       = $dedup
            compress_ratio    = $compr
            is_immutable      = $immFlag
            immutable_until   = if ($immUntil) { ([datetime]$immUntil).ToString('o') } else { $null }
            in_capacity_tier  = $inCap
        })
    }
}

# ---- NDJSON (uma linha por ponto) -----------------------------------------
$ndjson = Join-Path $OutputDir ("disk_backups_{0}.ndjson" -f $stamp)
$sw = [System.IO.StreamWriter]::new($ndjson, $false, [System.Text.Encoding]::UTF8)
foreach ($r in $rows) { $sw.WriteLine(($r | ConvertTo-Json -Compress -Depth 4)) }
$sw.Close()

# ---- DEDUP: o mesmo ponto (rp_id) aparece em mais de um backup ------------
# Para os RESUMOS, conta cada ponto fisico uma vez (mantem o 1o backup visto).
$uniq = $rows
if (@($rows | Where-Object { $_.rp_id }).Count -gt 0) {
    $uniq = @($rows | Group-Object rp_id | ForEach-Object { $_.Group[0] })
    Write-Host ("Dedup por rp_id: {0} linhas = {1} pontos fisicos." -f $rows.Count, $uniq.Count) -ForegroundColor DarkGray
}

# ---- RESUMO 0: por CAMADA (performance x capacity) -- so com -WithExtent ---
if (@($uniq | Where-Object { $_.tier -and $_.tier -ne 'n/d' }).Count -gt 0) {
    Write-Host ""
    Write-Host "==================== POR CAMADA (tier) ====================" -ForegroundColor Yellow
    foreach ($g in ($uniq | Group-Object tier | Sort-Object Name)) {
        $gb  = [math]::Round((($g.Group | Measure-Object size_bytes -Sum).Sum / 1GB), 1)
        Write-Host ("  {0,-22} {1,7} pts  {2,10} GB (BackupSize logico)" -f $g.Name, $g.Group.Count, $gb)
    }
}

# ---- RESUMO 1: por JOB / VM (GB em disco) ---------------------------------
Write-Host ""
Write-Host "==================== CONSUMO EM DISCO POR JOB / VM ====================" -ForegroundColor Yellow
$byJobVm = $uniq | Group-Object job_name, vm_name
foreach ($g in ($byJobVm | Sort-Object Name)) {
    $gb  = [math]::Round((($g.Group | Measure-Object size_bytes -Sum).Sum / 1GB), 1)
    $pts = $g.Group.Count
    $j = $g.Group[0].job_name; $vm = $g.Group[0].vm_name
    Write-Host ("  {0,-32} | {1,-24} | {2,8} GB | pts:{3}" -f $j, $vm, $gb, $pts)
}

# ---- RESUMO 2: por REPOSITORIO, liberavel por faixa de vencimento ---------
Write-Host ""
Write-Host "============ ESPACO LIBERAVEL POR VENCIMENTO DE IMUTABILIDADE (por repo) ============" -ForegroundColor Yellow
$haveImm = @($uniq | Where-Object { $_.immutable_until }).Count
if ($haveImm -eq 0) {
    Write-Host "  Nenhuma data de imutabilidade lida - confira o arquivo _discovery para o nome do campo nesta versao do Veeam." -ForegroundColor Red
} else {
    foreach ($repoGrp in ($uniq | Where-Object { $_.immutable_until } | Group-Object repository | Sort-Object Name)) {
        $buckets = [ordered]@{ 'expirado'=0.0; 'ate_7d'=0.0; 'ate_30d'=0.0; 'ate_60d'=0.0; 'ate_90d'=0.0; 'mais_90d'=0.0 }
        foreach ($r in $repoGrp.Group) {
            $days = ([datetime]$r.immutable_until - $now).TotalDays
            $gb = [double]$r.size_bytes / 1GB
            if     ($days -le 0)  { $buckets['expirado'] += $gb }
            elseif ($days -le 7)  { $buckets['ate_7d']   += $gb }
            elseif ($days -le 30) { $buckets['ate_30d']  += $gb }
            elseif ($days -le 60) { $buckets['ate_60d']  += $gb }
            elseif ($days -le 90) { $buckets['ate_90d']  += $gb }
            else                  { $buckets['mais_90d'] += $gb }
        }
        Write-Host ("  {0}" -f $repoGrp.Name) -ForegroundColor White
        foreach ($k in $buckets.Keys) {
            Write-Host ("      {0,-9}: {1,8} GB" -f $k, [math]::Round($buckets[$k],1))
        }
    }
}

Write-Host ""
Write-Host ("NDJSON:    {0}" -f $ndjson) -ForegroundColor Cyan
Write-Host ("Discovery: {0}" -f $discoveryPath) -ForegroundColor Cyan
Write-Host "  (envie este arquivo p/ confirmarmos o campo de imutabilidade nesta versao do Veeam)" -ForegroundColor DarkCyan
Write-Host ("Linhas (ponto de restauracao): {0}" -f $rows.Count) -ForegroundColor Cyan
Write-Host "Concluido (somente leitura - nada foi movido/removido)." -ForegroundColor Green
