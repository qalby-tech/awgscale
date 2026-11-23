# What building the app for Android needs, every part pinned by its exact version (and by checksum when
# it is downloaded here): Rust with the Android targets, JDK 17, Node, Go (for the tunnel's library,
# libawgscale.so), the Android SDK's command-line tools, one platform, one build-tools and one NDK.
# The stage `emulator` adds the emulator and one x86_64 system image, for app/tests/android.sh.
# Built and used by app/build-android.sh; nothing here is shipped.
FROM rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1 AS build

ARG JDK_URL=https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21.1%2B2/OpenJDK17U-jdk_x64_linux_hotspot_17.0.21.1_2.tar.gz
ARG JDK_SHA256=0b0ac5eab6db6abaec68c9913bcddb31321e5da411c45073d520d9c125e6f7b4
ARG NODE_VERSION=22.23.5
ARG NODE_SHA256=cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8
ARG GO_VERSION=1.25.14
ARG GO_SHA256=a21ae5633a269bcd7e90cf767e48225633795e99d831742cbf3397064fee7712
# cmdline-tools 23.0; its SHA-1 is the one Google's repository2-3.xml names (e025545c…)
ARG CMDLINE_TOOLS=commandlinetools-linux-16111833_latest.zip
ARG CMDLINE_TOOLS_SHA256=0877a1d048fe4a24efe2eff536ca4223f7adeb58648bb81909d33c446918cfa8
ARG PLATFORM_TOOLS=platform-tools_r37.0.3-linux.zip
ARG PLATFORM_TOOLS_SHA256=214548146cadb2ca3508143d8a1d4be4c59de5bb837253e3ede89ed74cf450d1
# sdkmanager checks what it installs against the SHA-1 of Google's repository
ARG SDK_PACKAGES="platforms;android-37.0 platforms;android-36 build-tools;36.0.0 ndk;29.0.14206865"

# Debian's packages as they were on one day (snapshot.debian.org), each by its exact version: the same
# image whenever it is built; apt checks them against the archive's signed lists
ARG DEBIAN_SNAPSHOT=20261122T000000Z
RUN printf '%s\n' \
      "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/$DEBIAN_SNAPSHOT bookworm bookworm-updates main" \
      "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/$DEBIAN_SNAPSHOT bookworm-security main" \
      > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends unzip=6.0-28+deb12u1 xz-utils=5.4.1-1+deb12u2 file=1:5.44-3 \
      python3=3.11.2-1+b1 ca-certificates=20250419~deb12u2 curl=7.88.1-10+deb12u15 \
 && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/opt/jdk ANDROID_HOME=/opt/android-sdk
ENV NDK_HOME=$ANDROID_HOME/ndk/29.0.14206865 ANDROID_NDK_HOME=$ANDROID_HOME/ndk/29.0.14206865
ENV PATH=/opt/jdk/bin:/opt/node/bin:/usr/local/go/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH

RUN set -eu; cd /tmp \
 && curl -fsSL -o jdk.tar.gz "$JDK_URL" && echo "$JDK_SHA256  jdk.tar.gz" | sha256sum -c - \
 && mkdir /opt/jdk && tar -xzf jdk.tar.gz -C /opt/jdk --strip-components=1 && rm jdk.tar.gz \
 && curl -fsSL -o node.tar.xz "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" \
 && echo "$NODE_SHA256  node.tar.xz" | sha256sum -c - \
 && mkdir /opt/node && tar -xJf node.tar.xz -C /opt/node --strip-components=1 && rm node.tar.xz \
 && curl -fsSL -o go.tar.gz "https://go.dev/dl/go$GO_VERSION.linux-amd64.tar.gz" && echo "$GO_SHA256  go.tar.gz" | sha256sum -c - \
 && tar -xzf go.tar.gz -C /usr/local && rm go.tar.gz \
 && curl -fsSL -o tools.zip "https://dl.google.com/android/repository/$CMDLINE_TOOLS" \
 && echo "$CMDLINE_TOOLS_SHA256  tools.zip" | sha256sum -c - \
 && mkdir -p $ANDROID_HOME/cmdline-tools && unzip -q tools.zip -d $ANDROID_HOME/cmdline-tools \
 && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest && rm tools.zip \
 && curl -fsSL -o platform-tools.zip "https://dl.google.com/android/repository/$PLATFORM_TOOLS" \
 && echo "$PLATFORM_TOOLS_SHA256  platform-tools.zip" | sha256sum -c - \
 && unzip -q platform-tools.zip -d $ANDROID_HOME && rm platform-tools.zip \
 && yes | sdkmanager --licenses >/dev/null \
 && sdkmanager --install $SDK_PACKAGES >/dev/null \
 && chmod -R a+rX $ANDROID_HOME
# the toolchain is the image's own (1.95.0): only the targets of the phones and the emulator are added
RUN rustup target add aarch64-linux-android x86_64-linux-android && chmod -R a+rwX /usr/local/rustup
WORKDIR /app

# The emulator, headless, with one system image (Android 15, Google APIs: `adb root` works there, which
# the tests need to put a network's profile in place and read the tunnel's interface)
FROM build AS emulator
ARG EMULATOR=emulator-linux_x64-16970787.zip
ARG EMULATOR_SHA256=a4405349d8a9c52136ab7861e5331dc40e85574bd91cb8889a298a2ff4c52e26
ARG SYSTEM_IMAGE=sys-img/google_apis/x86_64-35_r09.zip
ARG SYSTEM_IMAGE_SHA256=c67b9ba0ff5bc0eb6d046871bfa228af14d4d47b02f0cdae94f048e511b7566e
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpulse0=16.1+dfsg1-2+b1 libnss3=2:3.87.1-1+deb12u4 \
      libxcomposite1=1:0.4.5-1 libxcursor1=1:1.2.1-1 libxdamage1=1:1.1.6-1 libxi6=2:1.8-1+b1 libxtst6=2:1.2.3-1.1 \
      libxrandr2=2:1.5.2-2+b1 libasound2=1.2.8-1+b1 libdbus-1-3=1.14.10-1~deb12u1 libgl1=1.6.0-1 \
      libxkbfile1=1:1.1.0-1 libxkbcommon0=1.5.0-1 libx11-xcb1=2:1.8.4-2+deb12u2 libbsd0=0.11.7-2 \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; cd /tmp \
 && curl -fsSL -o emulator.zip "https://dl.google.com/android/repository/$EMULATOR" \
 && echo "$EMULATOR_SHA256  emulator.zip" | sha256sum -c - && unzip -q emulator.zip -d $ANDROID_HOME && rm emulator.zip \
 && curl -fsSL -o image.zip "https://dl.google.com/android/repository/$SYSTEM_IMAGE" \
 && echo "$SYSTEM_IMAGE_SHA256  image.zip" | sha256sum -c - \
 && mkdir -p $ANDROID_HOME/system-images/android-35/google_apis \
 && unzip -q image.zip -d $ANDROID_HOME/system-images/android-35/google_apis && rm image.zip \
 && chmod -R a+rX $ANDROID_HOME
ENV PATH=$ANDROID_HOME/emulator:$PATH
