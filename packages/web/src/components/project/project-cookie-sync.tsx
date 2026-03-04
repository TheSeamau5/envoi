"use client";

import { useEffect } from "react";
import { setProjectCookie } from "@/lib/cookies.client";

type ProjectCookieSyncProps = {
  project: string;
};

export function ProjectCookieSync({ project }: ProjectCookieSyncProps) {
  useEffect(() => {
    setProjectCookie(project);
  }, [project]);

  return null;
}
