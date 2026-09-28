# =============================================================================
# discover_license.ps1  -  Licenca do Veeam B&R (somente leitura)
#
# Mostra os campos uteis da licenca e faz dump completo das propriedades
# (para confirmarmos os nomes exatos nesta versao do Veeam).
# Nao altera nada. Nao instala/aplica licenca.
#
# Uso: rodar no servidor VBR.  .\discover_license.ps1
# =============================================================================

try { Import-Module Veeam.Backup.PowerShell -ErrorAction SilentlyContinue } catch {}
try { Add-PSSnapin VeeamPSSnapIn -ErrorAction SilentlyContinue } catch {}

$lic = $null
try { $lic = Get-VBRInstalledLicense } catch { Write-Host "Falha em Get-VBRInstalledLicense: $($_.Exception.Message)" -ForegroundColor Red; return }
if (-not $lic) { Write-Host "Nenhuma licenca retornada." -ForegroundColor Yellow; return }

function Show { param($label,$val) Write-Host ("  {0,-26}: {1}" -f $label, $val) }

Write-Host "==================== LICENCA VEEAM ====================" -ForegroundColor Cyan
Show "Status"               $lic.Status
Show "Tipo"                 $lic.Type
Show "Edition"             ($lic.Edition)
Show "Licenciado para"      $lic.LicensedTo
Show "Support ID"           $lic.SupportId
Show "Expiracao licenca"    $lic.ExpirationDate
Show "Expiracao suporte"    $lic.SupportExpirationDate
Show "Auto update"          $lic.AutoUpdateEnabled

# Instancias (licenciamento por instancia/VUL)
$inst = $null; try { $inst = $lic.InstanceLicenseSummary } catch {}
if ($inst) {
    Write-Host "  -- Instancias (VUL) --" -ForegroundColor DarkGray
    Show "  Licenciadas"   $inst.LicensedInstancesNumber
    Show "  Usadas"        $inst.UsedInstancesNumber
    Show "  Restantes"     $inst.RemainingInstancesNumber
}

# Sockets (licenciamento por socket)
$sock = $null; try { $sock = $lic.SocketLicenseSummary } catch {}
if ($sock) {
    Write-Host "  -- Sockets --" -ForegroundColor DarkGray
    Show "  Licenciados"   $sock.LicensedSocketsNumber
    Show "  Usados"        $sock.UsedSocketsNumber
    Show "  Restantes"     $sock.RemainingSocketsNumber
}

# Capacity (NAS / capacity-based, se houver)
$cap = $null; try { $cap = $lic.CapacityLicenseSummary } catch {}
if ($cap) {
    Write-Host "  -- Capacity --" -ForegroundColor DarkGray
    foreach ($p in ($cap.PSObject.Properties | Sort-Object Name)) { Show ("  " + $p.Name) $p.Value }
}

Write-Host ""
Write-Host "==================== DUMP COMPLETO (todas as propriedades) ====================" -ForegroundColor Magenta
foreach ($p in ($lic.PSObject.Properties | Sort-Object Name)) {
    $val = $null; try { $val = $p.Value } catch { $val = "<erro>" }
    $tn = if ($null -ne $val) { $val.GetType().Name } else { "null" }
    # objetos complexos: expande 1 nivel
    $isSimple = ($null -eq $val) -or ($val -is [string]) -or ($val -is [bool]) -or ($val -is [int]) `
                -or ($val -is [long]) -or ($val -is [double]) -or ($val -is [datetime]) -or ($val.GetType().IsEnum)
    if ($isSimple) {
        Write-Host ("{0} = {1}   [{2}]" -f $p.Name, $val, $tn)
    } else {
        Write-Host ("{0}:  [{1}]" -f $p.Name, $tn) -ForegroundColor Green
        try {
            foreach ($sp in ($val.PSObject.Properties | Sort-Object Name)) {
                $sv = $null; try { $sv = $sp.Value } catch {}
                Write-Host ("    {0} = {1}" -f $sp.Name, $sv)
            }
        } catch {}
    }
}

Write-Host ""
Write-Host "FIM. Cole a saida no chat se quiser que eu transforme num coletor NDJSON." -ForegroundColor Cyan
