# One recipe for every Python service of the stack: the package with its locked
# dependencies (uv), on the same Ubuntu as the entry server — the tools a
# service drives (iptables, ipset, ip) must be the server's own versions.
#   docker build -f docker/Dockerfile --build-arg APT="iptables iproute2" -t awgscale-tunnel:local .
#   docker build -f docker/Dockerfile --target resolver -t awgscale-resolver:local .
#   docker build -f docker/Dockerfile --target panel -t awgscale-panel:local .
# APT: the system tools that service needs. The entry point is the service's
# module:  command: ["python", "-m", "awgscale.tunnel"]
# The layers are ordered so that the images share what is the same in all of
# them (Python, the locked dependencies): built once, and shipped once by
# `docker save` (stack/deploy.sh). The last stage is the plain service image:
# a build without --target gives that one.
# UBUNTU: the entry server's own release (stack/deploy.sh asks the server and
# passes it to docker/build.sh), so iptables in the image is the server's.
# The services run on uv's own Python build, the same on any release (Ubuntu
# 24.04 ships 3.12); the release's own python3 is there too, as the server's
# bare one (the boot gate and the deploy's probes run on that, tests/stack.sh).
ARG UBUNTU=24.04
FROM ubuntu:${UBUNTU} AS python
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3 ca-certificates >/dev/null \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed UV_LINK_MODE=copy
# the release named in pyproject.toml (requires-python); uv pinned above pins its build
RUN uv python install 3.14
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ARG APT=""
RUN if [ -n "$APT" ]; then apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $APT >/dev/null \
 && apt-get clean && rm -rf /var/lib/apt/lists/*; fi
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# The resolver's keeper carries AdGuard Home itself. The binary is taken from
# AdGuard's own image and is pinned to the version whose config and data are on
# the server: a newer one rewrites the config to a newer schema, and there is no
# way back from that. The deploy refuses to start the container if the versions differ.
FROM adguard/adguardhome:v0.107.80 AS adguard
FROM python AS resolver
COPY --from=adguard /opt/adguardhome/AdGuardHome /opt/adguardhome/AdGuardHome

# The panel carries the invitation page and the typefaces; the built page
# (web/out) is not in the image: it is a directory mounted at /app/web.
FROM python AS panel
COPY panel/invite.html /app/invite.html
COPY panel/fonts /app/fonts

FROM python AS service
