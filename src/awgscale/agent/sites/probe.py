"""Does a site open — directly from this server, and through each exit."""
from __future__ import annotations

import re

from awgscale.core.ashell import TIMED_OUT, AsyncRunner
from awgscale.core.net import HTTPS_PORT

from .models import ProbeState, Seen, Via, Way

PROBE_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
# the page of a site that may be asked for: a path and a query, nothing a shell or a header could trip on
#     /status?x=1
PROBE_PATH_RE = re.compile(
    r"""
    ^(?P<path>/[A-Za-z0-9._~%!$&'()*+,;=:@/?-]{0,600})\Z
    """,
    re.VERBOSE,
)
# an exit in cascade.conf:   "1:203.0.113.10:10.77.1.2:nl:2"   or, without a name,   "2:203.0.113.20:10.77.2.2:"
EXIT_RE = re.compile(
    r"""
    "(?P<index>\d+):            # "1:
    (?P<address>[^:"]+):        # 203.0.113.10:   the exit's public address
    (?P<tunnel_ip>[^:"]+):      # 10.77.1.2:      the entry's address on the tunnel to it
    (?P<name>[^:"]*)            # nl              may be empty
    :?(?P<weight>\d*)"          # :2"
    """,
    re.VERBOSE,
)
PROBE_TIMEOUT = 14.0
DIRECT_NAME = "Напрямую"
CURL_TIMED_OUT = 28
# why curl gave up, in words for the admin
CURL_REASONS = {28: "не отвечает", 7: "соединение отклонено", 35: "соединение обрывается", 56: "соединение обрывается", 60: "ошибка сертификата"}
REFUSING_STATUSES = (403, 451)
FIRST_SERVER_ERROR = 500


def ways_of(conf_text: str) -> list[Way]:
    """Every way a site can be opened: directly, and through each exit the settings name."""
    ways = [Way(via=Via.DIRECT, name=DIRECT_NAME, source=None)]
    for exit_line in EXIT_RE.finditer(conf_text):
        ways.append(Way(via=Via.EXIT, name=exit_line["name"] or exit_line["address"], source=exit_line["tunnel_ip"]))
    return ways


def seen_of(status: int, milliseconds: int, curl_code: int) -> Seen:
    """curl's answer, as what a visitor would see."""
    if 200 <= status < 400:
        return Seen(state=ProbeState.OK, code=status, ms=milliseconds, text="открывается")
    if status in REFUSING_STATUSES:
        return Seen(state=ProbeState.REFUSED, code=status, ms=milliseconds, text=f"сайт отказывает ({status})")
    if status:
        state = ProbeState.OK if status < FIRST_SERVER_ERROR else ProbeState.ERROR
        return Seen(state=state, code=status, ms=milliseconds, text=f"отвечает ({status})")
    return Seen(state=ProbeState.DEAD, code=0, ms=milliseconds, text=CURL_REASONS.get(curl_code, "не открывается"))


class CurlProber:
    def __init__(self, run: AsyncRunner) -> None:
        self.__run = run

    async def probe(self, host: str, address: str, source: str | None, path: str = "/") -> Seen:
        """Opens https://host/ once, from this server directly or (source = the
        entry's address on a tunnel) through one exit."""
        command = [
            "curl", "-4", "-s", "-o", "/dev/null", "-m", "9", "--connect-timeout", "5", "--proto", "=https", "-A", PROBE_AGENT,
            "--resolve", f"{host}:{HTTPS_PORT}:{address}", "-w", "%{http_code} %{time_total}",
        ]
        if source:
            command += ["--interface", source]
        done = await self.__run([*command, f"https://{host}{path}"], None, PROBE_TIMEOUT)
        if done.code == TIMED_OUT:
            return seen_of(0, 0, CURL_TIMED_OUT)
        status_text, _, seconds_text = done.out.strip().partition(" ")
        try:
            status = int(status_text or 0)
            milliseconds = int(float(seconds_text or 0) * 1000)
        except ValueError:
            return seen_of(0, 0, CURL_TIMED_OUT)
        return seen_of(status, milliseconds, done.code)
