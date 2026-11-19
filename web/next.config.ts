import type { NextConfig } from "next";

// The panel is a client application behind a cookie session: it is built into
// plain files (out/) that the panel server hands out next to its /api.
const config: NextConfig = {
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
};

export default config;
