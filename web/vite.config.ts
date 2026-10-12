import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI under /app/ in production; hash routing means no
// server-side fallback is needed. `npm run dev` proxies API calls to :8080.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  define: {
    __BUILD_TIME__: JSON.stringify(new Date().toISOString().slice(0, 16).replace("T", " ")),
  },
  server: {
    port: 5173,
    // No changeOrigin: the server checks a cookie-carrying write's Origin
    // against the Host it arrived on, and keeping the browser's Host is what
    // lets localhost:5173 count as this install's own pages in development.
    proxy: {
      "/auth": "http://localhost:8080",
      "/admin": "http://localhost:8080",
      "/observe": "http://localhost:8080",
      "/agent": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
});
