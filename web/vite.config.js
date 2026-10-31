import path from "path";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const ROOT_ENV_DIR = path.resolve(__dirname, "..");

export default defineConfig(({ mode }) => {
  // Vite only auto-loads .env into `process.env` for CLIENT code
  // (import.meta.env) - this config file itself sees plain Node
  // `process.env`, which does NOT include values from a .env file unless
  // loaded explicitly. That's why adding PY_BACKEND_PORT=... to the repo
  // root .env previously did nothing here: `process.env.PY_BACKEND_PORT`
  // was always undefined, silently falling back to the hardcoded default.
  // `loadEnv` reads the SAME root .env this project already treats as the
  // single source of truth (see envDir below) so a plain .env edit actually
  // takes effect, with no shell-exported env var required.
  const env = loadEnv(mode, ROOT_ENV_DIR, "");
  const PY_BACKEND_PORT = env.PY_BACKEND_PORT || "8000";

  return {
    plugins: [react(), tailwindcss()],
    // The repo root .env is the single source of truth for VITE_* vars (it's
    // also what docker-compose reads to build the production image). Vite
    // defaults envDir to this project's own folder, which silently ignored
    // that file for `npm run dev` / a native `vite build` — pointing it at
    // the monorepo root instead of duplicating the values into a web-local
    // .env fixes that at the source. Only VITE_-prefixed keys ever reach the
    // client bundle regardless of envDir, so the root .env's other (secret)
    // values are never exposed here.
    envDir: ROOT_ENV_DIR,
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      host: true,
      port: 3000,
      proxy: {
        "/py": `http://localhost:${PY_BACKEND_PORT}`,
        "/api": "http://localhost:4000",
      },
    },
    // `vite preview` (serving the production build locally) does NOT reuse
    // `server.proxy` - without its own `preview.proxy`, every /py/* fetch
    // falls through to the SPA fallback and gets back index.html with a
    // 200 OK instead of a real backend response, so failures look silently
    // successful instead of erroring. Mirrors `server.proxy` so `dev` and
    // `preview` behave identically.
    preview: {
      host: true,
      proxy: {
        "/py": `http://localhost:${PY_BACKEND_PORT}`,
        "/api": "http://localhost:4000",
      },
    },
  };
});
