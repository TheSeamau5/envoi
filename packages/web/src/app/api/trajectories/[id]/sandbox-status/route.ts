/**
 * GET /api/trajectories/:id/sandbox-status
 *
 * Checks whether a trajectory's sandbox is still running by querying
 * the sandbox provider (Modal or E2B). Returns { running: boolean }.
 *
 * This is the source of truth for liveness — no heuristics needed.
 */

import { NextRequest, NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { getTrajectoryById } from "@/lib/server/data";
import { cached } from "@/lib/server/cache";

const execFileAsync = promisify(execFile);

const SCRIPT_PATH = path.join(process.cwd(), "scripts", "check-sandbox-status.py");

/** Cache sandbox status for 15 seconds to avoid hammering the provider */
const STATUS_CACHE_TTL_MS = 15_000;

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  try {
    const trajectory = await getTrajectoryById(id, { fresh: true });
    if (!trajectory) {
      return NextResponse.json({ error: "Trajectory not found" }, { status: 404 });
    }

    /** Already finished — no need to query the provider */
    if (trajectory.sessionEndReason) {
      return NextResponse.json({ running: false, reason: trajectory.sessionEndReason });
    }

    /** No sandbox info — legacy trajectory without sandbox tracking */
    if (!trajectory.sandboxId || !trajectory.sandboxProvider) {
      return NextResponse.json({ running: false, reason: "no_sandbox_info" });
    }

    const sandboxId = trajectory.sandboxId;
    const sandboxProvider = trajectory.sandboxProvider;

    const result = await cached(
      `sandbox-status:${sandboxId}`,
      async () => {
        const { stdout } = await execFileAsync("python3", [
          SCRIPT_PATH,
          sandboxProvider,
          sandboxId,
        ], { timeout: 10_000 });
        return JSON.parse(stdout.trim()) as { running: boolean; exitCode?: number; error?: string };
      },
      STATUS_CACHE_TTL_MS,
    );

    return NextResponse.json(result);
  } catch (error) {
    console.error(`GET /api/trajectories/${id}/sandbox-status error:`, error);
    return NextResponse.json(
      { running: false, error: "Failed to check sandbox status" },
      { status: 500 },
    );
  }
}
