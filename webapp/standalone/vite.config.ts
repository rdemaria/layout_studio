import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

const sourceRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "./",
  plugins: [react()],
  resolve: {
    alias: [
      { find: "@source", replacement: sourceRoot },
      { find: "@", replacement: sourceRoot },
    ],
    dedupe: ["react", "react-dom"],
  },
  build: {
    modulePreload: false,
    outDir: "dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    sourcemap: false,
    target: "es2020",
    rollupOptions: {
      output: {
        codeSplitting: false,
      },
    },
  },
});
