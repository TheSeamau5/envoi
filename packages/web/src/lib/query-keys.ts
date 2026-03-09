/**
 * TanStack Query key factory.
 * Centralizes all query keys so invalidation and prefetching
 * use consistent, type-safe keys throughout the app.
 */

export const queryKeys = {
  trajectories: {
    all: (project: string) => ["trajectories", project] as const,
    live: (project: string, ids: string[]) =>
      ["trajectories", project, "live", ...[...ids].sort()] as const,
    detail: (project: string, id: string) =>
      ["trajectories", project, id] as const,
    codeHistory: (project: string, id: string) =>
      ["trajectories", project, id, "code-history"] as const,
    logs: (project: string, id: string, fromSeq: number, limit: number) =>
      ["trajectories", project, id, "logs", fromSeq, limit] as const,
    sandboxStatus: (project: string, id: string) =>
      ["trajectories", project, id, "sandbox-status"] as const,
  },
  compare: {
    all: (project: string) => ["compare", project] as const,
    full: (project: string) => ["compare", project, "full"] as const,
    byIds: (project: string, ids: string[]) =>
      ["compare", project, ...[...ids].sort()] as const,
  },
  environments: {
    all: (project: string) => ["environments", project] as const,
  },
  difficulty: {
    all: (project: string) => ["difficulty", project] as const,
  },
  revision: {
    status: (project: string) => ["revision", project] as const,
  },
  schema: {
    all: (project: string) => ["schema", project] as const,
  },
} as const;
