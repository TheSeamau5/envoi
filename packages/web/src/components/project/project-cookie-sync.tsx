"use client";

import { useEffect } from "react";
import { setProjectCookie } from "@/lib/cookies.client";

type ProjectCookieSyncProps = {
  project: string;
};

export function ProjectCookieSync({ project }: ProjectCookieSyncProps) {
  console.log("[DEBUG] ProjectCookieSync render");
  useEffect(() => {
    setProjectCookie(project);
  }, [project]);

  return null;
}
