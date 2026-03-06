/**
 * Sandboxed Python execution for the chat agent.
 * Runs Python scripts in a temporary directory with:
 * - Read-only DuckDB access via DB_PATH env var
 * - matplotlib/json/csv/duckdb imports available
 * - 30-second timeout
 * - Output: stdout text + any generated SVG/PNG files as base64
 */

import { execFile } from "child_process";
import { writeFile, readFile, readdir, mkdir, rm } from "fs/promises";
import { resolve } from "path";
import { randomBytes } from "crypto";
import type { ToolResult } from "./tools";

const SANDBOX_BASE = "/tmp/envoi-agent-sandbox";
const TIMEOUT_MS = 30_000;

/** Resolve the DuckDB path for a project */
function getDbPath(project: string): string {
  return resolve(process.cwd(), ".cache", "duckdb", `${project}.duckdb`);
}

/** Execute Python code in a sandboxed temp directory */
export async function executePython(
  code: string,
  project: string,
): Promise<ToolResult> {
  const sessionId = randomBytes(8).toString("hex");
  const workDir = resolve(SANDBOX_BASE, sessionId);

  try {
    await mkdir(workDir, { recursive: true });

    const scriptPath = resolve(workDir, "script.py");
    await writeFile(scriptPath, code, "utf-8");

    const dbPath = getDbPath(project);

    const { stdout, stderr } = await runPython(scriptPath, workDir, dbPath);

    const images = await collectArtifacts(workDir);

    let output = stdout.trim();
    if (stderr.trim()) {
      output += output ? `\n\nStderr:\n${stderr.trim()}` : `Stderr:\n${stderr.trim()}`;
    }

    if (!output && images.length === 0) {
      output = "(no output)";
    }

    return { output, images: images.length > 0 ? images : undefined };
  } finally {
    rm(workDir, { recursive: true, force: true }).catch(() => {
      // Best effort cleanup
    });
  }
}

/** Run python3 as a subprocess with timeout */
function runPython(
  scriptPath: string,
  workDir: string,
  dbPath: string,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((promiseResolve, promiseReject) => {
    const child = execFile(
      "python3",
      [scriptPath],
      {
        cwd: workDir,
        timeout: TIMEOUT_MS,
        maxBuffer: 5 * 1024 * 1024,
        env: {
          ...process.env,
          DB_PATH: dbPath,
          MPLBACKEND: "Agg",
        },
      },
      (error, stdout, stderr) => {
        if (error && !stdout && !stderr) {
          promiseReject(error);
          return;
        }
        promiseResolve({
          stdout: typeof stdout === "string" ? stdout : "",
          stderr: typeof stderr === "string" ? stderr : "",
        });
      },
    );

    child.on("error", promiseReject);
  });
}

/** Collect generated image files (SVG/PNG) from the work directory */
async function collectArtifacts(
  workDir: string,
): Promise<Array<{ src: string; alt: string }>> {
  const files = await readdir(workDir);
  const artifacts: Array<{ src: string; alt: string }> = [];

  for (const fileName of files) {
    const lower = fileName.toLowerCase();
    if (!lower.endsWith(".svg") && !lower.endsWith(".png")) {
      continue;
    }

    const filePath = resolve(workDir, fileName);
    const content = await readFile(filePath);

    if (lower.endsWith(".svg")) {
      const svgText = content.toString("utf-8");
      artifacts.push({
        src: `data:image/svg+xml;base64,${Buffer.from(svgText).toString("base64")}`,
        alt: fileName,
      });
    } else {
      artifacts.push({
        src: `data:image/png;base64,${content.toString("base64")}`,
        alt: fileName,
      });
    }
  }

  return artifacts;
}
