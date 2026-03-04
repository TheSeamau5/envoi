"use client";

import { useState } from "react";
import type { Project } from "@/lib/types";

type CreateProjectDialogProps = {
  onCreated: (project: Project) => void;
  onCancel: () => void;
};

/** Inline project creation row. */
export function CreateProjectDialog({
  onCreated,
  onCancel,
}: CreateProjectDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  const canSubmit =
    !saving && name.trim().length >= 2 && name.trim().length <= 64;

  async function createProject(): Promise<void> {
    if (!canSubmit) {
      return;
    }

    setSaving(true);
    setError(undefined);
    try {
      const response = await fetch("/api/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || undefined,
        }),
      });

      const payload: unknown = await response.json();
      if (!response.ok) {
        const rawError =
          typeof payload === "object" && payload !== null
            ? Reflect.get(payload, "error")
            : undefined;
        setError(
          typeof rawError === "string" ? rawError : "Failed to create project",
        );
        return;
      }

      const rawProject =
        typeof payload === "object" && payload !== null
          ? Reflect.get(payload, "project")
          : undefined;
      if (
        typeof rawProject === "object" &&
        rawProject !== null &&
        typeof Reflect.get(rawProject, "name") === "string" &&
        typeof Reflect.get(rawProject, "createdAt") === "string" &&
        typeof Reflect.get(rawProject, "updatedAt") === "string"
      ) {
        const project: Project = {
          name: String(Reflect.get(rawProject, "name")),
          description:
            typeof Reflect.get(rawProject, "description") === "string"
              ? String(Reflect.get(rawProject, "description"))
              : undefined,
          createdAt: String(Reflect.get(rawProject, "createdAt")),
          updatedAt: String(Reflect.get(rawProject, "updatedAt")),
          trajectoryCount: Number(
            Reflect.get(rawProject, "trajectoryCount") ?? 0,
          ),
          environmentCount: Number(
            Reflect.get(rawProject, "environmentCount") ?? 0,
          ),
          modelCount: Number(Reflect.get(rawProject, "modelCount") ?? 0),
        };
        onCreated(project);
      }
    } catch {
      setError("Network error while creating project");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-b border-envoi-border-light bg-envoi-bg px-3.5 py-3">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] items-start gap-2">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="project-name"
          className="h-8 rounded border border-envoi-border bg-envoi-bg px-2 text-[12px] text-envoi-text outline-none focus:border-envoi-accent"
        />
        <input
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Optional description"
          className="h-8 rounded border border-envoi-border bg-envoi-bg px-2 text-[12px] text-envoi-text outline-none focus:border-envoi-accent"
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              void createProject();
            }}
            disabled={!canSubmit}
            className="h-8 rounded border border-envoi-border px-3 text-[12px] text-envoi-text-dim transition-colors hover:border-envoi-accent hover:text-envoi-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Creating..." : "Create"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="h-8 rounded border border-transparent px-2 text-[12px] text-envoi-text-dim transition-colors hover:text-envoi-text"
          >
            Cancel
          </button>
        </div>
      </div>
      {error && <p className="mt-2 text-[12px] text-red-600">{error}</p>}
    </div>
  );
}
