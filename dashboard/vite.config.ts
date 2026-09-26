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
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
});