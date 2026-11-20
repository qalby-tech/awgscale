#!/usr/bin/env bash
# =============================================================================
# stack/owner-code.sh — run ON THE OPERATOR MACHINE. Prints a one-time link (and
# its QR code) that makes the device that opens it in the awgscale app an owner
# of the network: it gets a user of its own and may run the network from the app.
#
#   stack/owner-code.sh             a new code; an earlier one nobody used stops working
#   stack/owner-code.sh --if-none   only when the network has no owner yet (the deploy)
#
# The code works for one device and 15 minutes. The link carries the secret:
# it is printed here and nowhere else, and is not kept.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=../site.sh
. "$HERE/site.sh"
site_env DOMAIN
SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes)

case "${1:-}" in
    "") condition= ;;
    --if-none) condition=if-none ;;
    *) echo "usage: $0 [--if-none]" >&2; exit 2 ;;
esac

answer=$("${SSH[@]}" entry "docker exec vpn-agent python -m awgscale.agent owner-code $condition" || true)
read -r ok token already < <(python3 -c '
import json, sys
try:
    answer = json.loads(sys.argv[1])
except ValueError:
    answer = {}
token = answer.get("token", "")
ok = answer.get("ok") is True and isinstance(token, str) and token.replace("-", "").replace("_", "").isalnum() and len(token) == 22
print("yes" if ok else "no", token if ok else "-", "yes" if answer.get("already") else "no")
' "$answer")

if [ "$ok" != yes ]; then
    if [ "$already" = yes ]; then
        echo "The network has an owner already: a new code is made by stack/owner-code.sh"
        exit 0
    fi
    echo "no owner code: $(printf '%s' "$answer" | cut -c1-200)" >&2
    exit 1
fi

link="awgscale://join?h=$DOMAIN&c=$token"
echo
echo "Connect your device: install the awgscale app and scan this code"
echo "(or open the link on the phone). One device, 15 minutes; it becomes the owner."
echo
(cd "$HERE" && uv run -q python -c 'import segno, sys; segno.make(sys.argv[1], error="m").terminal(compact=True)' "$link") || true
echo
echo "$link"
echo
