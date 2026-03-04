import type { Project } from "@/lib/types";
import {
  createProjectMeta,
  getProjectMeta,
  isS3Configured,
  listProjects as listProjectMeta,
  query,
} from "./db";

function normalizeDescription(value: string | undefined): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return undefined;
  }
  return trimmed;
}

function validateProjectName(name: string): string {
  const value = name.trim();
  if (value.length < 2 || value.length > 64) {
    throw new Error("Project name must be between 2 and 64 characters");
  }
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(value)) {
    throw new Error("Project name must match [a-z0-9][a-z0-9-]*[a-z0-9]");
  }
  return value;
}

/** Compute quick summary stats for a project from materialized trajectories. */
async function loadProjectStats(project: string): Promise<{
  trajectoryCount: number;
  environmentCount: number;
  modelCount: number;
}> {
  try {
    const rows = await query(
      `
        SELECT
          COUNT(*) AS trajectory_count,
          COUNT(DISTINCT environment) AS environment_count,
          COUNT(DISTINCT agent_model) AS model_count
        FROM trajectories
      `,
      project,
    );
    const row = rows[0] ?? {};
    return {
      trajectoryCount: Number(row.trajectory_count ?? 0),
      environmentCount: Number(row.environment_count ?? 0),
      modelCount: Number(row.model_count ?? 0),
    };
  } catch {
    return {
      trajectoryCount: 0,
      environmentCount: 0,
      modelCount: 0,
    };
  }
}

/** List all projects with metadata and lightweight aggregate stats. */
export async function getProjects(): Promise<Project[]> {
  if (!isS3Configured()) {
    const now = new Date().toISOString();
    return [
      {
        name: "mock",
        description: "Local mock data",
        createdAt: now,
        updatedAt: now,
        trajectoryCount: 0,
        environmentCount: 0,
        modelCount: 0,
      },
    ];
  }

  const metas = await listProjectMeta();
  const projects = await Promise.all(
    metas.map(async (meta) => {
      const stats = await loadProjectStats(meta.name);
      return {
        name: meta.name,
        description: meta.description,
        createdAt: meta.created_at,
        updatedAt: meta.updated_at,
        trajectoryCount: stats.trajectoryCount,
        environmentCount: stats.environmentCount,
        modelCount: stats.modelCount,
      } satisfies Project;
    }),
  );

  return projects.sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

/** Create a new project and persist project.json in S3. */
export async function createProject(
  name: string,
  description?: string,
): Promise<Project> {
  if (!isS3Configured()) {
    throw new Error("S3 is not configured");
  }

  const projectName = validateProjectName(name);
  const normalizedDescription = normalizeDescription(description);

  const existing = await getProjectMeta(projectName);
  if (existing) {
    throw new Error("Project already exists");
  }

  const created = await createProjectMeta(projectName, {
    description: normalizedDescription,
  });

  return {
    name: created.name,
    description: created.description,
    createdAt: created.created_at,
    updatedAt: created.updated_at,
    trajectoryCount: 0,
    environmentCount: 0,
    modelCount: 0,
  };
}
