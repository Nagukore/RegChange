import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Same-origin API in development, so the browser never needs CORS and the
      // SSE stream is not treated as cross-site.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Proxy must not buffer, or Server-Sent Events arrive in one lump at
        // the end of the stream instead of live.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache, no-transform";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // Recharts is large; splitting it out speeds the first paint of every
        // screen that does not draw a chart. Vite 8 types `manualChunks` as a
        // function, so the grouping is expressed per module id.
        manualChunks: (id) => {
          if (id.includes("node_modules/recharts") || id.includes("node_modules/d3")) {
            return "charts";
          }
          if (
            id.includes("node_modules/react") ||
            id.includes("node_modules/@tanstack")
          ) {
            return "vendor";
          }
          return undefined;
        },
      },
    },
  },
});
