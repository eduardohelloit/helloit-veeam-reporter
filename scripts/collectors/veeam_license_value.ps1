<#
=============================================================================
 veeam_license_value.ps1  -  retorna UM unico valor da licenca do Veeam
 (saida limpa para Zabbix: apenas o valor, sem nenhum texto extra)

 Uso:
   pwsh -File .\veeam_license_value.ps1 expirationdate
   pwsh -File .\veeam_license_value.ps1 daysremaining

 Campos suportados (case-insensitive):
   expirationdate          -> data de expiracao da licenca   (yyyy-MM-dd)
   supportexpirationdate   -> data de expiracao do suporte   (yyyy-MM-dd)
   daysremaining           -> dias ate expirar a licenca     (inteiro)
   supportdaysremaining    -> dias ate expirar o suporte     (inteiro)
   status                  -> Valid / Expired / Warning
   type                    -> Perpetual / Subscription / ...
   edition                 -> edicao licenciada
   licensedto              -> empresa
   supportid               -> ID de suporte

 SOMENTE LEITURA. Requer PowerShell 7 (modulo Veeam exige pwsh).
=============================================================================
#>
param(
    [Parameter(Position = 0, Mandatory = $false)]
    [string]$Field = "expirationdate",

    [string]$VbrServer = "localhost"
)

$ErrorActionPreference = "Stop"

# Formato de data fixo (ISO) - sem ambiguidade de locale para o Zabbix.
$DATE_FMT = "yyyy-MM-dd"

# Erros vao para stderr (visiveis na mao, NAO poluem o stdout que o Zabbix le).
function Fail { param([string]$msg, [int]$code = 1) [Console]::Error.WriteLine("ERRO: $msg"); exit $code }

try {
    # Silencia ruido de import/conexao no STDOUT (mantem stderr).
    try { Import-Module Veeam.Backup.PowerShell -ErrorAction Stop 3>$null 4>$null 5>$null 6>$null | Out-Null }
    catch { Fail "Import-Module Veeam.Backup.PowerShell falhou: $($_.Exception.Message)" }

    # Conecta (ignora se ja houver sessao). Em PS7 use sempre Connect-VBRServer.
    try { Connect-VBRServer -Server $VbrServer -ErrorAction Stop | Out-Null }
    catch {
        if ("$($_.Exception.Message)" -notmatch 'already') {
            Fail "Connect-VBRServer falhou: $($_.Exception.Message)"
        }
    }

    $lic = Get-VBRInstalledLicense -ErrorAction Stop | Select-Object -First 1
    if (-not $lic) { Fail "Get-VBRInstalledLicense nao retornou licenca." }

    $now = Get-Date

    switch ($Field.ToLower()) {
        "expirationdate" {
            if ($null -ne $lic.ExpirationDate) { $out = ([datetime]$lic.ExpirationDate).ToString($DATE_FMT) }
        }
        "supportexpirationdate" {
            if ($null -ne $lic.SupportExpirationDate) { $out = ([datetime]$lic.SupportExpirationDate).ToString($DATE_FMT) }
        }
        "daysremaining" {
            if ($null -ne $lic.ExpirationDate) { $out = [int]([math]::Floor((New-TimeSpan -Start $now -End ([datetime]$lic.ExpirationDate)).TotalDays)) }
        }
        "supportdaysremaining" {
            if ($null -ne $lic.SupportExpirationDate) { $out = [int]([math]::Floor((New-TimeSpan -Start $now -End ([datetime]$lic.SupportExpirationDate)).TotalDays)) }
        }
        "status"     { $out = "$($lic.Status)" }
        "type"       { $out = "$($lic.Type)" }
        "edition"    { $out = "$($lic.Edition)" }
        "licensedto" { $out = "$($lic.LicensedTo)" }
        "supportid"  { $out = "$($lic.SupportId)" }
        default {
            Fail "Campo desconhecido: '$Field'." 2
        }
    }

    if ($null -eq $out -or "$out" -eq "") {
        Fail "Campo '$Field' veio vazio/nulo na licenca."
    }

    # UNICA escrita em stdout: o valor.
    [Console]::Out.Write("$out")
    exit 0
}
catch {
    Fail "$($_.Exception.Message)"
}
