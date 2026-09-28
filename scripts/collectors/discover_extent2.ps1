<#
=============================================================================
 discover_extent2.ps1  -  FULLS da mesma VM espalhados entre extents
-----------------------------------------------------------------------------
 O problema real: um FULL cai no SRV-EXEMPLO, o full seguinte no SRV-EXEMPLO -> sem
 block cloning entre eles (o full novo grava cheio). Este probe varre SO os
 FULLS (rapido), pega o extent de cada full e lista as VMs cujos fulls estao
 em 2+ extents. SOMENTE LEITURA. Cole no ISE e rode (F5). Copie TODA a saida.
=============================================================================
#>
$ErrorActionPreference = "Continue"

function Get-PerfExtents {
    param($rp)
    $perf = New-Object System.Collections.Generic.List[string]
    try {
        foreach ($cr in @($rp.FindChainRepositories())) {
            $crn = "$($cr.Name)"; $crt = "$($cr.Type)"
            if ($crt -match 'S3|Azure|Blob|Amazon|Google|Wasabi|Object|Capacity|Archive') { continue }
            if ($crn -and -not $perf.Contains($crn)) { $perf.Add($crn) }
        }
    } catch {}
    return @($perf | Sort-Object)
}

$backups = @(Get-VBRBackup)
Write-Host ("Backups a varrer: {0}" -f $backups.Count) -ForegroundColor Cyan

# por VM: conjunto de extents onde os FULLS dela estao + contagem por extent
$vmExt = @{}    # vm -> hashset de extents
$vmJob = @{}    # vm -> nome do job (ultimo visto)
$vmFulls = @{}  # vm -> qtd de fulls
$totalFulls = 0

$bi = 0
foreach ($b in $backups) {
    $bi++
    Write-Host ("  [{0}/{1}] {2}" -f $bi, $backups.Count, $b.Name) -ForegroundColor DarkGray
    $rps = @()
    try { $rps = @(Get-VBRRestorePoint -Backup $b) } catch {}
    foreach ($rp in $rps) {
        $isFull = $false
        try { $isFull = [bool]$rp.IsFull } catch {}
        if (-not $isFull) { continue }
        $vm = "$($rp.Name)"
        if (-not $vm) { continue }
        $totalFulls++
        $exts = Get-PerfExtents $rp
        if ($exts.Count -eq 0) { continue }
        if (-not $vmExt.ContainsKey($vm)) { $vmExt[$vm] = New-Object System.Collections.Generic.HashSet[string] }
        foreach ($e in $exts) { [void]$vmExt[$vm].Add($e) }
        $vmJob[$vm] = "$($b.JobName)"
        if (-not $vmFulls.ContainsKey($vm)) { $vmFulls[$vm] = 0 }
        $vmFulls[$vm] = $vmFulls[$vm] + 1
    }
}

Write-Host ""
Write-Host ("=== RESUMO ===") -ForegroundColor Cyan
Write-Host ("Fulls lidos: {0}   VMs com full em disco: {1}" -f $totalFulls, $vmExt.Count)

$scattered = @()
foreach ($vm in $vmExt.Keys) {
    if ($vmExt[$vm].Count -ge 2) {
        $scattered += [pscustomobject]@{
            VM = $vm
            Extents = (@($vmExt[$vm]) -join ', ')
            NExt = $vmExt[$vm].Count
            Fulls = $vmFulls[$vm]
            Job = $vmJob[$vm]
        }
    }
}
Write-Host ("VMs com FULLS em 2+ extents: {0}" -f $scattered.Count) -ForegroundColor Yellow
Write-Host ""
Write-Host ("=== VMs com fulls espalhados (extent do block cloning perdido) ===") -ForegroundColor Cyan
$scattered = $scattered | Sort-Object NExt, VM -Descending
foreach ($s in ($scattered | Select-Object -First 60)) {
    Write-Host ("  {0,-24} | {1} extents [{2}] | {3} fulls | {4}" -f $s.VM, $s.NExt, $s.Extents, $s.Fulls, $s.Job)
}

Write-Host "`n=== FIM. Copie tudo e mande de volta. ===" -ForegroundColor Cyan
