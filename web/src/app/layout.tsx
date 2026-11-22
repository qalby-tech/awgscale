import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "awgscale",
  robots: { index: false, follow: false },
  // The panel server puts the id of the build here when it hands the page out
  // (see lib/build.ts): a tab that outlived a deploy notices and reloads.
  other: { ui: "__UI_ID__" },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, colorScheme: "dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
