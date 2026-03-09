import { access } from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { cached } from "./cache";
import { getTrajectorySandboxMeta } from "./data";

const execFileAsync = promisify(execFile);
const SCRIPT_PATH = path.join(
  process.cwd(),
  "scripts",
  "check-sandbox-status.py",
);
const REPO_PYTHON = path.resolve(
  process.cwd(),
  "..",
  "..",
  ".venv",
  "bin",
  "python3",
);
const STATUS_CACHE_TTL_MS = 15_000;

export type SandboxLivenessResult = {
  running: boolean;
  reason?: string;
  exitCode?: number;
  error?: string;
};

async function resolvePythonExecutable(): Promise<string> {
  try {
    await access(REPO_PYTHON);
    return REPO_PYTHON;
  } catch {
    return "python3";
  }
}

/** Resolve true sandbox-backed liveness for a trajectory. */
export async function getTrajectorySandboxLiveness(
  project: string,
  trajectoryId: string,
): Promise<SandboxLivenessResult> {
  const meta = await getTrajectorySandboxMeta(trajectoryId, project);
  if (!meta) {
    return {
      running: false,
      reason: "trajectory_not_found",
    };
  }

  if (meta.sessionEndReason) {
    return {
      running: false,
      reason: meta.sessionEndReason,
    };
  }

  if (!meta.sandboxId || !meta.sandboxProvider) {
    return {
      running: false,
      reason: "no_sandbox_info",
    };
  }

  return cached(
    `sandbox-status:${meta.sandboxId}`,
    async () => {
      const pythonExecutable = await resolvePythonExecutable();
      const { stdout } = await execFileAsync(
        pythonExecutable,
        [SCRIPT_PATH, meta.sandboxProvider ?? "", meta.sandboxId ?? ""],
        { timeout: 10_000 },
      );
      return JSON.parse(stdout.trim()) as SandboxLivenessResult;
    },
    STATUS_CACHE_TTL_MS,
  );
}
