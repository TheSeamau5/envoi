/**
 * GET /api/schema
 *
 * Returns the current DuckDB schema metadata for the SQL console sidebar.
 */

import { NextRequest, NextResponse } from "next/server";
import { getSchemaInfo } from "@/lib/server/data";
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

    const schema = await getSchemaInfo(project);
    return NextResponse.json(schema);
  } catch (error) {
    console.error("GET /api/schema error:", error);
    return NextResponse.json(
      { error: "Failed to fetch schema" },
      { status: 500 },
    );
  }
}
