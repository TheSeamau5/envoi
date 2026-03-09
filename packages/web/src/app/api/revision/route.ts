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

    const snapshot = await getProjectSnapshot(project);
    const revision = {
      hasManifest: true,
      inSync: true,
      s3Revision: snapshot.manifest.revision,
      loadedRevision: snapshot.manifest.revision,
      summaryRevision: snapshot.manifest.revision,
      loadedSummaryRevision: snapshot.manifest.revision,
      lastCheckedAt: snapshot.manifest.publishedAt,
      lastLoadedAt: snapshot.manifest.publishedAt,
      publishedAt: snapshot.manifest.publishedAt,
      revisionLagMs: 0,
      refreshDurationMs: 0,
      dataVersion: snapshot.manifest.revision,
      lastRawSyncAt: snapshot.manifest.publishedAt,
      lastTableRefreshAt: snapshot.manifest.publishedAt,
      rawSyncInFlight: false,
      summarySyncInFlight: false,
    };
    console.log(
      `[api/revision] project=${project} dataVersion=${revision.dataVersion} durationMs=${Date.now() - startedAt}`,
    );
    return NextResponse.json(revision, {
      headers: {
        "x-envoi-has-manifest": "true",
        "x-envoi-in-sync": "true",
        "x-envoi-s3-revision": snapshot.manifest.revision,
        "x-envoi-loaded-revision": snapshot.manifest.revision,
        "x-envoi-data-version": snapshot.manifest.revision,
      },
    });
  } catch (error) {
    console.error("GET /api/revision error:", error);
    return NextResponse.json(
      { error: "Failed to fetch revision status" },
      { status: 500 },
    );
  }
}
