/**
 * GET /api/trajectories/[id]/code-history
 *
 * Returns code snapshots for each commit in the trajectory.
 * The response is a map from commit index (number) to CodeSnapshot.
 * Returns an empty object if code_snapshots.parquet does not exist.
 */

import { NextRequest, NextResponse } from "next/server";
import { getProjectFromRequest } from "@/lib/server/project-context";
import { getCodeHistoryChunkFromSnapshot } from "@/lib/server/project-snapshot-store";

type RouteParams = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const project = await getProjectFromRequest(_request);
    if (!project) {
      return NextResponse.json(
        { error: "Project not selected" },
        { status: 400 },
      );
    }

    const { id } = await params;
    const codeHistory = await getCodeHistoryChunkFromSnapshot(project, id, 0);

    if (codeHistory === undefined) {
      return NextResponse.json({});
    }

    return NextResponse.json(codeHistory);
  } catch (error) {
    console.error("GET /api/trajectories/[id]/code-history error:", error);
    return NextResponse.json(
      { error: "Failed to fetch code history" },
      { status: 500 },
    );
  }
}
