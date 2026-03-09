/**
 * GET /api/trajectories/:id/sandbox-status
 *
 * Checks whether a trajectory's sandbox is still running by querying
 * the sandbox provider (Modal or E2B). Returns { running: boolean }.
 *
 * This is the source of truth for liveness — no heuristics needed.
 */

import { NextRequest, NextResponse } from "next/server";
import { getProjectFromRequest } from "@/lib/server/project-context";
import { getTrajectorySandboxLiveness } from "@/lib/server/sandbox-liveness";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const project = await getProjectFromRequest(request);
  if (!project) {
    return NextResponse.json(
      { error: "Project not selected" },
      { status: 400 },
    );
  }

  const { id } = await params;

  try {
    const result = await getTrajectorySandboxLiveness(project, id);
    return NextResponse.json(result);
  } catch (error) {
    console.error(`GET /api/trajectories/${id}/sandbox-status error:`, error);
    return NextResponse.json(
      { running: false, error: "Failed to check sandbox status" },
      { status: 500 },
    );
  }
}
