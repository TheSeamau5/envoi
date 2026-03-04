import { redirect } from "next/navigation";
import type { NextRequest } from "next/server";
import { readLayoutCookies } from "@/lib/cookies";

/** Read active project from cookie, falling back to ENVOI_PROJECT env. */
export async function getActiveProjectFromCookie(): Promise<
  string | undefined
> {
  const { project } = await readLayoutCookies();
  if (project && project.trim().length > 0) {
    return project;
  }

  const fromEnv = process.env.ENVOI_PROJECT?.trim();
  if (fromEnv && fromEnv.length > 0) {
    return fromEnv;
  }

  return undefined;
}

/** Enforce project selection for project-scoped pages. */
export async function requireActiveProject(): Promise<string> {
  const project = await getActiveProjectFromCookie();
  if (!project) {
    redirect("/");
  }
  return project;
}

/** Resolve project from query string first, then cookie/env fallback. */
export async function getProjectFromRequest(
  request: NextRequest,
): Promise<string | undefined> {
  const fromQuery = request.nextUrl.searchParams.get("project")?.trim();
  if (fromQuery && fromQuery.length > 0) {
    return fromQuery;
  }
  return getActiveProjectFromCookie();
}
