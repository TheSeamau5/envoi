/**
 * GET /api/trajectories
 *
 * Returns trajectory summaries for the list page.
 * Optional query params: ?environment=..., ?model=..., ?limit=..., ?offset=...
 */

import { NextRequest, NextResponse } from "next/server";
import { getProjectFromRequest } from "@/lib/server/project-context";
import { getProjectSnapshot } from "@/lib/server/project-snapshot-store";

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
    const snapshot = await getProjectSnapshot(project);
    let trajectories = snapshot.trajectories;
    if (environment) {
      trajectories = trajectories.filter(
        (trajectory) => trajectory.environment === environment,
      );
    }
    if (model) {
      trajectories = trajectories.filter(
        (trajectory) => trajectory.model === model,
      );
    }
    if (offset) {
      trajectories = trajectories.slice(offset);
    }
    if (limit) {
      trajectories = trajectories.slice(0, limit);
    }
    console.log(
      `[api/trajectories] project=${project} bust=${bustRequested} environment=${environment ?? "all"} model=${model ?? "all"} count=${trajectories.length} durationMs=${Date.now() - startedAt}`,
    );

    return NextResponse.json(trajectories, {
      headers: {
        "x-envoi-has-manifest": "true",
        "x-envoi-in-sync": "true",
        "x-envoi-s3-revision": snapshot.manifest.revision,
        "x-envoi-loaded-revision": snapshot.manifest.revision,
        "x-envoi-data-version": snapshot.manifest.revision,
      },
    });
  } catch (error) {
    console.error("GET /api/trajectories error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectories" },
      { status: 500 },
    );
  }
}
