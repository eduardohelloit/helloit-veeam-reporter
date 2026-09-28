# =============================================================================
# discover_synthetic_full.ps1  -  DIAGNOSTICO (somente leitura)
#
# Objetivo: descobrir QUAIS propriedades do Veeam 12 guardam o FULL SINTETICO
# (e o ACTIVE FULL): se esta habilitado e EM QUE DIAS roda.
#
# Motivo: o collect_job_config.ps1 le esses campos de GenerationPolicy
# (EnableSyntheticFulls / SyntheticFullBackupsScheduleKind) e volta NULL em
# 189/189 jobs. Suspeita: no v12 eles moraram para BackupTargetOptions
# (TransformFullToSyntethic / TransformToSyntethicDays), igual aconteceu com
# a retencao e o GFS.
#
# Como usar:
#   1. Edite $JobName abaixo com o nome EXATO de um job que voce SABE pela GUI
#      que tem full sintetico LIGADO (facilita conferir o valor).
#   2. Rode no servidor VBR:  .\discover_synthetic_full.ps1
#   3. Copie TODA a saida do console e cole no chat.
#
# Nao altera nada. Nao exporta senha.
# =============================================================================

$JobName = "NOME DA ROTINA AQUI"   # AJUSTE AQUI

# ---------------------------------------------------------------------------
try { Import-Module Veeam.Backup.PowerShell -ErrorAction SilentlyContinue } catch {}
try { Add-PSSnapin VeeamPSSnapIn -ErrorAction SilentlyContinue } catch {}

$job = Get-VBRJob -Name $JobName -ErrorAction SilentlyContinue
if (-not $job) {
    Write-Host ("Job exato nao encontrado. Tentando correspondencia parcial...") -ForegroundColor DarkYellow
    $job = Get-VBRJob | Where-Object { $_.Name -like ("*" + $JobName + "*") } | Select-Object -First 1
}
if (-not $job) {
    Write-Host "JOB NAO ENCONTRADO. Ajuste a variavel JobName no topo." -ForegroundColor Red
    Write-Host "Jobs disponiveis (primeiros 30):" -ForegroundColor DarkGray
    Get-VBRJob | Select-Object -First 30 -ExpandProperty Name | ForEach-Object { Write-Host ("  " + $_) }
    return
}

Write-Host ("JOB: {0}" -f $job.Name) -ForegroundColor Cyan
Write-Host ""

$opt = $null; try { $opt = $job.GetOptions() } catch {}
if (-not $opt) { Write-Host "GetOptions() falhou." -ForegroundColor Red; return }

# Funcao: imprime todas as propriedades (nome = valor) de um objeto.
function Dump-Object {
    param($obj, [string]$label, [int]$depth = 0)
    if ($null -eq $obj) {
        Write-Host ("{0}{1} = (null)" -f ("  " * $depth), $label) -ForegroundColor DarkGray
        return
    }
    $t = $obj.GetType().Name
    Write-Host ("{0}== {1}  [{2}] ==" -f ("  " * $depth), $label, $t) -ForegroundColor Yellow
    $props = $obj.PSObject.Properties | Sort-Object Name
    foreach ($p in $props) {
        $val = $null
        try { $val = $p.Value } catch { $val = "(erro ao ler)" }
        $vt = if ($null -ne $val) { $val.GetType().Name } else { "null" }

        # Array/colecao (ex.: dias da semana): imprime achatado, e o que interessa
        if ($null -ne $val -and $val -isnot [string] -and $val -is [System.Collections.IEnumerable]) {
            $flat = (@($val) | ForEach-Object { "$_" }) -join ", "
            Write-Host ("{0}  {1} = [{2}]   [{3}]" -f ("  " * $depth), $p.Name, $flat, $vt) -ForegroundColor White
            continue
        }

        $isSimple = ($null -eq $val) -or ($val -is [string]) -or ($val -is [bool]) `
                    -or ($val -is [int]) -or ($val -is [long]) -or ($val -is [double]) `
                    -or ($val -is [datetime]) -or ($val.GetType().IsEnum)
        if ($isSimple) {
            Write-Host ("{0}  {1} = {2}   [{3}]" -f ("  " * $depth), $p.Name, $val, $vt)
        }
        elseif ($depth -lt 2) {
            Write-Host ("{0}  {1}:  [{2}]" -f ("  " * $depth), $p.Name, $vt) -ForegroundColor Green
            Dump-Object -obj $val -label $p.Name -depth ($depth + 2)
        }
        else {
            Write-Host ("{0}  {1} = (objeto {2}, nao expandido)" -f ("  " * $depth), $p.Name, $vt)
        }
    }
}

# --- 1) O suspeito principal: onde o v12 guarda synthetic/active full --------
Write-Host "########## BackupTargetOptions  (SUSPEITO PRINCIPAL) ##########" -ForegroundColor Magenta
Dump-Object -obj $opt.BackupTargetOptions -label "BackupTargetOptions"
Write-Host ""

# --- 2) Onde o coletor le hoje (volta null p/ synthetic, ok p/ compact) -----
Write-Host "########## GenerationPolicy  (onde o coletor le HOJE) ##########" -ForegroundColor Magenta
Dump-Object -obj $opt.GenerationPolicy -label "GenerationPolicy"
Write-Host ""

# --- 3) Outros caminhos possiveis -------------------------------------------
Write-Host "########## Outros caminhos possiveis ##########" -ForegroundColor Magenta
foreach ($path in @("BackupStorageOptions","SyntheticFullOptions","BackupTargetOptions.FullBackupMonthlyScheduleOptions")) {
    $v = $null
    try {
        $v = $opt
        foreach ($seg in ($path -split '\.')) { if ($null -ne $v) { $v = $v.$seg } }
    } catch {}
    if ($null -ne $v) { Dump-Object -obj $v -label ("opt." + $path) }
    else { Write-Host ("opt.{0} = (null/inexistente)" -f $path) -ForegroundColor DarkGray }
}
Write-Host ""

# --- 4) Varredura por nome: qualquer propriedade que cheire a full ----------
# Pega o que a gente nao previu (nomes diferentes por versao/typo da Veeam).
Write-Host "########## Varredura: propriedades com Synt/Full/Transform no nome ##########" -ForegroundColor Magenta
$roots = @{
    "opt"                     = $opt
    "opt.BackupTargetOptions" = $opt.BackupTargetOptions
    "opt.GenerationPolicy"    = $opt.GenerationPolicy
    "opt.BackupStorageOptions"= $opt.BackupStorageOptions
}
foreach ($rk in $roots.Keys) {
    $ro = $roots[$rk]
    if ($null -eq $ro) { continue }
    foreach ($p in ($ro.PSObject.Properties | Sort-Object Name)) {
        if ($p.Name -match 'Synt|Full|Transform|Algorithm|Active') {
            $val = $null
            try { $val = $p.Value } catch { $val = "(erro ao ler)" }
            if ($null -ne $val -and $val -isnot [string] -and $val -is [System.Collections.IEnumerable]) {
                $val = "[" + (((@($val) | ForEach-Object { "$_" }) -join ", ")) + "]"
            }
            Write-Host ("{0}.{1} = {2}" -f $rk, $p.Name, $val) -ForegroundColor White
        }
    }
}

Write-Host ""
Write-Host "CONFERE NA GUI: Job / Storage / Advanced / aba Backup" -ForegroundColor Cyan
Write-Host "  (Create synthetic full backups periodically + os dias marcados)" -ForegroundColor Cyan
Write-Host "FIM. Cole TODA a saida acima no chat." -ForegroundColor Cyan
