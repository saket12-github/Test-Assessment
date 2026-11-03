import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API calls to FastAPI so the session cookie stays same-origin.
const backend = process.env.BACKEND_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": backend, "/webhook": backend, "/health": backend },
  },
});
