# What building the app for Linux needs: Rust, Node and the system's web view (WebKitGTK).
# Built and used by app/build.sh; nothing here is shipped.
FROM rust:1.95-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev libayatana-appindicator3-dev librsvg2-dev \
      libssl-dev patchelf file xdg-utils ca-certificates curl \
      xvfb xdotool imagemagick fonts-dejavu iproute2 nftables procps \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && rustup component add clippy rustfmt
WORKDIR /app
# The pictures of the screens (app/tests/screens.sh) find what they press by its text, through the web
# view's own WebDriver
RUN apt-get update \
 && apt-get install -y --no-install-recommends webkit2gtk-driver \
 && rm -rf /var/lib/apt/lists/*
