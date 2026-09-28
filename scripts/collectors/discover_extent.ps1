<#
=============================================================================
 discover_extent.ps1  -  DESCOBERTA: qual metodo retorna o EXTENT por ponto
-----------------------------------------------------------------------------
 Rapido (1 backup, poucos pontos). SOMENTE LEITURA. Cole no ISE e rode (F5).
 Copie TODA a saida do console e mande de volta.
=============================================================================
#>
$ErrorActionPreference = "Continue"

Write-Host "=== 1) EXTENTS conhecidos do(s) SOBR (nome + caminho) ===" -ForegroundColor Cyan
try {
    $sobrs = @(Get-VBRBackupRepository -ScaleOut)
    foreach ($s in $sobrs) {
        Write-Host ("SOBR: {0}" -f $s.Name) -ForegroundColor Yellow
        try {
            $exts = @(Get-VBRRepositoryExtent -Repository $s)
            foreach ($e in $exts) {
                $en = "$($e.Name)"
                $er = $e.Repository
                $ern = "$($er.Name)"
                $path = ""
                try { $path = "$($er.FriendlyPath)" } catch {}
                if (-not $path) { try { $path = "$($er.Path)" } catch {} }
                Write-Host ("   extent.Name={0} | Repository.Name={1} | path={2}" -f $en, $ern, $path)
            }
        } catch { Write-Host ("   (falha ao listar extents: {0})" -f $_.Exception.Message) -ForegroundColor Red }
    }
} catch { Write-Host ("(sem SOBR ou falha: {0})" -f $_.Exception.Message) -ForegroundColor Red }

Write-Host ""
Write-Host "=== 2) Acha 1 backup com pontos e testa metodos de extent ===" -ForegroundColor Cyan
$backups = @(Get-VBRBackup)
$target = $null; $rps = @()
foreach ($b in $backups) {
    try { $rps = @(Get-VBRRestorePoint -Backup $b) } catch { $rps = @() }
    if ($rps.Count -ge 1) { $target = $b; break }
}
if (-not $target) { Write-Host "Nenhum backup com pontos encontrado." -ForegroundColor Red; return }
Write-Host ("Backup de teste: {0}  ({1} pontos)" -f $target.Name, $rps.Count) -ForegroundColor Yellow

$max = [math]::Min(3, $rps.Count)
for ($i = 0; $i -lt $max; $i++) {
    $rp = $rps[$i]
    Write-Host ("`n--- Ponto {0} : VM={1} ---" -f ($i+1), "$($rp.Name)") -ForegroundColor Green
    $storage = $null
    try { $storage = $rp.GetStorage() } catch { Write-Host ("  GetStorage FALHOU: {0}" -f $_.Exception.Message) -ForegroundColor Red }

    # metodo A: $storage.GetRepository().Name  (o que o coletor usa hoje)
    try { $r = $storage.GetRepository(); Write-Host ("  A) storage.GetRepository().Name = {0}  (Type={1})" -f "$($r.Name)", "$($r.Type)") }
    catch { Write-Host ("  A) storage.GetRepository() FALHOU: {0}" -f $_.Exception.Message) -ForegroundColor Red }

    # metodo B: $rp.GetRepository().Name
    try { $r = $rp.GetRepository(); Write-Host ("  B) rp.GetRepository().Name = {0}" -f "$($r.Name)") }
    catch { Write-Host ("  B) rp.GetRepository() FALHOU: {0}" -f $_.Exception.Message) -ForegroundColor Red }

    # metodo C: $rp.FindChainRepositories()
    try { $rc = @($rp.FindChainRepositories()); Write-Host ("  C) rp.FindChainRepositories() = {0}" -f (($rc | ForEach-Object { "$($_.Name)" }) -join ', ')) }
    catch { Write-Host ("  C) rp.FindChainRepositories() FALHOU: {0}" -f $_.Exception.Message) -ForegroundColor Red }

    # metodo D: caminho do arquivo do storage (p/ mapear por path nos extents)
    foreach ($pn in @('FilePath','PartialPath','FileName','Path')) {
        try { $v = $storage.PSObject.Properties[$pn]; if ($v -and "$($v.Value)") { Write-Host ("  D) storage.{0} = {1}" -f $pn, "$($v.Value)") } } catch {}
    }
}

Write-Host "`n=== 3) Membros do STORAGE com 'Repositor' ou 'Extent' no nome ===" -ForegroundColor Cyan
try {
    $storage = $rps[0].GetStorage()
    $storage | Get-Member | Where-Object { $_.Name -match 'Repositor|Extent|Path' } | ForEach-Object { Write-Host ("  {0}  ({1})" -f $_.Name, $_.MemberType) }
} catch { Write-Host ("(falha: {0})" -f $_.Exception.Message) -ForegroundColor Red }

Write-Host "`n=== 4) Membros do RESTORE POINT com 'Repositor' ou 'Extent' ===" -ForegroundColor Cyan
try {
    $rps[0] | Get-Member | Where-Object { $_.Name -match 'Repositor|Extent' } | ForEach-Object { Write-Host ("  {0}  ({1})" -f $_.Name, $_.MemberType) }
} catch { Write-Host ("(falha: {0})" -f $_.Exception.Message) -ForegroundColor Red }

Write-Host "`n=== FIM. Copie tudo acima e mande de volta. ===" -ForegroundColor Cyan
