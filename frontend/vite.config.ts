import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

function apiProxyTarget(): string {
  const raw = process.env.DEMO_API_PROXY_TARGET;
  if (!raw) return "http://localhost:8000";

  const parsed = new URL(raw);
  const port = Number(parsed.port);
  if (
    parsed.protocol !== "http:"
    || !["127.0.0.1", "localhost"].includes(parsed.hostname)
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || !Number.isInteger(port)
    || port < 1024
    || port > 65535
  ) {
    throw new Error("DEMO_API_PROXY_TARGET must be loopback HTTP with an explicit non-privileged port");
  }
  return parsed.origin;
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget(),
        changeOrigin: true,
      },
    },
  },
});
