import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";
export default defineConfig({
    plugins: [react(), tailwindcss()],
    server: {
        host: "127.0.0.1",
        port: 5173,
        proxy: {
            "/api": {
                target: apiProxyTarget,
                changeOrigin: true,
                ws: false,
                configure: (proxy) => {
                    proxy.on("proxyRes", (proxyRes) => {
                        proxyRes.headers["cache-control"] = "no-cache";
                    });
                },
            },
        },
    },
    test: {
        environment: "jsdom",
        setupFiles: ["./tests/setup.ts"],
    },
});
