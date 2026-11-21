# app/tests/tunnel-windows.ps1 — the Windows tunnel on a GitHub Windows runner (an
# administrator's machine that is thrown away after the run). The service is the
# real one, registered with the service manager; the server is testserver in a
# stack of its own on this machine. The tunnel carries only 172.16.17.0/24, so the
# runner keeps its own connection; the full tunnel is switched on for seconds.
#   tunnel-windows.ps1 <dir with qalby.exe, testserver.exe, wintun.dll>
param([string]$Bin)
$ErrorActionPreference = "Continue"
$fails = 0
function Check($name, [scriptblock]$test) {
    try { $ok = & $test } catch { $ok = $false }
    if ($ok) { Write-Output "ok $name" } else { Write-Output "FAIL $name"; $script:fails++ }
}
function Qalby { & "$Bin\qalby.exe" @args 2>&1 | Out-String }

$work = New-Item -ItemType Directory -Force (Join-Path $env:RUNNER_TEMP "awgtest")
$address = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1).IPv4Address.IPAddress
& "$Bin\testserver.exe" make-confs $work "${address}:51820" "172.16.17.0/24" "-"
$server = Start-Process "$Bin\testserver.exe" -ArgumentList "serve-netstack", $work -PassThru -RedirectStandardOutput "$work\server.log" -NoNewWindow
New-NetFirewallRule -DisplayName "awgtest" -Direction Inbound -Protocol UDP -LocalPort 51820 -Action Allow | Out-Null
Start-Sleep 1

& "$Bin\qalby.exe" service install
Check "the service is registered and running" { (Get-Service awgscale).Status -eq "Running" }

$up = Qalby up "$work\client.conf"
Check "the tunnel is up after a handshake" { $up -match '"state":"up"' }
Check "the adapter is there" { (Get-NetAdapter -Name awgscale0).Status -eq "Up" }
Check "it has the device's address" { (Get-NetIPAddress -InterfaceAlias awgscale0 -AddressFamily IPv4).IPAddress -eq "172.16.17.15" }
Check "the app API answers through the tunnel" { (Invoke-RestMethod -TimeoutSec 10 http://172.16.17.1:8088/app/api/me).name -eq "test_laptop" }
Check "the server saw the device's tunnel address" { (Get-Content "$work\server.log") -match "get /app/api/me 172.16.17.15" }
Check "the runner still reaches the internet around the tunnel" { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 https://api.github.com).StatusCode -eq 200 }
if ($fails -gt 0) {
    Write-Output "── what the machine shows while the tunnel is up"
    Qalby status
    Get-NetIPInterface -InterfaceAlias awgscale0 | Format-Table -AutoSize | Out-String -Width 200
    Get-NetRoute -InterfaceAlias awgscale0 | Format-Table -AutoSize | Out-String -Width 200
    Find-NetRoute -RemoteIPAddress 172.16.17.1 -ErrorAction SilentlyContinue | Format-List | Out-String -Width 200
    Test-NetConnection 172.16.17.1 -Port 8088 -WarningAction SilentlyContinue | Format-List | Out-String -Width 200
    Get-Content "$work\server.log"
}

$down = Qalby down
Check "down: the adapter is gone" { -not (Get-NetAdapter -Name awgscale0 -ErrorAction SilentlyContinue) }

# the full tunnel, for seconds: everything goes to the adapter, the server itself around it; down whatever happens
(Get-Content "$work\client.conf") -replace '^AllowedIPs = .*', 'AllowedIPs = 0.0.0.0/0' | Set-Content "$work\full.conf"
$full = ""; $route = ""; $api = ""
try {
    $full = Qalby up "$work\full.conf"
    $route = (Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object { $_.RouteMetric + (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4).InterfaceMetric } | Select-Object -First 1).InterfaceAlias
    $api = try { (Invoke-RestMethod -TimeoutSec 10 http://172.16.17.1:8088/app/api/me).name } catch { "" }
} finally {
    Qalby down | Out-Null
}
Check "full tunnel: up" { $full -match '"state":"up"' }
Check "full tunnel: the best default route is the adapter" { $route -eq "awgscale0" }
Check "full tunnel: the server is still reached (its packets bound around the adapter)" { $api -eq "test_laptop" }
Check "after it the runner's internet is back" { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 https://api.github.com).StatusCode -eq 200 }

# the service killed: Windows restarts it, and nothing of the old tunnel is left
Qalby up "$work\client.conf" | Out-Null
Stop-Process -Name qalby -Force
Start-Sleep 5
Check "a killed service is started again by Windows" { (Get-Service awgscale).Status -eq "Running" }
Check "and no adapter is left behind" { -not (Get-NetAdapter -Name awgscale0 -ErrorAction SilentlyContinue) }
Check "the service answers again" { (Qalby status) -match '"state":"down"' }

& "$Bin\qalby.exe" service remove
Check "service remove takes it away" { -not (Get-Service awgscale -ErrorAction SilentlyContinue) }
Stop-Process -Id $server.Id -Force
if ($fails -eq 0) { Write-Output "app tunnel (windows): ok" } else { Write-Output "app tunnel (windows): $fails FAILED"; Get-Content "$work\server.log"; exit 1 }
