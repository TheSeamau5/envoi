/**
 * Centralized server-side project data freshness coordinator.
 *
 * Every project-scoped data reader should go through this module so freshness
 * policy, cache behavior, and status reporting stay consistent across routes.
 */

import { cached } from "./cache";
import {
  buildProjectDataHeaders,
  ensureProjectDataFreshness,
  getActiveProject,
  getProjectDataStatus,
  type ProjectDataStatus,
  type ProjectFreshnessMode,
} from "./db";

export type { ProjectDataStatus, ProjectFreshnessMode };

type ReadProjectDataOptions<T> = {
  project?: string;
  freshness?: ProjectFreshnessMode;
  cacheKey?: string;
  ttlMs?: number;
  load: (project: string) => Promise<T>;
};

export function freshnessFromBool(
  fresh: boolean | undefined,
): ProjectFreshnessMode {
  return fresh === true ? "force" : "cached";
}

export async function readProjectData<T>(
  options: ReadProjectDataOptions<T>,
): Promise<T> {
  const activeProject = await getActiveProject(options.project);
  const freshness = options.freshness ?? "cached";
  const startedAt = Date.now();
  console.log(
    `[project-data] read start project=${activeProject} freshness=${freshness} cacheKey=${options.cacheKey ?? "none"}`,
  );
  await ensureProjectDataFreshness(activeProject, { mode: freshness });

  const load = async (): Promise<T> => options.load(activeProject);
  let result: T;
  if (freshness !== "cached") {
    result = await load();
  } else if (!options.cacheKey) {
    result = await load();
  } else {
    result = await cached(options.cacheKey, load, options.ttlMs);
  }
  console.log(
    `[project-data] read done project=${activeProject} freshness=${freshness} cacheKey=${options.cacheKey ?? "none"} durationMs=${Date.now() - startedAt}`,
  );
  return result;
}

export async function readProjectDataStatus(
  project?: string,
  options?: {
    forceCheck?: boolean;
    mode?: ProjectFreshnessMode;
  },
): Promise<ProjectDataStatus> {
  const startedAt = Date.now();
  const status = await getProjectDataStatus(project, options);
  console.log(
    `[project-data] status project=${project ?? "default"} mode=${options?.mode ?? "fresh"} forceCheck=${options?.forceCheck === true} dataVersion=${status.dataVersion} durationMs=${Date.now() - startedAt}`,
  );
  return status;
}

export { buildProjectDataHeaders };
