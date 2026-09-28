<#
=============================================================================
 collect_repo_capacity.ps1  -  Capacidade dos repositorios: PERFORMANCE x CAPACITY
-----------------------------------------------------------------------------
 Responde:
   - Quanto de espaco TOTAL / LIVRE / USADO cada extent do PERFORMANCE tier tem
     (o disco local finito - onde mora o seu "2TB livre")
   - Quanto o CAPACITY tier (object storage) esta consumindo
   - Separa bem as duas camadas -> ancora de calibracao p/ o consumo em disco
     e base do "time-to-full"

 Saidas:
   1) NDJSON (uma linha por extent/repo) -> pronto p/ ingestao
   2) Resumo no console: PERFORMANCE (com livre) x CAPACITY (usado)
   3) _discovery: membros do SOBR / extent / container -> confirma os campos
      de espaco nesta versao do Veeam

 SOMENTE LEITURA. Nao altera nada. Requer PowerShell 7 (modulo Veeam).
 Uso: pwsh -File .\collect_repo_capacity.ps1
=============================================================================
#>
param(
    [string]$VbrServer = "localhost",
    [string]$OutputDir = "C:\temp\veeam-disk-usage"
)

$ErrorActionPreference = "Stop"

function First-NonNull { param($o,[string[]]$names)
    foreach($n in $names){ try{ $p=$o.PSObject.Properties[$n]; if($p -and $null -ne $p.Value -and "$($p.Value)" -ne ""){ return $p.Value } }catch{} }
    return $null
}

# tenta ler total/livre de um repositorio tentando varios caminhos/campos
function Get-Space { param($repo)
    $total=$null; $free=$null
    # 1) via container (comum em v11/v12)
    try {
        $c = $repo.GetContainer()
        $total = First-NonNull $c @('CachedTotalSpace','TotalSpace','CachedTotalSpaceBytes')
        $free  = First-NonNull $c @('CachedFreeSpace','FreeSpace','CachedFreeSpaceBytes')
    } catch {}
    # 2) via .Info
    if ($null -eq $total) { try { $total = First-NonNull $repo.Info @('CachedTotalSpace','TotalSpace') } catch {} }
    if ($null -eq $free)  { try { $free  = First-NonNull $repo.Info @('CachedFreeSpace','FreeSpace') } catch {} }
    # 3) direto no repo
    if ($null -eq $total) { $total = First-NonNull $repo @('CachedTotalSpace','TotalSpace') }
    if ($null -eq $free)  { $free  = First-NonNull $repo @('CachedFreeSpace','FreeSpace') }
    return @{ total = $total; free = $free }
}

# ---- conexao --------------------------------------------------------------
try { Import-Module Veeam.Backup.PowerShell -ErrorAction Stop *> $null } catch { Write-Host "Falha ao importar modulo Veeam (use pwsh / PS7): $($_.Exception.Message)" -ForegroundColor Red; return }
try { Connect-VBRServer -Server $VbrServer -ErrorAction Stop | Out-Null } catch { if("$($_.Exception.Message)" -notmatch 'already'){ Write-Host "Falha ao conectar: $($_.Exception.Message)" -ForegroundColor Red; return } }

if (-not (Test-Path -LiteralPath $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$discoveryPath = Join-Path $OutputDir ("_repo_discovery_{0}.txt" -f $stamp)
$rows = New-Object System.Collections.Generic.List[object]
$discoveryDone = $false

function Add-Row { param($tier,$sobr,$name,$type,$space,$path)
    $rows.Add([pscustomobject]@{
        collection_type = "veeam_repo_capacity"
        tier          = $tier                      # Performance | Capacity | Archive | Standalone
        sobr_name     = $sobr
        name          = $name
        repo_type     = "$type"
        total_bytes   = if ($space.total) { [int64]$space.total } else { $null }
        free_bytes    = if ($space.free)  { [int64]$space.free }  else { $null }
        used_bytes    = if ($space.total -and $space.free) { [int64]$space.total - [int64]$space.free } else { $null }
        path          = "$path"
    })
}

# ---- SOBRs: performance extents + capacity/archive tier -------------------
Write-Host "Coletando Scale-Out Backup Repositories..." -ForegroundColor Cyan
$sobrs = @()
try { $sobrs = @(Get-VBRBackupRepository -ScaleOut) } catch { Write-Host "  (sem SOBR ou cmdlet indisponivel)" -ForegroundColor DarkGray }
foreach ($sobr in $sobrs) {
    $sname = "$($sobr.Name)"
    Write-Host ("  SOBR: {0}" -f $sname) -ForegroundColor White

    # performance tier extents
    $extents = @()
    try { $extents = @(Get-VBRRepositoryExtent -Repository $sobr) } catch {}
    foreach ($ext in $extents) {
        $er = $null; try { $er = $ext.Repository } catch {}
        $ename = "$(First-NonNull $ext @('Name'))"; if (-not $ename -and $er) { $ename = "$($er.Name)" }
        $etype = if ($er) { First-NonNull $er @('Type','TypeDisplay') } else { $null }
        $epath = if ($er) { First-NonNull $er @('FriendlyPath','Path') } else { $null }
        $space = if ($er) { Get-Space $er } else { @{ total=$null; free=$null } }
        Add-Row "Performance" $sname $ename $etype $space $epath

        if (-not $discoveryDone -and $er) {
            "===== EXTENT.Repository (Get-Member) =====" | Out-File $discoveryPath -Encoding UTF8
            ($er | Get-Member | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8
            "===== EXTENT.Repository.GetContainer() (Format-List) =====" | Out-File $discoveryPath -Append -Encoding UTF8
            try { ($er.GetContainer() | Format-List * | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8 } catch { "GetContainer indisponivel" | Out-File $discoveryPath -Append -Encoding UTF8 }
            "===== EXTENT.Repository.Info (Format-List) =====" | Out-File $discoveryPath -Append -Encoding UTF8
            try { ($er.Info | Format-List * | Out-String) | Out-File $discoveryPath -Append -Encoding UTF8 } catch {}
            $discoveryDone = $true
        }
    }

    # capacity tier (object storage) + archive tier
    foreach ($prop in @('CapacityExtent','CapacityTier','ArchiveExtent')) {
        $ce = $null; try { $ce = $sobr.$prop } catch {}
        if (-not $ce) { continue }
        $cr = $null; try { $cr = $ce.Repository } catch {}
        if (-not $cr) { $cr = $ce }
        $tierName = if ($prop -like 'Archive*') { "Archive" } else { "Capacity" }
        $space = Get-Space $cr
        Add-Row $tierName $sname "$($cr.Name)" (First-NonNull $cr @('Type','TypeDisplay')) $space (First-NonNull $cr @('FriendlyPath','Path'))
    }
}

# ---- Repositorios simples (nao-SOBR) --------------------------------------
Write-Host "Coletando repositorios simples..." -ForegroundColor Cyan
$simple = @()
try { $simple = @(Get-VBRBackupRepository) } catch {}
foreach ($r in $simple) {
    $space = Get-Space $r
    Add-Row "Standalone" "" "$($r.Name)" (First-NonNull $r @('Type','TypeDisplay')) $space (First-NonNull $r @('FriendlyPath','Path'))
}

# ---- NDJSON ---------------------------------------------------------------
$ndjson = Join-Path $OutputDir ("repo_capacity_{0}.ndjson" -f $stamp)
$sw = [System.IO.StreamWriter]::new($ndjson, $false, [System.Text.Encoding]::UTF8)
foreach ($r in $rows) { $sw.WriteLine(($r | ConvertTo-Json -Compress -Depth 4)) }
$sw.Close()

# ---- RESUMO: PERFORMANCE (com livre) x CAPACITY (usado) -------------------
function Show-Tier { param($tier)
    $grp = @($rows | Where-Object { $_.tier -eq $tier })
    if ($grp.Count -eq 0) { return }
    Write-Host ("---- {0} TIER ----" -f $tier.ToUpper()) -ForegroundColor White
    $tt=0.0; $tf=0.0; $tu=0.0
    foreach ($r in ($grp | Sort-Object name)) {
        $tot = if ($r.total_bytes) { [math]::Round($r.total_bytes/1TB,2) } else { 0 }
        $fre = if ($r.free_bytes)  { [math]::Round($r.free_bytes/1TB,2) }  else { 0 }
        $usd = if ($r.used_bytes)  { [math]::Round($r.used_bytes/1TB,2) }  else { 0 }
        $pct = if ($r.total_bytes) { [math]::Round(100.0*$r.used_bytes/$r.total_bytes,0) } else { 0 }
        Write-Host ("  {0,-26} total:{1,7} TB  livre:{2,7} TB  usado:{3,7} TB  ({4}%)" -f $r.name, $tot, $fre, $usd, $pct)
        if ($r.total_bytes) { $tt += $r.total_bytes/1TB }
        if ($r.free_bytes)  { $tf += $r.free_bytes/1TB }
        if ($r.used_bytes)  { $tu += $r.used_bytes/1TB }
    }
    Write-Host ("  {0,-26} total:{1,7} TB  livre:{2,7} TB  usado:{3,7} TB" -f "== TOTAL", [math]::Round($tt,2), [math]::Round($tf,2), [math]::Round($tu,2)) -ForegroundColor Green
}

Write-Host ""
Write-Host "==================== CAPACIDADE DOS REPOSITORIOS ====================" -ForegroundColor Yellow
Show-Tier "Performance"
Show-Tier "Capacity"
Show-Tier "Archive"
Show-Tier "Standalone"

$semEspaco = @($rows | Where-Object { $null -eq $_.total_bytes -and $_.tier -eq 'Performance' }).Count
if ($semEspaco -gt 0) {
    Write-Host ("  ATENCAO: {0} extent(s) de performance sem leitura de espaco - veja o _discovery p/ o campo certo." -f $semEspaco) -ForegroundColor Red
}

Write-Host ""
Write-Host ("NDJSON:    {0}" -f $ndjson) -ForegroundColor Cyan
Write-Host ("Discovery: {0}" -f $discoveryPath) -ForegroundColor Cyan
Write-Host ("Linhas (extent/repo): {0}" -f $rows.Count) -ForegroundColor Cyan
Write-Host "Concluido (somente leitura)." -ForegroundColor Green
