import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const backend = process.env.VITE_BACKEND ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@contract": fileURLToPath(new URL("../contracts/ts/contract.ts", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/mock": backend,
      // Ссылки панели «Система»: Swagger, документация кода и метрики Prometheus бэкенда.
      "^/(docs|redoc|openapi\\.json|code-docs|metrics)": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
});