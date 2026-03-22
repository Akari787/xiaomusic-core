import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    base: "/webui/",
    publicDir: false,
    build: {
        outDir: "static",
        emptyOutDir: false,
        sourcemap: false,
    },
});
