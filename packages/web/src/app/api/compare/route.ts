/**
 * GET /api/compare
 *
 * Returns trajectory data for the compare page.
 * Query params: ?ids=traj1,traj2,traj3 or ?environment=c_compiler
 */

import { NextRequest, NextResponse } from "next/server";
import { getCompareTrajectories } from "@/lib/server/data";
import {
  buildSummaryRevisionHeaders,
  getSummaryRevisionStatus,
} from "@/lib/server/db";
import { getProjectFromRequest } from "@/lib/server/project-context";

export async function GET(request: NextRequest) {
  try {
    const project = await getProjectFromRequest(request);
    if (!project) {
      return NextResponse.json(
        { error: "Project not selected" },
        { status: 400 },
      );
    }

    const { searchParams } = request.nextUrl;
    const idsParam = searchParams.get("ids");
    const environment = searchParams.get("environment") ?? undefined;
    const fresh = searchParams.has("bust");

    const ids = idsParam ? idsParam.split(",").filter(Boolean) : undefined;

    const trajectories = await getCompareTrajectories({
      ids,
      environment,
      fresh,
      project,
    });
    const revision = await getSummaryRevisionStatus(project);

    return NextResponse.json(trajectories, {
      headers: buildSummaryRevisionHeaders(revision),
    });
  } catch (error) {
    console.error("GET /api/compare error:", error);
    return NextResponse.json(
      { error: "Failed to fetch compare data" },
      { status: 500 },
    );
  }
}
