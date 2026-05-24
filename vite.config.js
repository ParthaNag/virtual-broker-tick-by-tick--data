import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const mockServerUrl = process.env.VITE_MOCK_SERVER_URL || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: mockServerUrl,
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
