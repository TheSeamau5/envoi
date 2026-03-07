/**
 * GET /api/trajectories/[id]
 *
 * Returns full trajectory data for the detail page.
 * Pass ?bust=<timestamp> to bypass local cache and read directly from S3
 * (used by the live-polling hook for in-progress trajectories).
 */

import { NextRequest, NextResponse } from "next/server";
import { getTrajectoryById } from "@/lib/server/data";
import { getProjectFromRequest } from "@/lib/server/project-context";

type RouteParams = { params: Promise<{ id: string }> };

export async function GET(request: NextRequest, { params }: RouteParams) {
  const startedAt = Date.now();
  try {
    const project = await getProjectFromRequest(request);
    if (!project) {
      return NextResponse.json(
        { error: "Project not selected" },
        { status: 400 },
      );
    }

    const { id } = await params;
    const trajectory = await getTrajectoryById(id, { fresh: false, project });

    if (!trajectory) {
      return NextResponse.json(
        { error: "Trajectory not found" },
        { status: 404 },
      );
    }

    console.log(
      `[api/trajectory-detail] project=${project} id=${id} commits=${trajectory.commits.length} durationMs=${Date.now() - startedAt}`,
    );

    return NextResponse.json(trajectory);
  } catch (error) {
    console.error("GET /api/trajectories/[id] error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectory" },
      { status: 500 },
    );
  }
}
