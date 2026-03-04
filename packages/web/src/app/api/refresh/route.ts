/**
 * POST /api/refresh
 *
 * Triggers a data re-sync from S3 and reloads summary tables into DuckDB.
 * Also clears the in-memory API response cache so subsequent requests
 * see fresh data.
 */

import { NextRequest, NextResponse } from "next/server";
import { refreshData } from "@/lib/server/db";
import { clearCache } from "@/lib/server/cache";
import { getProjectFromRequest } from "@/lib/server/project-context";

export async function POST(request: NextRequest) {
  try {
    const project = await getProjectFromRequest(request);
    if (!project) {
      return NextResponse.json(
        { error: "Project not selected" },
        { status: 400 },
      );
    }

    await refreshData(project);
    clearCache();
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("POST /api/refresh error:", error);
    return NextResponse.json({ error: "Refresh failed" }, { status: 500 });
  }
}
