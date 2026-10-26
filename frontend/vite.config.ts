import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server on 5177 proxies the API (FastAPI on 8077). In production FastAPI serves dist/.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5177, proxy: { "/api": "http://127.0.0.1:8077" } },
  build: { chunkSizeWarningLimit: 2000 },
});
