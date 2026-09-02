import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { VitePWA } from "vite-plugin-pwa";
import { fileURLToPath, URL } from "node:url";
import packageJson from "./package.json" with { type: "json" };

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/" : "/",
  define: {
    __APP_VERSION__: JSON.stringify(
      process.env.BOTICA_VERSION?.trim() || packageJson.version,
    ),
  },
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
      routeFileIgnorePattern: "\\.test\\.[jt]sx?$",
    }),
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "prompt",
      injectRegister: null,
      workbox: {
        // The shell and the two self-hosted faces, and nothing else.
        globPatterns: ["**/*.{js,css,html,woff2,svg,ico}"],
        // Django serves the built assets under /static/ and the worker itself
        // from the origin root, so that its scope covers every route rather
        // than /static/ alone.
        modifyURLPrefix: { "": "/static/" },
        navigateFallback: "/static/index.html",
        // THE rule: the service worker never caches an API response. Not
        // /api/me, not /api/locations, not one byte under /api/. The local
        // store S2 builds is the offline data layer, and two caches are two
        // truths — the specific failure being a stock level served from an
        // HTTP cache to a screen a cashier is about to sell from.
        navigateFallbackDenylist: [/^\/api\//, /^\/admin\//, /^\/_allauth\//],
        runtimeCaching: [],
        // A new shell version activates on the next full load, never
        // mid-session: swapping the running application under an unsent sale is
        // precisely what S2's queue and S4's ticket cannot tolerate.
        skipWaiting: false,
        clientsClaim: false,
        cleanupOutdatedCaches: true,
      },
      manifest: {
        name: "Botica",
        short_name: "Botica",
        lang: "es-CO",
        start_url: "/",
        display: "standalone",
        background_color: "#fbfbfb",
        theme_color: "#fbfbfb",
        icons: [],
      },
    }),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(
      ["/api", "/_allauth", "/admin"].map((path) => [
        path,
        process.env.BOTICA_API_ORIGIN || "http://127.0.0.1:8000",
      ]),
    ),
  },
}));
