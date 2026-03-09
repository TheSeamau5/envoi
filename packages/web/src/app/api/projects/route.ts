import { NextRequest, NextResponse } from "next/server";
import { getProjectsForUi } from "@/lib/server/project-snapshot-store";
import { createProject } from "@/lib/server/projects";

/** GET /api/projects — list all projects. */
export async function GET() {
  try {
    const projects = await getProjectsForUi();
    return NextResponse.json({ projects });
  } catch (error) {
    console.error("GET /api/projects error:", error);
    return NextResponse.json(
      { error: "Failed to list projects" },
      { status: 500 },
    );
  }
}

/** POST /api/projects — create a new project. */
export async function POST(request: NextRequest) {
  try {
    const payload: unknown = await request.json();
    if (typeof payload !== "object" || payload === null) {
      return NextResponse.json(
        { error: "Invalid request body" },
        { status: 400 },
      );
    }

    const rawName = Reflect.get(payload, "name");
    const rawDescription = Reflect.get(payload, "description");
    const name = typeof rawName === "string" ? rawName : "";
    const description =
      typeof rawDescription === "string" ? rawDescription : undefined;

    const project = await createProject(name, description);
    return NextResponse.json({ project }, { status: 201 });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to create project";
    const status = message === "Project already exists" ? 409 : 400;
    return NextResponse.json({ error: message }, { status });
  }
}
