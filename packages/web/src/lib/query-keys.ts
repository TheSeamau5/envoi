/**
 * TanStack Query key factory.
 * Centralizes all query keys so invalidation and prefetching
 * use consistent, type-safe keys throughout the app.
 */

export const queryKeys = {
  trajectories: {
    all: (project: string) => ["trajectories", project] as const,
    detail: (project: string, id: string) =>
      ["trajectories", project, id] as const,
    codeHistory: (project: string, id: string) =>
      ["trajectories", project, id, "code-history"] as const,
    sandboxStatus: (project: string, id: string) =>
      ["trajectories", project, id, "sandbox-status"] as const,
  },
  compare: {
    all: (project: string) => ["compare", project] as const,
    byIds: (project: string, ids: string[]) =>
      ["compare", project, ...[...ids].sort()] as const,
  },
} as const;
