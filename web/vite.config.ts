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
    proxy: {
      "/admin": "http://localhost:8080",
      "/observe": "http://localhost:8080",
      "/agent": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
    },
  },
});
