/**
 * Next.js instrumentation hook.
 *
 * We warm the serving snapshot store for non-legacy projects at startup. The
 * previous eager prewarm path pulled every project and every major page shape
 * through the old DuckDB/local-cache stack, which amplified stale state and
 * duplicated failures. The startup warm path now lives in a separate
 * server-only module so this file stays Edge-safe.
 */

/** Warm serving snapshots before the server starts serving critical UI routes. */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "edge") {
    return;
  }

  const { warmProjectSnapshotStore } = await import(
    "@/lib/server/project-snapshot-store"
  );
  void warmProjectSnapshotStore().catch((error: unknown) => {
    console.warn(
      "[instrumentation] background snapshot warm failed:",
      error instanceof Error ? error.message : error,
    );
  });
}
