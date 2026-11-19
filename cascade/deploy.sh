#!/usr/bin/env bash
# Run ON THE OPERATOR MACHINE, once, on a NEW entry server before the first stack/deploy.sh:
# the few things the stack expects to find on the host. Safe to run again.
#   - the packages the rules need (ipset, conntrack);
#   - /root/awg/cascade/cascade.conf, SEEDED only where there is none. After that
#     the database is where the settings live and the agent compiles the file
#     from it (docker exec vpn-agent python -m awgscale.agent cascade-get | cascade-set);
#   - fail closed on the host's own awg-quick unit (the way back for the users'
#     tunnel, see stack/switch-tunnel.sh): no tunnel unless the rules were applied.
# The rules themselves, the watchdog and the weekly refreshes are the container
# vpn-watch and the boot gate — both installed by stack/deploy.sh watch.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes)
"${SSH[@]}" entry 'mkdir -p /root/awg/cascade && { command -v ipset >/dev/null && command -v conntrack >/dev/null; } || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ipset conntrack curl >/dev/null; }'
# seeded only where there is none; the seed leaves new-server behind, so that the first
# stack/deploy.sh starts the settings store empty (store-init --fresh)
"${SSH[@]}" entry '[ -s /root/awg/cascade/cascade.conf ]' || { scp -q -F "$HERE/ssh_config" "$HERE/cascade/cascade.conf" entry:/root/awg/cascade/ && "${SSH[@]}" entry 'touch /root/awg/cascade/new-server'; }
"${SSH[@]}" entry 'chmod 600 /root/awg/cascade/cascade.conf
mkdir -p /etc/systemd/system/awg-quick@awg0.service.d && printf "[Unit]\nRequires=cascade-apply.service\nAfter=cascade-apply.service\n" > /etc/systemd/system/awg-quick@awg0.service.d/cascade.conf
systemctl daemon-reload
echo "host prepared; now run stack/deploy.sh (see its header for the services to name)"'
