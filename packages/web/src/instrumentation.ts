/**
 * Next.js instrumentation — runs once at server start.
 * BLOCKS until ALL project data is loaded into DuckDB and cached.
 * After register() completes, every page renders with real data instantly.
 *
 * All Node.js imports are dynamic to avoid Edge runtime bundling errors.
 */

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "edge") {
    return;
  }

  const { readdir } = await import("node:fs/promises");
  const path = await import("node:path");

  const cacheBase = path.resolve(process.cwd(), ".cache");
  const projectSet = new Set<string>();

  try {
    const parquetDirs = await readdir(path.join(cacheBase, "parquet"));
    for (const dir of parquetDirs) {
      projectSet.add(dir);
    }
  } catch {
    // No parquet cache yet
  }

  try {
    const duckFiles = await readdir(path.join(cacheBase, "duckdb"));
    for (const file of duckFiles) {
      if (file.endsWith(".duckdb")) {
        projectSet.add(file.replace(/\.duckdb$/, ""));
      }
    }
  } catch {
    // No DuckDB cache yet
  }

  try {
    const { listProjects, isS3Configured } = await import("@/lib/server/db");
    if (isS3Configured()) {
      const s3Projects = await listProjects();
      for (const meta of s3Projects) {
        projectSet.add(meta.name);
      }
    }
  } catch {
    // S3 not available or misconfigured
  }

  if (projectSet.size === 0) {
    return;
  }

  const projects = [...projectSet];
  const { getDb } = await import("@/lib/server/db");
  const {
    getAllTrajectories,
    getTrajectoryById,
    getCompareTrajectories,
  } = await import("@/lib/server/data");

  const startedAt = Date.now();
  console.log(
    `[instrumentation] loading ALL data for ${projects.length} project(s): ${projects.join(", ")}`,
  );

  for (const project of projects) {
    try {
      await getDb(project);

      const trajectories = await getAllTrajectories({ project });
      await Promise.all(
        trajectories.map((trajectory) =>
          getTrajectoryById(trajectory.id, { project }).catch(() => {}),
        ),
      );
      await getCompareTrajectories({ project }).catch(() => {});

      console.log(
        `[instrumentation] ${project} done: ${trajectories.length} trajectories (${Date.now() - startedAt}ms)`,
      );
    } catch (error) {
      console.warn(
        `[instrumentation] ${project} failed:`,
        error instanceof Error ? error.message : error,
      );
    }
  }

  console.log(
    `[instrumentation] ALL DATA READY (${Date.now() - startedAt}ms)`,
  );
}
