/**
 * GET /api/trajectories
 *
 * Returns trajectory summaries for the list page.
 * Optional query params: ?environment=..., ?model=..., ?limit=..., ?offset=...
 */

import { NextRequest, NextResponse } from "next/server";
import { getAllTrajectories } from "@/lib/server/data";
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
    const environment = searchParams.get("environment") ?? undefined;
    const model = searchParams.get("model") ?? undefined;
    const limit = searchParams.get("limit")
      ? Number(searchParams.get("limit"))
      : undefined;
    const offset = searchParams.get("offset")
      ? Number(searchParams.get("offset"))
      : undefined;

    const fresh = searchParams.has("bust");
    const trajectories = await getAllTrajectories({
      environment,
      model,
      limit,
      offset,
      fresh,
      project,
    });
    const revision = await getSummaryRevisionStatus(project);

    return NextResponse.json(trajectories, {
      headers: buildSummaryRevisionHeaders(revision),
    });
  } catch (error) {
    console.error("GET /api/trajectories error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectories" },
      { status: 500 },
    );
  }
}
