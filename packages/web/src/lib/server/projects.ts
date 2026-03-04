import type { Project } from "@/lib/types";
import {
  createProjectMeta,
  getProjectMeta,
  isS3Configured,
  listProjects as listProjectMeta,
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

/**
 * List all projects with metadata.
 * Only reads project.json files from S3 — no DuckDB queries, no S3 sync.
 * Stats (trajectory/environment/model counts) are NOT loaded here because
 * each would require switching the DuckDB singleton to that project,
 * triggering a full S3 sync per project (~4-6s each).
 */
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
  const projects = metas.map((meta) => ({
    name: meta.name,
    description: meta.description,
    createdAt: meta.created_at,
    updatedAt: meta.updated_at,
    trajectoryCount: 0,
    environmentCount: 0,
    modelCount: 0,
  } satisfies Project));

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
