import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const frontendDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: "./",

  build: {
    outDir: resolve(frontendDir, "../../static/dist/react"),
    emptyOutDir: true,

    rollupOptions: {
            input: {
        trash: resolve(frontendDir, "src/pages/trash/main.jsx"),
        customers: resolve(frontendDir, "src/pages/customers/main.jsx"),
        vehicles: resolve(frontendDir, "src/pages/vehicles/main.jsx"),
      },

      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/chunks/[name]-[hash].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
}); 