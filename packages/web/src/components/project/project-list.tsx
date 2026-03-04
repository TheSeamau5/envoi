"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUpRight } from "lucide-react";
import type { Project } from "@/lib/types";
import { setProjectCookie } from "@/lib/cookies.client";
import { CreateProjectDialog } from "./create-project-dialog";

type ProjectListProps = {
  projects: Project[];
  activeProject?: string;
};

const HEADER_STYLE =
  "text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim whitespace-nowrap";

function formatUpdated(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
  });
}

/** Project list table with inline creation. */
export function ProjectList({ projects, activeProject }: ProjectListProps) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [rows, setRows] = useState(projects);

  const sortedRows = useMemo(
    () =>
      [...rows].sort((left, right) =>
        right.updatedAt.localeCompare(left.updatedAt),
      ),
    [rows],
  );

  function openProject(project: string): void {
    setProjectCookie(project);
    router.push(`/project/${encodeURIComponent(project)}`);
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center justify-between border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Projects
        </span>
        <button
          type="button"
          onClick={() => setCreating((prev) => !prev)}
          className="h-7 rounded border border-envoi-border px-2 text-[12px] text-envoi-text-dim transition-colors hover:border-envoi-accent hover:text-envoi-accent"
        >
          + New Project
        </button>
      </div>

      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5 py-1.5">
        <span className={`min-w-0 flex-1 px-3 ${HEADER_STYLE}`}>Name</span>
        <span className={`w-32 shrink-0 px-3 ${HEADER_STYLE}`}>
          Trajectories
        </span>
        <span className={`w-32 shrink-0 px-3 ${HEADER_STYLE}`}>
          Environments
        </span>
        <span className={`w-22 shrink-0 px-3 ${HEADER_STYLE}`}>Models</span>
        <span className={`w-24 shrink-0 px-3 text-right ${HEADER_STYLE}`}>
          Updated
        </span>
        <span className="w-6 shrink-0" />
      </div>

      <div className="flex-1 overflow-y-auto">
        {creating && (
          <CreateProjectDialog
            onCreated={(project) => {
              setRows((prev) => [
                project,
                ...prev.filter((row) => row.name !== project.name),
              ]);
              setCreating(false);
            }}
            onCancel={() => setCreating(false)}
          />
        )}

        {sortedRows.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-[13px] text-envoi-text-dim">
            <span>No projects yet</span>
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="h-8 rounded border border-envoi-border px-3 text-[12px] transition-colors hover:border-envoi-accent hover:text-envoi-accent"
            >
              Create your first project
            </button>
          </div>
        ) : (
          <>
            {sortedRows.map((project) => {
              const isActive = activeProject === project.name;
              return (
                <button
                  key={project.name}
                  type="button"
                  onClick={() => openProject(project.name)}
                  className={`flex w-full items-center border-b border-envoi-border-light px-3.5 py-2.5 text-left transition-colors hover:bg-envoi-surface ${
                    isActive ? "bg-envoi-surface" : ""
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate px-3 text-[13px] font-medium text-envoi-text hover:text-envoi-accent">
                    {project.name}
                  </span>
                  <span className="w-32 shrink-0 px-3 text-[12px] text-envoi-text-muted">
                    {project.trajectoryCount}
                  </span>
                  <span className="w-32 shrink-0 px-3 text-[12px] text-envoi-text-muted">
                    {project.environmentCount}
                  </span>
                  <span className="w-22 shrink-0 px-3 text-[12px] text-envoi-text-muted">
                    {project.modelCount}
                  </span>
                  <span className="w-24 shrink-0 px-3 text-right text-[12px] text-envoi-text-muted">
                    {formatUpdated(project.updatedAt)}
                  </span>
                  <span className="flex w-6 shrink-0 items-center justify-end text-envoi-text-dim">
                    <ArrowUpRight size={14} />
                  </span>
                </button>
              );
            })}
            <div className="px-3.5 py-4 text-center text-[12px] uppercase tracking-[0.08em] text-envoi-text-dim">
              no more projects
            </div>
          </>
        )}
      </div>
    </div>
  );
}
