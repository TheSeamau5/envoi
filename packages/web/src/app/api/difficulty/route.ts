/**
 * GET /api/difficulty — Difficulty heatmap data.
 * Returns per-(category, model) pass rates.
 */

import { NextRequest, NextResponse } from "next/server";
import { getDifficultyData } from "@/lib/server/data";
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

    const data = await getDifficultyData(project);
    return NextResponse.json(data);
  } catch (error) {
    console.error("GET /api/difficulty error:", error);
    return NextResponse.json(
      { error: "Failed to fetch difficulty data" },
      { status: 500 },
    );
  }
}
