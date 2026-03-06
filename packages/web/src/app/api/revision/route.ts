import { NextRequest, NextResponse } from "next/server";
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

    const revision = await getSummaryRevisionStatus(project, {
      forceCheck: true,
    });
    return NextResponse.json(revision, {
      headers: buildSummaryRevisionHeaders(revision),
    });
  } catch (error) {
    console.error("GET /api/revision error:", error);
    return NextResponse.json(
      { error: "Failed to fetch revision status" },
      { status: 500 },
    );
  }
}
