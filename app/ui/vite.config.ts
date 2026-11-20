// The app's interface: a plain single page that Tauri shows in its window.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5174, strictPort: true },
  build: { outDir: "dist", target: "es2021", sourcemap: false },
});
