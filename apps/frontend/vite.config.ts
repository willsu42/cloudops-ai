/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the browser calls the Vite origin and Vite forwards /health to
// the API, so no CORS configuration is needed.
const apiTarget = `http://127.0.0.1:${process.env.API_HOST_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
