import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy API calls to the FastAPI backend so the SPA is same-origin in dev
// (no CORS needed) and paths match production.
const API = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/simulations": API,
      "/teams": API,
      "/ingestion": API,
      "/players": API,
    },
  },
});
