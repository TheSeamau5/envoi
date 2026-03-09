"use client";

import { useEffect, useRef } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type {
  CodeSnapshot,
  DifficultyCell,
  SchemaColumn,
  Trajectory,
  TrajectoryLogRow,
} from "@/lib/types";
import { queryKeys } from "@/lib/query-keys";
import { isTrajectoryActive } from "@/lib/trajectory-state";

const PROJECT_STATUS_POLL_MS = 1_000;
const LIVE_TRAJECTORY_POLL_MS = 1_000;

export type ProjectDataStatus = {
  hasManifest: boolean;
  inSync: boolean;
  s3Revision?: string;
  loadedRevision?: string;
  summaryRevision?: string;
  loadedSummaryRevision?: string;
  lastCheckedAt?: string;
  lastLoadedAt?: string;
  publishedAt?: string;
  revisionLagMs: number;
  refreshDurationMs?: number;
  dataVersion: string;
  lastRawSyncAt?: string;
  lastTableRefreshAt?: string;
  rawSyncInFlight: boolean;
  summarySyncInFlight: boolean;
};

function dedupeTrajectoriesById(traces: Trajectory[]): Trajectory[] {
  const deduped = new Map<string, Trajectory>();
  for (const trace of traces) {
    deduped.set(trace.id, trace);
  }
  return [...deduped.values()];
}

export function ensureQueryValue<T>(
  value: T | undefined,
  queryKey: readonly unknown[],
): T {
  if (value === undefined) {
    throw new Error(
      `Query data cannot be undefined. Affected query key: ${JSON.stringify(queryKey)}`,
    );
  }
  return value;
}

function buildProjectUrl(
  path: string,
  project: string,
  options?: { bust?: boolean; searchParams?: URLSearchParams },
): string {
  const searchParams = options?.searchParams ?? new URLSearchParams();
  searchParams.set("project", project);
  if (options?.bust) {
    searchParams.set("bust", `${Date.now()}`);
  }
  const query = searchParams.toString();
  return query.length > 0 ? `${path}?${query}` : path;
}

async function fetchProjectJson<T>(
  path: string,
  project: string,
  options?: {
    bust?: boolean;
    searchParams?: URLSearchParams;
  },
): Promise<T> {
  const response = await fetch(buildProjectUrl(path, project, options));
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return ensureQueryValue((await response.json()) as T | undefined, [
    path,
    project,
  ]);
}

function invalidateProjectQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  invalidateKeys: readonly (readonly unknown[])[],
): void {
  for (const key of invalidateKeys) {
    void queryClient.invalidateQueries({ queryKey: [...key] });
  }
}

export function useProjectDataStatus(
  project: string,
  options?: {
    enabled?: boolean;
    invalidateKeys?: readonly (readonly unknown[])[];
  },
): ProjectDataStatus | undefined {
  const enabled = options?.enabled !== false;
  const queryClient = useQueryClient();
  const previousDataVersionRef = useRef<string | undefined>(undefined);
  const hasPrimedInvalidationRef = useRef(false);

  const statusQuery = useQuery({
    queryKey: queryKeys.revision.status(project),
    queryFn: () =>
      fetchProjectJson<ProjectDataStatus>("/api/revision", project),
    enabled,
    refetchInterval: enabled ? PROJECT_STATUS_POLL_MS : false,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  useEffect(() => {
    const dataVersion = statusQuery.data?.dataVersion;
    if (!dataVersion) {
      return;
    }
    if (previousDataVersionRef.current === undefined) {
      previousDataVersionRef.current = dataVersion;
      if (hasPrimedInvalidationRef.current) {
        return;
      }
      hasPrimedInvalidationRef.current = true;
      invalidateProjectQueries(queryClient, options?.invalidateKeys ?? []);
      return;
    }
    if (previousDataVersionRef.current === dataVersion) {
      return;
    }
    previousDataVersionRef.current = dataVersion;
    invalidateProjectQueries(queryClient, options?.invalidateKeys ?? []);
  }, [options?.invalidateKeys, queryClient, statusQuery.data?.dataVersion]);

  return statusQuery.data;
}

export function useProjectTrajectories(
  project: string,
  initialData?: Trajectory[],
) {
  useProjectDataStatus(project, {
    invalidateKeys: [queryKeys.trajectories.all(project)],
  });

  return useQuery({
    queryKey: queryKeys.trajectories.all(project),
    queryFn: async () =>
      dedupeTrajectoriesById(
        await fetchProjectJson<Trajectory[]>("/api/trajectories", project),
      ),
    initialData:
      initialData && initialData.length > 0
        ? dedupeTrajectoriesById(initialData)
        : undefined,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
  });
}

export function useProjectLiveTrajectoryIds(
  project: string,
  trajectories: Trajectory[],
) {
  const candidateIds = trajectories
    .filter((trajectory) => !trajectory.sessionEndReason)
    .map((trajectory) => trajectory.id)
    .sort();

  return useQuery({
    queryKey: queryKeys.trajectories.live(project, candidateIds),
    queryFn: async () => {
      const liveIds = new Set<string>();
      await Promise.allSettled(
        candidateIds.map(async (trajectoryId) => {
          const response = await fetch(
            buildProjectUrl(
              `/api/trajectories/${encodeURIComponent(trajectoryId)}/sandbox-status`,
              project,
            ),
          );
          if (!response.ok) {
            return;
          }
          const data = (await response.json()) as { running?: boolean };
          if (data.running === true) {
            liveIds.add(trajectoryId);
          }
        }),
      );
      return liveIds;
    },
    enabled: candidateIds.length > 0,
    initialData: new Set<string>(),
    refetchInterval: candidateIds.length > 0 ? LIVE_TRAJECTORY_POLL_MS : false,
    staleTime: 0,
  });
}

export function useProjectCompare(
  project: string,
  options?: {
    ids?: string[];
    environment?: string;
    initialData?: Trajectory[];
    enabled?: boolean;
  },
) {
  const ids = options?.ids ?? [];
  const sortedIds = [...ids].sort();
  const searchParams = new URLSearchParams();
  if (sortedIds.length > 0) {
    searchParams.set("ids", sortedIds.join(","));
  }
  if (options?.environment) {
    searchParams.set("environment", options.environment);
  }

  const queryKey =
    sortedIds.length > 0
      ? queryKeys.compare.byIds(project, sortedIds)
      : options?.environment
        ? ([...queryKeys.compare.all(project), options.environment] as const)
        : queryKeys.compare.full(project);

  useProjectDataStatus(project, {
    invalidateKeys: [queryKey],
  });

  return useQuery({
    queryKey,
    queryFn: async () =>
      dedupeTrajectoriesById(
        await fetchProjectJson<Trajectory[]>("/api/compare", project, {
          searchParams,
        }),
      ),
    initialData:
      options?.initialData && options.initialData.length > 0
        ? dedupeTrajectoriesById(options.initialData)
        : undefined,
    enabled: options?.enabled !== false,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
  });
}

export function useProjectSetups(project: string, initialData?: Trajectory[]) {
  const query = useProjectCompare(project, {
    initialData,
  });

  return {
    ...query,
    data: dedupeTrajectoriesById(
      (query.data ?? []).filter((trace) => isTrajectoryActive(trace)),
    ),
  };
}

export function useProjectDifficulty(
  project: string,
  initialData?: DifficultyCell[],
) {
  useProjectDataStatus(project, {
    invalidateKeys: [queryKeys.difficulty.all(project)],
  });

  return useQuery({
    queryKey: queryKeys.difficulty.all(project),
    queryFn: () =>
      fetchProjectJson<DifficultyCell[]>("/api/difficulty", project),
    initialData:
      initialData && initialData.length > 0 ? initialData : undefined,
    staleTime: 0,
    refetchOnMount: true,
  });
}

export function useProjectEnvironments(
  project: string,
  initialData?: Array<{ environment: string }>,
) {
  useProjectDataStatus(project, {
    invalidateKeys: [queryKeys.environments.all(project)],
  });

  return useQuery({
    queryKey: queryKeys.environments.all(project),
    queryFn: () =>
      fetchProjectJson<Array<{ environment: string }>>(
        "/api/environments",
        project,
      ),
    initialData:
      initialData && initialData.length > 0 ? initialData : undefined,
    staleTime: 0,
    refetchOnMount: true,
  });
}

export function useProjectSchema(
  project: string,
  initialData?: SchemaColumn[],
) {
  useProjectDataStatus(project, {
    invalidateKeys: [queryKeys.schema.all(project)],
  });

  return useQuery({
    queryKey: queryKeys.schema.all(project),
    queryFn: () => fetchProjectJson<SchemaColumn[]>("/api/schema", project),
    initialData:
      initialData && initialData.length > 0 ? initialData : undefined,
    staleTime: 0,
    refetchOnMount: true,
  });
}

export function useProjectSandboxStatus(
  project: string,
  trajectoryId: string,
  options?: { enabled?: boolean; initialData?: boolean },
) {
  return useQuery({
    queryKey: queryKeys.trajectories.sandboxStatus(project, trajectoryId),
    queryFn: async () => {
      const response = await fetch(
        buildProjectUrl(
          `/api/trajectories/${encodeURIComponent(trajectoryId)}/sandbox-status`,
          project,
        ),
      );
      if (!response.ok) {
        return false;
      }
      const data = (await response.json()) as { running?: boolean };
      return data.running === true;
    },
    enabled: options?.enabled !== false,
    initialData: options?.initialData,
    refetchInterval:
      options?.enabled !== false ? LIVE_TRAJECTORY_POLL_MS : false,
    staleTime: 0,
  });
}

export function useProjectTrajectoryDetail(
  project: string,
  trajectoryId: string,
  initialData?: Trajectory,
) {
  const queryClient = useQueryClient();
  const queryKey = queryKeys.trajectories.detail(project, trajectoryId);

  useProjectDataStatus(project, {
    invalidateKeys: [queryKey],
  });

  const trajectoryQuery = useQuery({
    queryKey,
    queryFn: async (): Promise<Trajectory | null> => {
      const response = await fetch(
        buildProjectUrl(
          `/api/trajectories/${encodeURIComponent(trajectoryId)}`,
          project,
        ),
      );
      if (response.status === 404) {
        const existing = queryClient.getQueryData<Trajectory | null>(queryKey);
        if (existing) {
          return existing;
        }
        return null;
      }
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory");
      }
      return (await response.json()) as Trajectory;
    },
    initialData,
    refetchOnMount: false,
    refetchInterval: (query) => {
      const trajectory = query.state.data;
      if (!trajectory || trajectory.sessionEndReason) {
        return false;
      }
      return LIVE_TRAJECTORY_POLL_MS;
    },
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const trajectory = trajectoryQuery.data;
  const isDone = !!trajectory?.sessionEndReason;
  const sandboxQuery = useProjectSandboxStatus(project, trajectoryId, {
    enabled: trajectory !== undefined && trajectory !== null && !isDone,
  });

  return {
    ...trajectoryQuery,
    trajectory,
    isLive:
      trajectory !== undefined &&
      trajectory !== null &&
      !isDone &&
      sandboxQuery.data === true,
    lastRefreshed: new Date(trajectoryQuery.dataUpdatedAt),
  };
}

export function useProjectTrajectoryCodeHistory(
  project: string,
  trajectoryId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.trajectories.codeHistory(project, trajectoryId),
    queryFn: async () => {
      const data = await fetchProjectJson<Record<string, CodeSnapshot>>(
        `/api/trajectories/${encodeURIComponent(trajectoryId)}/code-history`,
        project,
      );
      const mapped: Record<number, CodeSnapshot> = {};
      for (const [key, snapshot] of Object.entries(data)) {
        mapped[Number(key)] = snapshot;
      }
      return mapped;
    },
    enabled,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
  });
}

export function useProjectTrajectoryLogs(
  project: string,
  trajectoryId: string,
  options?: {
    enabled?: boolean;
    isLive?: boolean;
    fromSeq?: number;
    limit?: number;
  },
) {
  const fromSeq = options?.fromSeq ?? 0;
  const limit = options?.limit ?? 5000;
  const searchParams = new URLSearchParams();
  searchParams.set("fromSeq", `${fromSeq}`);
  searchParams.set("limit", `${limit}`);

  return useQuery({
    queryKey: queryKeys.trajectories.logs(
      project,
      trajectoryId,
      fromSeq,
      limit,
    ),
    queryFn: async () => {
      const response = await fetch(
        buildProjectUrl(
          `/api/trajectories/${encodeURIComponent(trajectoryId)}/logs`,
          project,
          {
            searchParams,
          },
        ),
      );
      if (response.status === 404) {
        return [] as TrajectoryLogRow[];
      }
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory logs");
      }
      const data = (await response.json()) as { rows?: TrajectoryLogRow[] };
      return data.rows ?? [];
    },
    enabled: options?.enabled !== false,
    refetchInterval:
      options?.enabled !== false && options?.isLive === true
        ? LIVE_TRAJECTORY_POLL_MS
        : false,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
  });
}
