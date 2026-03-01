import fs from "fs";
import path from "path";
import type { NextConfig } from "next";

// Load .env from the monorepo root so AWS_S3_BUCKET etc. are available server-side.
// Next.js only auto-loads .env from its own directory (packages/web/), but the
// monorepo keeps shared secrets in the repo root .env.
const rootEnvPath = path.resolve(__dirname, "../../.env");
if (fs.existsSync(rootEnvPath)) {
  for (const line of fs.readFileSync(rootEnvPath, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx < 0) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const value = trimmed.slice(eqIdx + 1).trim();
    // Don't overwrite existing env vars (e.g. from shell or .env.local)
    if (!process.env[key]) {
      process.env[key] = value;
    }
  }
}

const nextConfig: NextConfig = {
  devIndicators: false,
  serverExternalPackages: ["@duckdb/node-api", "@duckdb/node-bindings"],
};

export default nextConfig;
