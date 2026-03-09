import type {
  CodeSnapshot,
  Project,
  Trajectory,
  TrajectoryLogRow,
} from "@/lib/types";
import {
  getAllTrajectories,
  getCodeHistory,
  getTrajectoryByIdForServing,
  getTrajectoryLogsById,
} from "./data";
import { publishLiveRevisionEvent } from "./live-events";
import { getTrajectorySandboxLiveness } from "./sandbox-liveness";
import {
  computeServingSourceRevision,
  getServingProjects,
  putServingJson,
  readServingCompare,
  readServingCodeHistory,
  readServingDetail,
  readServingLiveIndex,
  readServingLogs,
  readServingManifest,
  readServingSetups,
  readServingTrajectories,
  servingManifestKey,
  servingRevisionPrefix,
} from "./serving-s3";
import type {
  ServingLiveIndex,
  ServingManifest,
  ServingProjectSnapshot,
  ServingTrajectoryArtifacts,
} from "./serving-types";

type SnapshotStoreState = {
  pollers: Map<string, NodeJS.Timeout>;
  snapshots: Map<string, ServingProjectSnapshot>;
  refreshes: Map<string, Promise<ServingProjectSnapshot>>;
};

const snapshotStoreGlobals = globalThis as unknown as Partial<{
  envoiSnapshotStore: SnapshotStoreState;
}>;

const snapshotStore = (snapshotStoreGlobals.envoiSnapshotStore ??= {
  pollers: new Map<string, NodeJS.Timeout>(),
  snapshots: new Map<string, ServingProjectSnapshot>(),
  refreshes: new Map<string, Promise<ServingProjectSnapshot>>(),
});

const PROJECT_POLL_MS = 500;

function buildAgentSummary(
  trajectories: Trajectory[],
): ServingManifest["agents"] {
  const counts = new Map<string, number>();
  for (const trajectory of trajectories) {
    const key = trajectory.model;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([model, count]) => ({ model, count }))
    .sort((left, right) => left.model.localeCompare(right.model));
}

function buildProjectForSnapshot(
  project: Project,
  trajectories: Trajectory[],
): Project {
  const environments = new Set(
    trajectories.map((trajectory) => trajectory.environment),
  );
  const models = new Set(trajectories.map((trajectory) => trajectory.model));
  return {
    ...project,
    trajectoryCount: trajectories.length,
    environmentCount: environments.size,
    modelCount: models.size,
  };
}

function buildServingTrajectorySummary(
  fallback: Trajectory,
  detail: Trajectory | undefined,
): Trajectory {
  if (!detail) {
    return fallback;
  }
  return {
    ...detail,
    commits: [],
  };
}

async function resolveLiveTrajectoryIds(
  project: string,
  trajectories: Trajectory[],
): Promise<string[]> {
  const candidates = trajectories.filter(
    (trajectory) => !trajectory.sessionEndReason,
  );
  const liveIds: string[] = [];
  await Promise.all(
    candidates.map(async (trajectory) => {
      const liveness = await getTrajectorySandboxLiveness(
        project,
        trajectory.id,
      );
      if (liveness.running) {
        liveIds.push(trajectory.id);
      }
    }),
  );
  return liveIds.sort();
}

async function publishLiveOverlay(
  snapshot: ServingProjectSnapshot,
  publishedAt: string,
): Promise<ServingProjectSnapshot> {
  const liveIds = await resolveLiveTrajectoryIds(
    snapshot.project.name,
    snapshot.trajectories,
  );
  const live: ServingLiveIndex = {
    revision: snapshot.manifest.revision,
    updatedAt: publishedAt,
    trajectoryIds: liveIds,
    liveTrajectoryCount: liveIds.length,
  };
  const liveIndex = await putServingJson(
    `${servingRevisionPrefix(snapshot.project.name, snapshot.manifest.revision)}/live.index.json`,
    live,
  );
  const manifest: ServingManifest = {
    ...snapshot.manifest,
    publishedAt,
    liveTrajectoryCount: liveIds.length,
    objects: {
      ...snapshot.manifest.objects,
      liveIndex,
    },
  };
  await putServingJson(servingManifestKey(snapshot.project.name), manifest);
  return {
    ...snapshot,
    manifest,
    live,
  };
}

async function publishProjectSnapshot(
  project: Project,
  revision: string,
): Promise<ServingProjectSnapshot> {
  const summaryTrajectories = await getAllTrajectories({
    project: project.name,
  });
  const details = new Map<string, Trajectory>();
  const refs: Record<string, ServingTrajectoryArtifacts> = {};
  const compare = [...summaryTrajectories];
  const trajectories = [...summaryTrajectories]
    .map((trajectory) => buildServingTrajectorySummary(trajectory, undefined))
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt));
  const setups = compare;
  const publishedAt = new Date().toISOString();
  const liveIds = await resolveLiveTrajectoryIds(project.name, trajectories);
  const revisionPrefix = servingRevisionPrefix(project.name, revision);
  const trajectoriesIndex = await putServingJson(
    `${revisionPrefix}/trajectories.index.json`,
    trajectories,
  );
  const compareIndex = await putServingJson(
    `${revisionPrefix}/compare.index.json`,
    compare,
  );
  const setupsIndex = await putServingJson(
    `${revisionPrefix}/setups.index.json`,
    setups,
  );
  const live: ServingLiveIndex = {
    revision,
    updatedAt: publishedAt,
    trajectoryIds: liveIds,
    liveTrajectoryCount: liveIds.length,
  };
  const liveIndex = await putServingJson(
    `${revisionPrefix}/live.index.json`,
    live,
  );
  const manifest: ServingManifest = {
    project: project.name,
    revision,
    publishedAt,
    trajectoryCount: trajectories.length,
    liveTrajectoryCount: liveIds.length,
    agents: buildAgentSummary(trajectories),
    objects: {
      trajectoriesIndex,
      compareIndex,
      setupsIndex,
      liveIndex,
      trajectories: refs,
    },
  };
  await putServingJson(servingManifestKey(project.name), manifest);

  return {
    project: buildProjectForSnapshot(project, trajectories),
    manifest,
    trajectories,
    compare,
    setups,
    live,
    details,
  };
}

async function loadPublishedSnapshot(
  project: Project,
  manifest: ServingManifest,
): Promise<ServingProjectSnapshot> {
  const trajectories = await readServingTrajectories(manifest);
  const compare = await readServingCompare(manifest);
  const setups = await readServingSetups(manifest);
  const live = await readServingLiveIndex(manifest);

  return {
    project: buildProjectForSnapshot(project, trajectories),
    manifest,
    trajectories,
    compare,
    setups,
    live,
    details: new Map<string, Trajectory>(),
  };
}

async function refreshProjectSnapshot(
  project: Project,
): Promise<ServingProjectSnapshot> {
  const sourceRevision = await computeServingSourceRevision(project.name);
  const current = snapshotStore.snapshots.get(project.name);
  if (current && current.manifest.revision === sourceRevision) {
    const nextLiveIds = await resolveLiveTrajectoryIds(
      project.name,
      current.trajectories,
    );
    const currentLiveIds = current.live.trajectoryIds.join(",");
    const resolvedLiveIds = nextLiveIds.join(",");
    if (currentLiveIds === resolvedLiveIds) {
      return current;
    }
    const snapshot = await publishLiveOverlay(
      current,
      new Date().toISOString(),
    );
    snapshotStore.snapshots.set(project.name, snapshot);
    return snapshot;
  }

  const publishedManifest = await readServingManifest(project.name);
  const publishedArtifactsCount = publishedManifest
    ? Object.keys(publishedManifest.objects.trajectories).length
    : 0;
  const isPublishedManifestComplete =
    publishedManifest?.trajectoryCount === publishedArtifactsCount;
  if (
    publishedManifest &&
    publishedManifest.revision === sourceRevision &&
    isPublishedManifestComplete
  ) {
    const snapshot = await loadPublishedSnapshot(project, publishedManifest);
    snapshotStore.snapshots.set(project.name, snapshot);
    if (current?.manifest.revision !== snapshot.manifest.revision) {
      publishLiveRevisionEvent({
        project: project.name,
        revision: snapshot.manifest.revision,
      });
    }
    return snapshot;
  }

  const snapshot = await publishProjectSnapshot(project, sourceRevision);
  snapshotStore.snapshots.set(project.name, snapshot);
  if (current?.manifest.revision !== snapshot.manifest.revision) {
    publishLiveRevisionEvent({
      project: project.name,
      revision: snapshot.manifest.revision,
    });
  }
  return snapshot;
}

function startProjectPolling(project: Project): void {
  if (snapshotStore.pollers.has(project.name)) {
    return;
  }

  const poller = setInterval(() => {
    if (snapshotStore.refreshes.has(project.name)) {
      return;
    }
    const task = refreshProjectSnapshot(project)
      .catch((error) => {
        console.warn(
          `[snapshot-store] refresh failed project=${project.name}:`,
          error instanceof Error ? error.message : error,
        );
        throw error;
      })
      .finally(() => {
        snapshotStore.refreshes.delete(project.name);
      });
    snapshotStore.refreshes.set(project.name, task);
    void task.catch(() => {});
  }, PROJECT_POLL_MS);
  poller.unref?.();
  snapshotStore.pollers.set(project.name, poller);
}

async function getProjectMetadata(
  projectName: string,
): Promise<Project | undefined> {
  const projects = await getServingProjects();
  return projects.find((project) => project.name === projectName);
}

/** Warm all non-legacy serving snapshots and start revision polling. */
export async function warmProjectSnapshotStore(): Promise<void> {
  const projects = await getServingProjects();
  await Promise.all(
    projects.map((project) => ensureProjectSnapshot(project.name)),
  );
}

/** Return the UI-visible projects, excluding legacy and including serving counts. */
export async function getProjectsForUi(): Promise<Project[]> {
  const projects = await getServingProjects();
  const snapshots = await Promise.all(
    projects.map((project) => ensureProjectSnapshot(project.name)),
  );
  return snapshots.map((snapshot) => snapshot.project);
}

/** Return the current in-memory serving snapshot for a project. */
export async function getProjectSnapshot(
  project: string,
): Promise<ServingProjectSnapshot> {
  return ensureProjectSnapshot(project);
}

/** Return the current serving detail for a trajectory. */
export async function getTrajectoryDetailFromSnapshot(
  project: string,
  trajectoryId: string,
): Promise<Trajectory | undefined> {
  const snapshot = await ensureProjectSnapshot(project);
  const existing = snapshot.details.get(trajectoryId);
  if (existing) {
    return existing;
  }
  const detail = await readServingDetail(snapshot.manifest, trajectoryId);
  if (detail) {
    snapshot.details.set(trajectoryId, detail);
    return detail;
  }

  const fallback = await getTrajectoryByIdForServing(trajectoryId, project);
  if (fallback) {
    snapshot.details.set(trajectoryId, fallback);
  }
  return fallback;
}

/** Return serving logs for a trajectory, filtered by sequence window. */
export async function getTrajectoryLogsFromSnapshot(
  project: string,
  trajectoryId: string,
  fromSeq: number,
  limit: number,
): Promise<TrajectoryLogRow[] | undefined> {
  const snapshot = await ensureProjectSnapshot(project);
  const payload = await readServingLogs(snapshot.manifest, trajectoryId);
  if (!payload) {
    return getTrajectoryLogsById(trajectoryId, {
      project,
      fresh: false,
      fromSeq,
      limit,
    });
  }
  return payload.rows.filter((row) => row.seq > fromSeq).slice(0, limit);
}

/** Return the first serving code-history chunk for a trajectory. */
export async function getCodeHistoryChunkFromSnapshot(
  project: string,
  trajectoryId: string,
  chunkIndex: number,
): Promise<Record<number, CodeSnapshot> | undefined> {
  if (chunkIndex !== 0) {
    return undefined;
  }
  const snapshot = await ensureProjectSnapshot(project);
  const payload = await readServingCodeHistory(snapshot.manifest, trajectoryId);
  if (payload) {
    return payload.codeHistory;
  }
  return getCodeHistory(trajectoryId, project);
}

async function ensureProjectSnapshot(
  projectName: string,
): Promise<ServingProjectSnapshot> {
  const existing = snapshotStore.snapshots.get(projectName);
  if (existing) {
    return existing;
  }

  const inFlight = snapshotStore.refreshes.get(projectName);
  if (inFlight) {
    return inFlight;
  }

  const project = await getProjectMetadata(projectName);
  if (!project || project.name === "legacy") {
    throw new Error(`Serving snapshot unavailable for project: ${projectName}`);
  }

  startProjectPolling(project);

  const task = refreshProjectSnapshot(project).finally(() => {
    snapshotStore.refreshes.delete(projectName);
  });
  snapshotStore.refreshes.set(projectName, task);
  return task;
}
