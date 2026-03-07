/**
 * GET /api/portfolio — Portfolio dashboard data.
 * Returns per-model rankings, environment summaries, and Pareto frontier points.
 */

import { NextRequest, NextResponse } from "next/server";
import {
  getPortfolioData,
  getPortfolioEnvironmentData,
  getParetoData,
} from "@/lib/server/data";
import { getProjectFromRequest } from "@/lib/server/project-context";

/** GET handler for full portfolio dashboard payload */
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
    const [rows, environmentRows, paretoPoints] = await Promise.all([
      getPortfolioData(project, { fresh }),
      getPortfolioEnvironmentData(project, { fresh }),
      getParetoData(undefined, project, { fresh }),
    ]);

    return NextResponse.json({ rows, environmentRows, paretoPoints });
  } catch (error) {
    console.error("GET /api/portfolio error:", error);
    return NextResponse.json(
      { error: "Failed to fetch portfolio data" },
      { status: 500 },
    );
  }
}
