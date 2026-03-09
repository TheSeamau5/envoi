/**
 * GET /api/compare
 *
 * Returns trajectory data for the compare page.
 * Query params: ?ids=traj1,traj2,traj3 or ?environment=c_compiler
 */

import { NextRequest, NextResponse } from "next/server";
import { getProjectFromRequest } from "@/lib/server/project-context";
import {
  getProjectSnapshot,
  getTrajectoryDetailFromSnapshot,
} from "@/lib/server/project-snapshot-store";

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
    const idsParam = searchParams.get("ids");
    const environment = searchParams.get("environment") ?? undefined;
    const bustRequested = searchParams.has("bust");

    const ids = idsParam ? idsParam.split(",").filter(Boolean) : undefined;
    const snapshot = await getProjectSnapshot(project);
    let trajectories = snapshot.compare;
    if (ids && ids.length > 0) {
      const details = await Promise.all(
        ids.map((id) => getTrajectoryDetailFromSnapshot(project, id)),
      );
      trajectories = details.filter(
        (trajectory): trajectory is NonNullable<typeof trajectory> =>
          trajectory !== undefined,
      );
    }
    if (environment) {
      trajectories = trajectories.filter(
        (trajectory) => trajectory.environment === environment,
      );
    }
    console.log(
      `[api/compare] project=${project} bust=${bustRequested} ids=${ids?.length ?? 0} environment=${environment ?? "all"} count=${trajectories.length} durationMs=${Date.now() - startedAt}`,
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
    console.error("GET /api/compare error:", error);
    return NextResponse.json(
      { error: "Failed to fetch compare data" },
      { status: 500 },
    );
  }
}
