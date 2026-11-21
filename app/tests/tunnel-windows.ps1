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
$env:TESTSERVER_VERBOSE = "1"
$server = Start-Process "$Bin\testserver.exe" -ArgumentList "serve-netstack", $work -PassThru -RedirectStandardOutput "$work\server.log" -RedirectStandardError "$work\server-errors.log" -NoNewWindow
New-NetFirewallRule -DisplayName "awgtest" -Direction Inbound -Protocol UDP -LocalPort 51820 -Action Allow | Out-Null
Start-Sleep 1

# the service, telling everything into a log of the test (its environment, for this run only)
& "$Bin\qalby.exe" service install
Check "the service is registered and running" { (Get-Service awgscale).Status -eq "Running" }
Stop-Service awgscale
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\awgscale" -Name Environment -Type MultiString -Value @("AWGSCALE_VERBOSE=1", "AWGSCALE_LOG=$work\daemon.log")
Start-Service awgscale
Start-Sleep 1

$up = Qalby up "$work\client.conf"
Check "the tunnel is up after a handshake" { $up -match '"state":"up"' }
Check "the adapter is there" { (Get-NetAdapter -Name awgscale0).Status -eq "Up" }
Check "it has the device's address" { (Get-NetIPAddress -InterfaceAlias awgscale0 -AddressFamily IPv4).IPAddress -eq "172.16.17.15" }
Check "the app API answers through the tunnel" { (Invoke-RestMethod -TimeoutSec 10 http://172.16.17.1:8088/app/api/me).name -eq "test_laptop" }
Check "the server saw the device's tunnel address" { (Get-Content "$work\server.log") -match "get /app/api/me 172.16.17.15" }
Check "the runner still reaches the internet around the tunnel" { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 http://www.msftconnecttest.com/connecttest.txt).Content -match "Microsoft Connect Test" }
if ($fails -gt 0) {
    Write-Output "── what the machine shows while the tunnel is up"
    Qalby status
    Get-NetIPInterface -InterfaceAlias awgscale0 -ErrorAction SilentlyContinue | Format-Table -AutoSize | Out-String -Width 200
    Get-NetRoute -InterfaceAlias awgscale0 -ErrorAction SilentlyContinue | Format-Table -AutoSize | Out-String -Width 200
    Get-NetAdapterStatistics -Name awgscale0 -ErrorAction SilentlyContinue | Format-List SentBytes,SentUnicastPackets,ReceivedBytes,ReceivedUnicastPackets,OutboundDiscardedPackets,OutboundPacketErrors | Out-String -Width 200
    Get-NetIPAddress -InterfaceAlias awgscale0 -AddressFamily IPv4 -ErrorAction SilentlyContinue | Format-List IPAddress,PrefixLength,AddressState,SkipAsSource | Out-String -Width 200
    Find-NetRoute -RemoteIPAddress 172.16.17.1 -ErrorAction SilentlyContinue | Format-List | Out-String -Width 200
    Test-NetConnection 172.16.17.1 -Port 8088 -WarningAction SilentlyContinue | Format-List | Out-String -Width 200
    Get-Content "$work\server.log"
    Get-Content "$work\server-errors.log" | Select-String -NotMatch "Routine|UAPI|Updating" | Select-Object -Last 25
    Write-Output "── the client's own log"
    Get-Content "$work\daemon.log" | Select-String -NotMatch "Routine|UAPI|Updating" | Select-Object -Last 40
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
    $local = (Find-NetRoute -RemoteIPAddress 10.96.0.10 -ErrorAction SilentlyContinue | Select-Object -Last 1).InterfaceAlias
    $own = (Find-NetRoute -RemoteIPAddress 172.16.17.9 -ErrorAction SilentlyContinue | Select-Object -Last 1).InterfaceAlias
} finally {
    Qalby down | Out-Null
}
Check "full tunnel: a local network goes around it (by default)" { $local -and $local -ne "awgscale0" }
Check "full tunnel: the network's own subnet stays in it" { $own -eq "awgscale0" }
Check "full tunnel: up" { $full -match '"state":"up"' }
Check "full tunnel: the best default route is the adapter" { $route -eq "awgscale0" }
Check "full tunnel: the server is still reached (its packets bound around the adapter)" { $api -eq "test_laptop" }
Check "after it the runner's internet is back" { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 http://www.msftconnecttest.com/connecttest.txt).Content -match "Microsoft Connect Test" }

# what the panel's configs have: the network's DNS (kept inside by the WFP rules), and the kill
# switch. Each tried for seconds, down whatever happens.
$ethernet = $address
function OutsideAnswers {
    # a TCP connection to 1.1.1.1:443 from the Ethernet address itself: it leaves around the tunnel or
    # not at all (not DNS: port 53 outside is closed whenever the network's DNS is in use)
    $tcp = New-Object System.Net.Sockets.TcpClient (New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse($ethernet), 0))
    try { return $tcp.ConnectAsync("1.1.1.1", 443).Wait(4000) -and $tcp.Connected } catch { return $false } finally { $tcp.Close() }
}
function ForeignDNSAnswers { try { [bool](Resolve-DnsName example.com -Server 8.8.8.8 -DnsOnly -QuickTimeout -ErrorAction Stop) } catch { $false } }
(Get-Content "$work\client.conf") -replace '^MTU = ', "DNS = 172.16.17.1`nMTU = " | Set-Content "$work\dns.conf"
(Get-Content "$work\dns.conf") -replace '^AllowedIPs = .*', 'AllowedIPs = 0.0.0.0/0' | Set-Content "$work\full-dns.conf"
$first = ""; $second = ""; $foreign = $true; $baseline = $false; $killed = ""; $around = $true; $inside = ""
try {
    $first = Qalby up "$work\dns.conf"
    $foreign = ForeignDNSAnswers
    Qalby down | Out-Null
    $second = Qalby up "$work\dns.conf"
    Qalby down | Out-Null
    Qalby up "$work\full-dns.conf" | Out-Null
    $baseline = OutsideAnswers
    Qalby down | Out-Null
    $env:AWGSCALE_KILL_SWITCH = "1"
    $killed = Qalby up "$work\full-dns.conf"
    Remove-Item Env:\AWGSCALE_KILL_SWITCH
    $around = OutsideAnswers
    $inside = try { (Invoke-RestMethod -TimeoutSec 10 http://172.16.17.1:8088/app/api/me).name } catch { "" }
} finally {
    Remove-Item Env:\AWGSCALE_KILL_SWITCH -ErrorAction SilentlyContinue
    Qalby down | Out-Null
}
Write-Output "  the service said: with DNS: $first | again: $second | with the kill switch: $killed"
Check "with the network's DNS: up, and the status says the DNS is the network's" { $first -match '"state":"up"' -and $first -match '"dns":"set"' }
Check "with it no other DNS server is asked" { -not $foreign }
Check "the tunnel and its rules come up a second time" { $second -match '"state":"up"' -and $second -match '"dns":"set"' }
Check "without the kill switch a packet may leave around the tunnel" { $baseline }
Check "with the kill switch: up" { $killed -match '"state":"up"' }
Check "with the kill switch it may not" { -not $around }
Check "with the kill switch the tunnel still works" { $inside -eq "test_laptop" }
Check "after them other DNS servers answer again" { ForeignDNSAnswers }
Check "and the runner's internet is back" { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 http://www.msftconnecttest.com/connecttest.txt).Content -match "Microsoft Connect Test" }

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
