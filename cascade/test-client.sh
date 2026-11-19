#!/usr/bin/env bash
# Run ON THE OPERATOR MACHINE: connects a throwaway AmneziaWG client (container, isolated
# network namespace) with the given client config and checks the cascade end
# to end. Needs the `amnezia-awg2` image (docker save/load from an exit).
#   cascade/test-client.sh clients/my_phone.conf
set -euo pipefail
. "$(cd "$(dirname "$0")/.." && pwd)/site.sh"
site_env DOMAIN
CONF=${1:?client .conf}
N=awg-testclient
docker rm -f $N >/dev/null 2>&1 || true
docker run -d --name $N --cap-add NET_ADMIN --device /dev/net/tun --entrypoint sleep amnezia-awg2 600 >/dev/null
trap 'docker rm -f $N >/dev/null 2>&1' EXIT
DNS=$(awk -F' *= *' '/^DNS/{print $2}' "$CONF" | cut -d, -f1)
grep -v '^DNS' "$CONF" | sed 's|, ::/0||' | docker exec -i $N sh -c 'mkdir -p /etc/amnezia/amneziawg && umask 077 && cat > /etc/amnezia/amneziawg/awg0.conf'
docker exec -e DNS="$DNS" -e DOMAIN="$DOMAIN" $N sh -c '
apk add -q curl bind-tools 2>/dev/null
# the container cannot write this sysctl; the route setup works without it
sed -i "s/cmd sysctl -q net.ipv4.conf.all.src_valid_mark=1/true/" "$(command -v awg-quick)"
awg-quick up awg0 >/dev/null 2>&1; sleep 4
echo "nameserver $DNS" > /etc/resolv.conf
echo "handshake        : $(awg show awg0 latest-handshakes | awk "{print (\$2>0)?\"yes\":\"NO\"}")"
echo "listed site egress (chatgpt.com)  : $(curl -s -m 12 https://chatgpt.com/cdn-cgi/trace | grep -E "^(ip|loc)=" | tr "\n" " ")"
echo "unlisted site egress (ifconfig.me): $(curl -s -m 12 https://ifconfig.me)"
# one address from each Telegram network: MTProto servers do not speak TLS, so
# "connected" (a TCP handshake) is the success criterion, not an HTTP code
for ip in 149.154.175.50 149.154.167.51 91.108.56.130 91.108.4.200 91.105.192.100; do
    t=$(curl -s -m 6 -k -o /dev/null -w "%{time_connect}" https://$ip/ || true)
    [ "$t" = "0.000000" ] && t=$(curl -s -m 6 -k -o /dev/null -w "%{time_connect}" https://$ip/ || true)   # one retry: single servers are flaky
    echo "telegram $ip : $([ "$t" != "0.000000" ] && echo "connected in ${t}s" || echo "NO CONNECTION")"
done
echo "DNS normal       : $(dig +short +time=5 example.org | head -1)"
echo "DNS ad (0.0.0.0) : $(dig +short +time=5 ad.doubleclick.net | head -1)"
echo "youtube          : $(curl -s -m 12 -o /dev/null -w "%{http_code}" https://www.youtube.com/)"
curl -s -m 40 -o /dev/null -w "russian direct   : %{http_code} %{speed_download} B/s\n" https://mirror.yandex.ru/ubuntu/ls-lR.gz
curl -s -m 60 -o /dev/null -w "download cascade : %{speed_download} B/s\n" "https://speed.cloudflare.com/__down?bytes=50000000"
head -c 20000000 /dev/zero | curl -s -m 60 -o /dev/null -w "upload cascade   : %{speed_upload} B/s\n" -X POST --data-binary @- https://speed.cloudflare.com/__up
echo "panel via tunnel : $(curl -s -m 10 -o /dev/null -w "%{http_code} from %{local_ip}" https://$DOMAIN/)"
echo "private dst      : $(ping -c1 -W3 10.8.1.1 >/dev/null 2>&1 && echo REACHABLE || echo blocked)"'
