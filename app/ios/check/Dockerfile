# What app/ios/check.sh needs on Linux to type-check the app for iOS: Rust with the iOS targets, and
# clang for the stand-ins of Apple's tools next to this file (no Apple SDK: C is not really compiled).
FROM rust:1.95-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends clang \
 && rm -rf /var/lib/apt/lists/* \
 && rustup target add aarch64-apple-ios aarch64-apple-ios-sim \
 && rustup component add clippy rustfmt
COPY xcrun stub-cc /usr/local/bin/
