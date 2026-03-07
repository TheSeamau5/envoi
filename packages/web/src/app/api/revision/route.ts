import { NextRequest, NextResponse } from "next/server";
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

    const revision = await readProjectDataStatus(project, {
      forceCheck: true,
      mode: "fresh",
    });
    console.log(
      `[api/revision] project=${project} dataVersion=${revision.dataVersion} durationMs=${Date.now() - startedAt}`,
    );
    return NextResponse.json(revision, {
      headers: buildProjectDataHeaders(revision),
    });
  } catch (error) {
    console.error("GET /api/revision error:", error);
    return NextResponse.json(
      { error: "Failed to fetch revision status" },
      { status: 500 },
    );
  }
}
