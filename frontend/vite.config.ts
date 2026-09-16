import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // A GitHub Pages project site is served from /<repo>/, not from the domain root, so
  // asset URLs need that prefix. Set only for the Pages build: the local build and the
  // dev server are served from the root and would break with it.
  base: process.env.VITE_BASE ?? "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The API is reached through a same-origin /api prefix in development, so the
    // frontend never needs to know the backend host and no CORS split exists.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
