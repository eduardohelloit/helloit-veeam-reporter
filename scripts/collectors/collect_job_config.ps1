<#
============================================================================
 Veeam B&R - AUDITORIA DE CONFIGURAÇÃO de rotina  ->  NDJSON + JSON pretty
----------------------------------------------------------------------------
 Responde "COMO a rotina está CONFIGURADA?" - NÃO "como executou".
 NÃO coleta execução/sessão/histórico/resultado/duração.

 Coleta a configuração ATUAL de UMA rotina (job) do Veeam:
   geral, schedule, repositório/SOBR, backup proxy, guest interaction proxy,
   guest processing, application-aware, SQL transaction log, credenciais (sem
   senha), objetos, exclusões, retenção, advanced/storage, notificações.

 Inclui raw_debug (nomes/tipos de propriedades) e raw_sanitized (estruturas
 brutas SEM campos sensíveis) para validarmos o que o Veeam expõe nesta versão.

 SOMENTE LEITURA. Nada de Set-/Remove-/Start-/Enable-/Disable-/Sync-/Rescan-.
 Windows PowerShell 5.1. Rodar no servidor Veeam.
 Versao: 1.0
============================================================================
#>
# ----- CONFIGURACAO (defaults; pode passar por parametro) -------------------
param(
    [string]$VbrServer       = "localhost",
    [string]$JobName         = "NOME DA ROTINA AQUI",
    [string]$OutputDir       = "C:\temp\veeam-job-config-audit",
    [switch]$IncludeRawDebug,                 # so p/ descoberta (arquivo enxuto sem isto)
    [bool]  $CollectAllJobs  = $true,         # TODAS as rotinas (1 linha NDJSON por rotina)
    [switch]$Pretty                           # tambem grava .pretty.json (o sistema so usa o NDJSON)
)
# ----------------------------------------------------------------------------
$ScriptVersion = "1.8"
$CollectionType = "veeam_job_configuration_audit"
$ErrorActionPreference = "Stop"

# Campos sensíveis a remover/redatar (qualquer nome contendo um destes)
$SensitiveRe = '(?i)(password|passwd|secret|token|secure|credential_password|privatekey|\bkey\b)'

# ============================ FUNCOES AUXILIARES =============================

# Lê uma propriedade simples; retorna $null se ausente.
function Get-PropertySafe {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    try { $p = $Object.PSObject.Properties[$Name]; if ($p) { return $p.Value } } catch {}
    return $null
}

# Lê propriedade aninhada por caminho "A.B.C".
function Get-NestedPropertySafe {
    param($Object, [string]$Path)
    $v = $Object
    foreach ($part in $Path.Split('.')) {
        $v = Get-PropertySafe $v $part
        if ($null -eq $v) { return $null }
    }
    return $v
}

# Primeiro caminho não-nulo de uma lista de candidatos (suporta aninhado).
function First-NonNull {
    param($Object, [string[]]$Names)
    foreach ($n in $Names) {
        $v = if ($n -match '\.') { Get-NestedPropertySafe $Object $n } else { Get-PropertySafe $Object $n }
        if ($null -ne $v -and "$v" -ne '') { return $v }
    }
    return $null
}

# String segura de um valor (enum/obj -> texto).
function S { param($v) if ($null -eq $v) { return $null } return "$v" }

# Normaliza o nome da rotina (trim, espaços, hífens, uppercase).
function Normalize-JobName {
    param([string]$Name)
    if (-not $Name) { return "" }
    $s = ($Name -replace '\s+', ' ').Trim()
    $s = $s -replace '\s*-\s*', ' - '
    $s = ($s -replace '\s+', ' ').Trim()
    return $s.ToUpper()
}

# Lista nome|tipo|valor(truncado) das propriedades de um objeto (para raw_debug).
function Get-PropertyDump {
    param($Object)
    if ($null -eq $Object) { return @("<null>") }
    $out = @()
    try { $props = $Object.PSObject.Properties | Sort-Object Name } catch { return @("<sem propriedades>") }
    foreach ($p in $props) {
        $name = $p.Name
        $val = $null; $tname = ""
        try { $val = $p.Value; if ($null -ne $val) { $tname = $val.GetType().Name } } catch { $val = "<erro>" }
        if ($name -match $SensitiveRe) { $disp = "***REDACTED***" }
        elseif ($null -eq $val) { $disp = "<null>" }
        elseif ($val -is [System.Collections.IEnumerable] -and $val -isnot [string]) {
            $c = 0; try { $c = @($val).Count } catch {}; $disp = "[colecao: $c]"
        } else { $disp = "$val"; if ($disp.Length -gt 120) { $disp = $disp.Substring(0,120) + "..." }; $disp = $disp -replace "\r?\n"," " }
        $out += ("{0} | {1} | {2}" -f $name, $tname, $disp)
    }
    return $out
}

# Converte um objeto em hashtable RASA (1 nível), removendo campos sensíveis.
# Evita recursão profunda/circular dos objetos Veeam.
function Convert-ToSafeJsonObject {
    param($Object)
    if ($null -eq $Object) { return $null }
    $h = [ordered]@{}
    try { $props = $Object.PSObject.Properties } catch { return @{} }
    foreach ($p in $props) {
        $name = $p.Name
        if ($name -match $SensitiveRe) { $h[$name] = "***REDACTED***"; continue }
        $val = $null
        try { $val = $p.Value } catch { $h[$name] = "<erro>"; continue }
        if ($null -eq $val) { $h[$name] = $null }
        elseif ($val -is [string] -or $val -is [bool] -or $val -is [int] -or $val -is [long] -or $val -is [double] -or $val -is [datetime]) { $h[$name] = $val }
        elseif ($val.GetType().IsEnum) { $h[$name] = "$val" }
        elseif ($val -is [System.Collections.IEnumerable]) { $c = 0; try { $c = @($val).Count } catch {}; $h[$name] = "[colecao: $c]" }
        else { $h[$name] = "$val" }   # objeto aninhado -> apenas texto (sem descer)
    }
    return $h
}

# Remove recursivamente campos sensíveis (defesa extra antes de exportar).
function Remove-SensitiveFields {
    param($Object)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        $keys = @($Object.Keys)
        foreach ($k in $keys) {
            if ("$k" -match $SensitiveRe) { $Object[$k] = "***REDACTED***" }
            else { $Object[$k] = Remove-SensitiveFields $Object[$k] }
        }
        return $Object
    }
    if ($Object -is [System.Collections.IEnumerable] -and $Object -isnot [string]) {
        $arr = @(); foreach ($i in $Object) { $arr += (Remove-SensitiveFields $i) }; return $arr
    }
    return $Object
}

# =============================== PREPARACAO =================================
if (-not (Test-Path -LiteralPath $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }
try {
    if (-not (Get-Module -Name Veeam.Backup.PowerShell)) { Import-Module Veeam.Backup.PowerShell -ErrorAction Stop }
} catch { Write-Error "Falha ao importar modulo Veeam. $($_.Exception.Message)"; return }
try {
    if (-not (Get-VBRServerSession -ErrorAction SilentlyContinue)) {
        Write-Host "Conectando em $VbrServer ..." -ForegroundColor Cyan
        Connect-VBRServer -Server $VbrServer
    }
} catch { Write-Error "Falha ao conectar no VBR. $($_.Exception.Message)"; return }

# =============================== COLETA POR ROTINA =========================
# Recebe um job e retorna o documento de configuração (ordered hashtable).
# Nao escreve arquivo nem imprime - isso fica no driver no fim.
function Collect-JobConfigDoc {
    param($job)

    # Objetos auxiliares (best-effort, somente leitura)
$opt   = $null; try { $opt   = $job.GetOptions() }         catch {}
$sched = $null; try { $sched = $job.GetScheduleOptions() } catch { try { $sched = $job.ScheduleOptions } catch {} }
$vss   = $null; try { $vss   = $job.GetVssOptions() }      catch { try { $vss = $job.VssOptions } catch {} }
$sql   = Get-PropertySafe $vss "SqlBackupOptions"
$vssSnap = Get-PropertySafe $vss "VssSnapshotOptions"
$genPol  = Get-PropertySafe $opt "GenerationPolicy"
$stgOpt  = Get-PropertySafe $opt "BackupStorageOptions"
$srcOpt  = Get-PropertySafe $opt "ViSourceOptions"
$tgtOpt  = Get-PropertySafe $opt "BackupTargetOptions"

$repo = $null; try { $repo = $job.GetTargetRepository() } catch {}
$proxies = @(); try { $proxies = @($job.GetProxy()) } catch {}
$objs = @(); try { $objs = @(Get-VBRJobObject -Job $job) } catch { try { $objs = @($job.GetObjectsInJob()) } catch {} }

# =============================== MONTAGEM ===================================
function NB { param($v) if ($null -eq $v) { return $null } return $v }   # passthrough p/ legibilidade

# Achata DayOfWeek[] em "Saturday" / "Monday Saturday" (mesmo formato de days_of_week).
function Days-Str {
    param($v)
    if ($null -eq $v) { return $null }
    $a = @($v) | ForEach-Object { "$_" } | Where-Object { $_ -ne "" }
    if ($a.Count -eq 0) { return $null }
    return ($a -join " ")
}

# "First Monday" a partir de CDomFullBackupMonthlyScheduleOptions (usado quando
# o Kind e Monthly; nesse caso a lista de dias da semana nao vale).
function Monthly-Str {
    param($mo)
    if ($null -eq $mo) { return $null }
    $num = S (First-NonNull $mo @("DayNumberInMonth"))
    $dow = S (First-NonNull $mo @("DayOfWeek"))
    if (-not $num -and -not $dow) { return $null }
    return (($num, $dow) -join " ").Trim()
}

$jobBlock = [ordered]@{
    job_name            = $job.Name
    job_name_normalized = Normalize-JobName $job.Name
    job_id              = S (First-NonNull $job @("Id","Uid"))
    job_type            = S (First-NonNull $job @("JobType","TypeToString"))
    job_description     = First-NonNull $job @("Description")
    is_enabled          = (First-NonNull $job @("IsScheduleEnabled")) -eq $true
    is_schedule_enabled = (First-NonNull $job @("IsScheduleEnabled")) -eq $true
    next_run            = S (First-NonNull $sched @("NextRun"))
    platform            = S (First-NonNull $job @("BackupPlatform","Info.BackupPlatform"))
    backup_type         = S (First-NonNull $job @("BackupType","JobType"))
}

# Schedule periodico: FullPeriod vem na unidade base (OptionsPeriodically.Unit,
# tipicamente Seconds). Kind e a unidade de exibicao (Hours/Minutes). Ex.:
# FullPeriod=7200, Unit=Seconds, Kind=Hours  ->  7200s = 2 horas.
$perFull = First-NonNull $sched @("OptionsPeriodically.FullPeriod")
$perKind = S (First-NonNull $sched @("OptionsPeriodically.Kind"))
$perBase = S (First-NonNull $sched @("OptionsPeriodically.Unit"))
$perEvery = $perFull
if ($null -ne $perFull) {
    # normaliza para segundos conforme a unidade base
    $sec = [double]$perFull
    switch ("$perBase") {
        "Minutes" { $sec = $sec * 60 }
        "Hours"   { $sec = $sec * 3600 }
        default   { }   # Seconds (ou desconhecido) -> ja em segundos
    }
    switch ("$perKind") {
        "Hours"   { $perEvery = [math]::Round($sec / 3600, 2) }
        "Minutes" { $perEvery = [math]::Round($sec / 60, 2) }
        "Seconds" { $perEvery = [math]::Round($sec, 0) }
        default   { $perEvery = $perFull }
    }
    # remove .0 desnecessario
    if ($perEvery -eq [math]::Floor($perEvery)) { $perEvery = [int]$perEvery }
}

$scheduleBlock = [ordered]@{
    schedule_enabled      = First-NonNull $job   @("IsScheduleEnabled")
    daily_enabled         = First-NonNull $sched @("OptionsDaily.Enabled")
    daily_time            = S (First-NonNull $sched @("OptionsDaily.TimeLocal","OptionsDaily.Time"))
    daily_kind            = S (First-NonNull $sched @("OptionsDaily.Kind"))
    days_of_week          = S (First-NonNull $sched @("OptionsDaily.DaysSrv"))
    periodically_enabled  = First-NonNull $sched @("OptionsPeriodically.Enabled")
    periodically_every    = S $perEvery
    periodically_unit     = $perKind
    periodically_raw_full = S $perFull
    periodically_raw_unit = $perBase
    monthly_enabled       = First-NonNull $sched @("OptionsMonthly.Enabled")
    monthly_schedule      = S (First-NonNull $sched @("OptionsMonthly.DayNumberInMonth","OptionsMonthly.DayOfWeek"))
    continuous_enabled    = First-NonNull $sched @("OptionsContinuous.Enabled")
    retry_enabled         = First-NonNull $sched @("RetrySpecified","RetryTimes")
    retry_count           = S (First-NonNull $sched @("RetryTimes","RetryCount"))
    retry_wait_minutes    = S (First-NonNull $sched @("RetryTimeout","WaitTimeout"))
    backup_window_enabled = First-NonNull $sched @("OptionsBackupWindow.IsEnabled","BackupTerminationWindowEnabled")
    after_this_job        = First-NonNull $sched @("OptionsScheduleAfterJob.IsEnabled","RunAfterJob")
    chain_job_name        = S (First-NonNull $sched @("OptionsScheduleAfterJob.PreviousJobUid"))
}

# Repositório / SOBR
# Imutabilidade e Linux hardened REAIS ficam nos EXTENTS (performance tier),
# nao no SOBR de topo (que retorna IsImmutabilitySupported=false).
$repoTypeStr = S (First-NonNull $repo @("TypeDisplay","Type"))
$isSobr = $false
if ($repoTypeStr -and ($repoTypeStr -match 'Scale|SOBR|Compose|Extendable')) { $isSobr = $true }

$extentNames = @()
$extCount = 0
$repoImmut = $false
$repoHardened = $false
$capName = $null; $capImmut = $null; $capImmutDays = $null

if ($isSobr) {
    $hardenedAll = $true
    $exts = @()
    try { $exts = @(Get-VBRRepositoryExtent -Repository $repo) } catch {}
    foreach ($e in $exts) {
        $er = First-NonNull $e @("Repository"); if (-not $er) { $er = $e }
        $extCount++
        $en = S (First-NonNull $er @("Name")); if ($en) { $extentNames += $en }
        if ((First-NonNull $er @("IsImmutabilitySupported")) -eq $true) { $repoImmut = $true }
        if ((First-NonNull $er @("IsLinuxHardened")) -ne $true) { $hardenedAll = $false }
    }
    if ($extCount -gt 0) { $repoHardened = $hardenedAll }

    # Capacity tier (object storage) - imutabilidade propria, separada do performance tier.
    try {
        $sobrObj = Get-VBRBackupRepository -Scaleout -Name (S (First-NonNull $repo @("Name")))
        $capRepo = First-NonNull $sobrObj @("CapacityExtent.Repository")
        if ($capRepo) {
            $capName     = S (First-NonNull $capRepo @("Name"))
            $capImmut    = (First-NonNull $capRepo @("BackupImmutabilityEnabled")) -eq $true
            $capImmutDays= S (First-NonNull $capRepo @("ImmutabilityPeriod"))
        }
    } catch {}
} else {
    $repoImmut    = (First-NonNull $repo @("IsImmutabilitySupported")) -eq $true
    $repoHardened = (First-NonNull $repo @("IsLinuxHardened")) -eq $true
}

$repositoryBlock = [ordered]@{
    repository_name   = S (First-NonNull $repo @("Name"))
    repository_id     = S (First-NonNull $repo @("Id","Uid"))
    repository_type   = $repoTypeStr
    is_sobr           = $isSobr
    sobr_name         = if ($isSobr) { S (First-NonNull $repo @("Name")) } else { $null }
    extent_count      = $extCount
    extent_names      = $extentNames
    immutability_supported = $repoImmut        # performance tier (extents) imutavel
    is_linux_hardened      = $repoHardened      # todos os extents hardened
    is_object_storage      = (First-NonNull $repo @("IsObjectStorageRepository")) -eq $true
    per_vm_backup_files    = (First-NonNull $repo @("SplitStoragesPerVm")) -eq $true
    capacity_tier_name        = $capName
    capacity_tier_immutable   = $capImmut
    capacity_tier_immut_days  = $capImmutDays
    target_repository_name = S (First-NonNull $repo @("Name"))
    target_repository_id   = S (First-NonNull $repo @("Id","Uid"))
}

# Backup Proxy
$proxyNames = @(); $proxyIds = @()
foreach ($p in $proxies) { $proxyNames += (S (First-NonNull $p @("Name"))); $proxyIds += (S (First-NonNull $p @("Id","Uid"))) }
# Veeam não expõe um flag "automatic" simples aqui; inferimos pela lista de proxies.
$proxyAuto = ($proxyNames.Count -eq 0)
$proxyTransports = @($proxies | ForEach-Object { S (First-NonNull $_ @("TransportMode")) } | Where-Object { $_ } | Select-Object -Unique)
$backupProxyBlock = [ordered]@{
    proxy_selection_mode      = if ($proxyAuto) { "Automatic" } else { "Selected" }
    automatic_proxy_selection = $proxyAuto
    selected_proxy_names      = $proxyNames
    selected_proxy_ids        = $proxyIds
    selected_proxy_count      = $proxyNames.Count
    transport_modes           = $proxyTransports
}

# Guest Interaction Proxy
$gipAuto = First-NonNull $vss @("GuestProxyAutoDetect","IsGuestProxyAutoDetect")
$guestInteractionProxyBlock = [ordered]@{
    guest_interaction_proxy_mode = if ($gipAuto -eq $true) { "Automatic" } elseif ($gipAuto -eq $false) { "Selected" } else { "Unknown" }
    automatic_guest_interaction_proxy = $gipAuto
    selected_guest_interaction_proxy_names = @()   # preencher após validar via raw
    note = "Confirmar nomes do GIP via raw_debug.vss_properties."
}

# Guest Processing
$guestCredId  = First-NonNull $vss @("WinCredsId","WindowsCredsId","GuestCredsId")
$winCredsSet  = (First-NonNull $vss @("AreWinCredsSet")) -eq $true
$fsIndexType  = S (First-NonNull $vss @("GuestFSIndexingType","IndexingType"))
$appProcEnabled = (First-NonNull $vssSnap @("ApplicationProcessingEnabled")) -eq $true
$guestProcessingBlock = [ordered]@{
    guest_processing_enabled       = (First-NonNull $vss @("Enabled")) -eq $true
    application_aware_processing_enabled = $appProcEnabled
    guest_fs_indexing_type         = $fsIndexType
    guest_file_system_indexing_enabled = ($fsIndexType -ne $null -and $fsIndexType -ne 'None')
    guest_credentials_configured   = $winCredsSet
    guest_credentials_id           = S $guestCredId
}

# Application-Aware Processing
$ignoreErr = (First-NonNull $vssSnap @("IgnoreErrors")) -eq $true
$applicationAwareBlock = [ordered]@{
    application_aware_enabled = $appProcEnabled
    application_aware_mode    = if ($ignoreErr) { "TryApplicationProcessing" } else { "RequireSuccess" }
    require_success          = (-not $ignoreErr)
    ignore_failures          = $ignoreErr
    vmware_tools_quiescence_enabled = (First-NonNull $srcOpt @("VMToolsQuiesce","EnableVMToolsQuiesce")) -eq $true
    is_copy_only             = (First-NonNull $vssSnap @("IsCopyOnly")) -eq $true
    per_object_overrides     = @()   # coletado em objects[] quando disponivel
}

# SQL Transaction Log
$txMode = S (First-NonNull $sql @("TransactionLogsProcessing"))
$logBackupEnabled = (First-NonNull $sql @("BackupLogsEnabled")) -eq $true
$neverTruncate    = (First-NonNull $sql @("NeverTruncateLogs")) -eq $true
$sqlBlock = [ordered]@{
    detected                  = ($sql -ne $null)
    sql_processing_enabled    = ($sql -ne $null)
    transaction_log_mode      = $txMode                          # Never | TruncateOnlyOnSuccessJob | Backup...
    transaction_log_backup_enabled = $logBackupEnabled            # backup periódico dos logs (shipping)
    truncate_logs_enabled     = (-not $neverTruncate)
    transaction_log_frequency_minutes = S (First-NonNull $sql @("BackupLogsFrequencyMin"))
    log_retention_days        = S (First-NonNull $sql @("RetainDays"))
    use_db_backup_retention   = (First-NonNull $sql @("UseDbBackupRetention")) -eq $true
    sql_log_proxy_auto_select = (First-NonNull $sql @("ProxyAutoSelect")) -eq $true
    fail_job_on_db_absence    = (First-NonNull $sql @("FailJobOnDbAbsenceOrBackupImpossibility")) -eq $true
    copy_only_mode            = (First-NonNull $vssSnap @("IsCopyOnly")) -eq $true
    confidence                = if ($sql -ne $null) { "ok" } else { "unknown" }
    raw_source                = "vss.SqlBackupOptions"
}

# Credenciais (sem senha)
$credentialsBlock = [ordered]@{
    credentials_configured   = ($guestCredId -ne $null -and "$guestCredId" -notmatch '^0{8}-')
    credential_id            = S $guestCredId
    used_for_guest_processing = First-NonNull $vss @("AreApplicationsAware")
    note = "Senha NUNCA exportada. Nome da credencial pode ser resolvido via Get-VBRCredentials (futuro)."
}

# Retencao + GFS
# Veeam 12: a retencao real e o GFS NAO estao onde o v11 guardava.
#   - Dias (40): BackupStorageOptions.RetainDaysToKeep  (RetainDays=30 e "remover VM excluida apos N dias")
#   - Pontos:    BackupStorageOptions.RetainCycles
#   - GFS real:  opt.GfsPolicy.{Weekly,Monthly,Yearly}.{IsEnabled,KeepBackupsForNumberOf*}
#               (o modelo flat GenerationPolicy.GFS* fica zerado/legado).
$gfsPol = Get-PropertySafe $opt "GfsPolicy"
$gfsWk  = Get-PropertySafe $gfsPol "Weekly"
$gfsMo  = Get-PropertySafe $gfsPol "Monthly"
$gfsYr  = Get-PropertySafe $gfsPol "Yearly"

# Enabled por nivel (v12 GfsPolicy) com fallback p/ modelo flat antigo (v11).
$gfsWEn = First-NonNull $gfsWk @("IsEnabled"); if ($null -eq $gfsWEn) { $gfsWEn = First-NonNull $genPol @("GFSWeeklyBackupsEnabled")  }
$gfsMEn = First-NonNull $gfsMo @("IsEnabled"); if ($null -eq $gfsMEn) { $gfsMEn = First-NonNull $genPol @("GFSMonthlyBackupsEnabled") }
$gfsYEn = First-NonNull $gfsYr @("IsEnabled"); if ($null -eq $gfsYEn) { $gfsYEn = First-NonNull $genPol @("GFSYearlyBackupsEnabled")  }
$gfsW = (($gfsWEn) -eq $true)
$gfsM = (($gfsMEn) -eq $true)
$gfsY = (($gfsYEn) -eq $true)
$gfsAny = First-NonNull $gfsPol @("IsEnabled")
if ($null -eq $gfsAny) { $gfsAny = ($gfsW -or $gfsM -or $gfsY) } else { $gfsAny = (($gfsAny) -eq $true) }

# Contagens GFS (v12) com fallback p/ flat antigo.
$gfsWN = First-NonNull $gfsWk @("KeepBackupsForNumberOfWeeks");  if ($null -eq $gfsWN) { $gfsWN = First-NonNull $genPol @("GFSWeeklyBackups")  }
$gfsMN = First-NonNull $gfsMo @("KeepBackupsForNumberOfMonths"); if ($null -eq $gfsMN) { $gfsMN = First-NonNull $genPol @("GFSMonthlyBackups") }
$gfsYN = First-NonNull $gfsYr @("KeepBackupsForNumberOfYears");  if ($null -eq $gfsYN) { $gfsYN = First-NonNull $genPol @("GFSYearlyBackups")  }

$retentionBlock = [ordered]@{
    retention_type            = S (First-NonNull $genPol @("RetentionPolicyType"))     # Simple
    restore_points_to_keep    = S (First-NonNull $genPol @("SimpleRetentionRestorePoints","ActualRetentionRestorePoints"))
    storage_retention_type    = S (First-NonNull $stgOpt @("RetentionType"))           # Cycles | Days
    retention_cycles          = S (First-NonNull $stgOpt @("RetainCycles"))
    retention_days            = S (First-NonNull $stgOpt @("RetainDaysToKeep","RetainDays"))  # REAL (Days)
    deleted_vm_retention_days = S (First-NonNull $stgOpt @("RetainDays"))              # "remover VM excluida apos N dias"
    gfs_enabled               = $gfsAny
    gfs_weekly_enabled        = $gfsW
    gfs_weekly                = S $gfsWN
    gfs_monthly_enabled       = $gfsM
    gfs_monthly               = S $gfsMN
    gfs_yearly_enabled        = $gfsY
    gfs_yearly                = S $gfsYN
}

# Objetos protegidos - INCLUI VSS POR OBJETO (override por VM).
# A config real de SQL Transaction Log / Application-Aware frequentemente está
# AQUI (no objeto), não no default do job. Ex.: log shipping a cada 30min por VM.
$objectsBlock = @()
$anyObjLogBackup = $false
foreach ($o in $objs) {
    if ((First-NonNull $o @("IsExcluded")) -eq $true) { continue }   # exclusões tratadas à parte

    $ovss   = First-NonNull $o @("VssOptions")
    $osql   = First-NonNull $ovss @("SqlBackupOptions")
    $ovsnap = First-NonNull $ovss @("VssSnapshotOptions")
    $oTxMode = S (First-NonNull $osql @("TransactionLogsProcessing"))
    $oLogBackup = (First-NonNull $osql @("BackupLogsEnabled")) -eq $true
    if ($oLogBackup) { $anyObjLogBackup = $true }

    $objectsBlock += [ordered]@{
        object_name = S (First-NonNull $o @("Name","Object.Name"))
        object_type = S (First-NonNull $o @("TypeDisplayName","Type","Object.Type"))
        object_id   = S (First-NonNull $o @("ObjectId","Id","Object.Id"))
        path        = S (First-NonNull $o @("Location","Object.Path","Path"))
        approx_size = S (First-NonNull $o @("ApproxSizeString","ApproxSize"))
        # --- VSS por objeto (override) ---
        guest_processing_enabled  = (First-NonNull $ovss @("Enabled")) -eq $true
        application_aware_enabled = (First-NonNull $ovsnap @("ApplicationProcessingEnabled")) -eq $true
        sql_transaction_log = [ordered]@{
            mode                    = $oTxMode
            transaction_log_backup_enabled = $oLogBackup
            frequency_minutes       = S (First-NonNull $osql @("BackupLogsFrequencyMin"))
            retain_days             = S (First-NonNull $osql @("RetainDays"))
            use_db_backup_retention = (First-NonNull $osql @("UseDbBackupRetention")) -eq $true
            truncate_logs_enabled   = ((First-NonNull $osql @("NeverTruncateLogs")) -ne $true)
        }
    }
}

# O bloco SQL acima é o DEFAULT do job; a config efetiva pode estar por objeto.
$sqlBlock.scope = "job_default"
$sqlBlock.per_object_log_backup_detected = $anyObjLogBackup
$sqlBlock.note = "Default do job. A config EFETIVA por VM esta em objects[].sql_transaction_log (override por objeto)."

# Exclusões (best-effort)
$exclusionsBlock = @()
try {
    $excl = @(Get-VBRJobObject -Job $job -Type Exclude)
    foreach ($e in $excl) {
        $exclusionsBlock += [ordered]@{
            excluded_object_name = S (First-NonNull $e @("Name"))
            excluded_object_type = S (First-NonNull $e @("Type","TypeDisplay"))
            excluded_object_id   = S (First-NonNull $e @("Id"))
        }
    }
} catch {}

# Advanced / Storage
$advancedBlock = [ordered]@{
    changed_block_tracking_enabled = First-NonNull $srcOpt @("UseChangeTracking","EnableChangeTracking")
    vmware_tools_quiescence_enabled = First-NonNull $srcOpt @("VMToolsQuiesce","EnableVMToolsQuiesce")
    compression_level       = S (First-NonNull $stgOpt @("CompressionLevel"))
    storage_optimization    = S (First-NonNull $stgOpt @("StgBlockSize"))
    deduplication_enabled   = First-NonNull $stgOpt @("EnableDeduplication")
    encryption_enabled      = First-NonNull $stgOpt @("StorageEncryptionEnabled","EncryptionEnabled")
    integrity_checks        = First-NonNull $stgOpt @("EnableIntegrityChecks")
    # Full sintetico / active full / compact full
    # Veeam 12: NAO estao em GenerationPolicy (so o compact esta). O sintetico e o
    # active full vivem em opt.BackupTargetOptions -- e a Veeam grafa "Syntethic"
    # (typo do produto). Confirmado via discover_synthetic_full.ps1.
    # Pegadinha: o liga/desliga do ACTIVE full esta em BackupStorageOptions
    # (EnableFullBackup), mas os DIAS dele estao em BackupTargetOptions.
    backup_algorithm        = S (First-NonNull $tgtOpt @("Algorithm"))   # Increment | Syntetic | Full
    synthetic_full_enabled  = First-NonNull $tgtOpt @("TransformFullToSyntethic","TransformToSyntheticFull")
    synthetic_full_kind     = S (First-NonNull $tgtOpt @("TransformToSyntethicKind"))   # Daily | Monthly
    synthetic_full_days     = Days-Str (First-NonNull $tgtOpt @("TransformToSyntethicDays"))
    synthetic_full_monthly  = Monthly-Str (Get-PropertySafe $tgtOpt "TransformToSyntethicMonthly")
    transform_to_rollbacks  = First-NonNull $tgtOpt @("TransformToRollbacks")
    active_full_enabled     = First-NonNull $stgOpt @("EnableFullBackup")
    active_full_kind        = S (First-NonNull $tgtOpt @("FullBackupScheduleKind"))     # Daily | Monthly
    active_full_days        = Days-Str (First-NonNull $tgtOpt @("FullBackupDays"))
    active_full_monthly     = Monthly-Str (Get-PropertySafe $tgtOpt "FullBackupMonthlyScheduleOptions")
    compact_full_enabled    = First-NonNull $genPol @("EnableCompactFull")
    compact_full_schedule   = S (First-NonNull $genPol @("CompactFullBackupScheduleKind"))
    compact_full_days       = Days-Str (First-NonNull $genPol @("CompactFullBackupDays"))
    compact_full_monthly    = Monthly-Str (Get-PropertySafe $genPol "CompactFullBackupMonthlyScheduleOptions")
    per_vm_backup_files     = (First-NonNull $repo @("SplitStoragesPerVm")) -eq $true
    storage_snapshot_enabled = First-NonNull $opt @("SanOptions.SanSnapshotsEnabled","SanIntegrationOptions.Enabled")
}

# Notificações
$notificationBlock = [ordered]@{
    email_notification_enabled = First-NonNull $opt @("NotificationOptions.SendEmailNotification2AdditionalAddresses","NotificationOptions.EmailNotification")
    notify_on_success = First-NonNull $opt @("NotificationOptions.EmailNotifyOnSuccess")
    notify_on_warning = First-NonNull $opt @("NotificationOptions.EmailNotifyOnWarning")
    notify_on_failure = First-NonNull $opt @("NotificationOptions.EmailNotifyOnError")
    recipients_count  = 0
}
try {
    $rcpt = First-NonNull $opt @("NotificationOptions.EmailNotificationAddresses")
    if ($rcpt) { $notificationBlock.recipients_count = @($rcpt -split '[;,]').Count }
} catch {}

# Raw debug (descoberta de propriedades) + raw sanitized
$rawDebug = [ordered]@{}
$rawSanitized = [ordered]@{}
if ($IncludeRawDebug) {
    $rawDebug = [ordered]@{
        job_properties           = Get-PropertyDump $job
        options_properties       = Get-PropertyDump $opt
        schedule_properties      = Get-PropertyDump $sched
        vss_properties           = Get-PropertyDump $vss
        sql_options_properties   = Get-PropertyDump $sql
        vss_snapshot_properties  = Get-PropertyDump $vssSnap
        generation_policy_properties = Get-PropertyDump $genPol
        backup_target_properties     = Get-PropertyDump $tgtOpt
        storage_options_properties   = Get-PropertyDump $stgOpt
        source_options_properties    = Get-PropertyDump $srcOpt
        repository_properties    = Get-PropertyDump $repo
        proxy_properties         = if ($proxies.Count -gt 0) { Get-PropertyDump $proxies[0] } else { @("<sem proxies>") }
        object_properties        = if ($objs.Count -gt 0) { Get-PropertyDump $objs[0] } else { @("<sem objetos>") }
    }
    $rawSanitized = [ordered]@{
        job_options      = Convert-ToSafeJsonObject $opt
        schedule_options = Convert-ToSafeJsonObject $sched
        vss_options      = Convert-ToSafeJsonObject $vss
        sql_options      = Convert-ToSafeJsonObject $sql
        proxy_options    = if ($proxies.Count -gt 0) { Convert-ToSafeJsonObject $proxies[0] } else { @{} }
    }
}

# =============================== DOCUMENTO FINAL ===========================
$doc = [ordered]@{
    collection_type = $CollectionType
    script_version  = $ScriptVersion
    collected_at    = ([datetimeoffset](Get-Date)).ToString("o")
    vbr_server      = $VbrServer
    computer_name   = $env:COMPUTERNAME
    job                         = $jobBlock
    schedule                    = $scheduleBlock
    repository                  = $repositoryBlock
    backup_proxy                = $backupProxyBlock
    guest_interaction_proxy     = $guestInteractionProxyBlock
    guest_processing            = $guestProcessingBlock
    application_aware_processing = $applicationAwareBlock
    sql_transaction_log         = $sqlBlock
    credentials                 = $credentialsBlock
    retention                   = $retentionBlock
    objects                     = $objectsBlock
    exclusions                  = $exclusionsBlock
    advanced_settings           = $advancedBlock
    storage_settings            = $advancedBlock   # storage_settings espelha advanced nesta versao
    notification_settings       = $notificationBlock
    raw_debug                   = $rawDebug
    raw_sanitized               = $rawSanitized
}
    return (Remove-SensitiveFields $doc)   # defesa extra
}

# =============================== DRIVER + SAIDA ============================
function YN { param($v) if ($v -eq $true) { "Sim" } elseif ($v -eq $false) { "Nao" } else { "-" } }

function Write-JobSummary {
    param($doc, [switch]$Brief)
    $jb = $doc.job; $rp = $doc.repository; $bp = $doc.backup_proxy; $sq = $doc.sql_transaction_log
    if ($Brief) {
        Write-Host ("  {0,-46} | enab={1,-3} | proxy={2}/{3} | appAware={4,-3} | SQLlogObj={5}" -f `
            ($jb.job_name.Substring(0,[math]::Min(46,$jb.job_name.Length))), (YN $jb.is_enabled),
            $bp.proxy_selection_mode, $bp.selected_proxy_count,
            (YN $doc.application_aware_processing.application_aware_enabled),
            (YN $sq.per_object_log_backup_detected))
        return
    }
    Write-Host "`n== CONFIGURACAO DA ROTINA (nao e execucao) ==" -ForegroundColor Green
    Write-Host ("Rotina:                 {0}" -f $jb.job_name)
    Write-Host ("Habilitada:             {0}" -f (YN $jb.is_enabled))
    Write-Host ("Repositorio:            {0}{1}" -f $rp.repository_name, $(if ($rp.is_sobr) { " (SOBR)" } else { "" }))
    Write-Host ("Backup Proxy:           {0} / {1} proxy(s)" -f $bp.proxy_selection_mode, $bp.selected_proxy_count)
    Write-Host ("Guest Interaction Proxy:{0}" -f $doc.guest_interaction_proxy.guest_interaction_proxy_mode)
    Write-Host ("Guest Processing:       {0}" -f (YN $doc.guest_processing.guest_processing_enabled))
    Write-Host ("Application-Aware:      {0}" -f (YN $doc.application_aware_processing.application_aware_enabled))
    Write-Host ("SQL Tx Log (default):   modo={0} backup_logs={1} freq={2}min" -f $sq.transaction_log_mode, (YN $sq.transaction_log_backup_enabled), $sq.transaction_log_frequency_minutes)
    Write-Host ("SQL Tx Log (por objeto):") -ForegroundColor Cyan
    foreach ($ob in @($doc.objects)) {
        $ot = $ob.sql_transaction_log
        Write-Host ("   - {0}: modo={1} backup_logs={2} freq={3}min" -f $ob.object_name, $ot.mode, (YN $ot.transaction_log_backup_enabled), $ot.frequency_minutes)
    }
    Write-Host ("Objetos protegidos:     {0}" -f @($doc.objects).Count)
    Write-Host ("Exclusoes:              {0}" -f @($doc.exclusions).Count)
}

$stamp     = (Get-Date).ToString("yyyyMMdd_HHmmss")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if ($CollectAllJobs) {
    # ---- Modo MASSA: todas as rotinas (1 linha NDJSON por rotina) ----
    Write-Host "Coletando configuracao de TODAS as rotinas..." -ForegroundColor Cyan
    if ($IncludeRawDebug) {
        Write-Host 'AVISO: IncludeRawDebug ligado em massa gera arquivo MUITO grande.' -ForegroundColor Yellow
    }
    $allJobs = @(); try { $allJobs = @(Get-VBRJob) } catch { Write-Error "Falha em Get-VBRJob. $($_.Exception.Message)"; return }
    Write-Host ("Rotinas encontradas: {0}" -f $allJobs.Count)

    # Padrão @( foreach {...} ) -> array garantido. NUNCA usar @($list) sobre
    # Generic.List (dispara "Argument types do not match" no PowerShell 5.1).
    $i = 0
    $docs = @(foreach ($j in $allJobs) {
        $i++
        Write-Host ("  [{0}/{1}] {2}" -f $i, $allJobs.Count, $j.Name) -ForegroundColor DarkGray
        try { Collect-JobConfigDoc $j }
        catch { Write-Host ("    ERRO em {0}: {1}" -f $j.Name, $_.Exception.Message) -ForegroundColor Red }
    })

    $ndjsonPath = Join-Path $OutputDir "job_config_audit_ALL_$stamp.ndjson"
    $prettyPath = Join-Path $OutputDir "job_config_audit_ALL_$stamp.pretty.json"
    $lines = foreach ($d in $docs) { $d | ConvertTo-Json -Depth 12 -Compress }
    [System.IO.File]::WriteAllLines($ndjsonPath, [string[]]@($lines), $utf8NoBom)
    if ($Pretty) { [System.IO.File]::WriteAllText($prettyPath, ($docs | ConvertTo-Json -Depth 12), $utf8NoBom) }

    Write-Host "`n== RESUMO (todas as rotinas) ==" -ForegroundColor Green
    foreach ($d in $docs) { Write-JobSummary $d -Brief }
    Write-Host ("`nTotal coletado: {0} rotinas" -f $docs.Count) -ForegroundColor Green
    Write-Host ("NDJSON:  {0}" -f $ndjsonPath) -ForegroundColor Green
    if ($Pretty) { Write-Host ("Pretty:  {0}" -f $prettyPath) -ForegroundColor Green }
}
else {
    # ---- Modo UNICO: rotina pelo nome ----
    $job = $null
    try { $job = Get-VBRJob -Name $JobName -ErrorAction SilentlyContinue } catch {}
    if (-not $job) { try { $job = Get-VBRJob | Where-Object { $_.Name -like "*$JobName*" } | Select-Object -First 1 } catch {} }
    if (-not $job) {
        Write-Error "Rotina '$JobName' nao encontrada. Ajuste `$JobName ou use `$CollectAllJobs = `$true."
        Write-Host "Jobs disponiveis (amostra):" -ForegroundColor Yellow
        try { Get-VBRJob | Select-Object -First 40 | ForEach-Object { Write-Host "  - $($_.Name)" } } catch {}
        return
    }
    Write-Host ("Rotina encontrada: {0}" -f $job.Name) -ForegroundColor Green

    $doc = Collect-JobConfigDoc $job
    $ndjsonPath = Join-Path $OutputDir "job_config_audit_$stamp.ndjson"
    $prettyPath = Join-Path $OutputDir "job_config_audit_$stamp.pretty.json"
    [System.IO.File]::WriteAllText($ndjsonPath, ($doc | ConvertTo-Json -Depth 12 -Compress), $utf8NoBom)
    if ($Pretty) { [System.IO.File]::WriteAllText($prettyPath, ($doc | ConvertTo-Json -Depth 12), $utf8NoBom) }

    Write-JobSummary $doc
    Write-Host ("`nNDJSON:  {0}" -f $ndjsonPath) -ForegroundColor Green
    if ($Pretty) { Write-Host ("Pretty:  {0}" -f $prettyPath) -ForegroundColor Green }
}
