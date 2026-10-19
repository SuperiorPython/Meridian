import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Port must match the origins allowed by MERIDIAN_CORS_ORIGINS on the API.
  server: { port: 5173, strictPort: true },
});
