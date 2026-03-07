/**
 * GET /api/trajectories
 *
 * Returns trajectory summaries for the list page.
 * Optional query params: ?environment=..., ?model=..., ?limit=..., ?offset=...
 */

import { NextRequest, NextResponse } from "next/server";
import { getAllTrajectories } from "@/lib/server/data";
import {
  buildProjectDataHeaders,
  readProjectDataStatus,
} from "@/lib/server/project-data";
import { getProjectFromRequest } from "@/lib/server/project-context";

export async function GET(request: NextRequest) {
  const startedAt = Date.now();
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

    const bustRequested = searchParams.has("bust");
    const trajectories = await getAllTrajectories({
      environment,
      model,
      limit,
      offset,
      fresh: false,
      project,
    });
    const status = await readProjectDataStatus(project, {
      forceCheck: bustRequested,
      mode: bustRequested ? "fresh" : "cached",
    });
    console.log(
      `[api/trajectories] project=${project} bust=${bustRequested} environment=${environment ?? "all"} model=${model ?? "all"} count=${trajectories.length} durationMs=${Date.now() - startedAt}`,
    );

    return NextResponse.json(trajectories, {
      headers: buildProjectDataHeaders(status),
    });
  } catch (error) {
    console.error("GET /api/trajectories error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectories" },
      { status: 500 },
    );
  }
}
