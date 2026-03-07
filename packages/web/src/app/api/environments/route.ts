/**
 * GET /api/environments
 *
 * Returns distinct environments found in the data with metadata.
 */

import { NextRequest, NextResponse } from "next/server";
import { getEnvironments } from "@/lib/server/data";
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

    const fresh = request.nextUrl.searchParams.has("bust");
    const environments = await getEnvironments(project, { fresh });
    return NextResponse.json(environments);
  } catch (error) {
    console.error("GET /api/environments error:", error);
    return NextResponse.json(
      { error: "Failed to fetch environments" },
      { status: 500 },
    );
  }
}
