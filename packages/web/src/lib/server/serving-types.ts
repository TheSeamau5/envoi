import type {
  CodeSnapshot,
  Project,
  Trajectory,
  TrajectoryLogRow,
} from "@/lib/types";

export type ServingObjectRef = {
  key: string;
  eTag?: string;
  sizeBytes: number;
  contentEncoding?: string;
};

export type ServingAgentSummary = {
  model: string;
  count: number;
};

export type ServingTrajectoryArtifacts = {
  detail: ServingObjectRef;
  logs?: ServingObjectRef;
  codeHistory?: ServingObjectRef[];
};

export type ServingManifest = {
  project: string;
  revision: string;
  publishedAt: string;
  trajectoryCount: number;
  liveTrajectoryCount: number;
  agents: ServingAgentSummary[];
  objects: {
    trajectoriesIndex: ServingObjectRef;
    compareIndex: ServingObjectRef;
    setupsIndex: ServingObjectRef;
    liveIndex: ServingObjectRef;
    trajectories: Record<string, ServingTrajectoryArtifacts>;
  };
};

export type ServingLiveIndex = {
  revision: string;
  updatedAt: string;
  trajectoryIds: string[];
  liveTrajectoryCount: number;
};

export type ServingTrajectoryDetail = {
  revision: string;
  trajectory: Trajectory;
};

export type ServingLogPage = {
  revision: string;
  rows: TrajectoryLogRow[];
};

export type ServingCodeHistoryChunk = {
  revision: string;
  chunkIndex: number;
  codeHistory: Record<number, CodeSnapshot>;
};

export type ServingProjectSnapshot = {
  project: Project;
  manifest: ServingManifest;
  trajectories: Trajectory[];
  compare: Trajectory[];
  setups: Trajectory[];
  live: ServingLiveIndex;
  details: Map<string, Trajectory>;
};
