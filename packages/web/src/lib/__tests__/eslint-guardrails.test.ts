import { describe, expect, it } from "vitest";

import eslintConfig from "../../../eslint.config.mjs";

describe("eslint freshness guardrails", () => {
  it("restricts direct project data query imports outside the central module", () => {
    const configEntries = Array.isArray(eslintConfig)
      ? eslintConfig
      : [eslintConfig];
    const restrictedEntry = configEntries.find((entry) => {
      if (
        typeof entry !== "object" ||
        entry === null ||
        !("rules" in entry) ||
        typeof entry.rules !== "object" ||
        entry.rules === null
      ) {
        return false;
      }
      return "no-restricted-imports" in entry.rules;
    });

    expect(restrictedEntry).toBeDefined();
    const rules =
      restrictedEntry &&
      typeof restrictedEntry === "object" &&
      "rules" in restrictedEntry &&
      typeof restrictedEntry.rules === "object" &&
      restrictedEntry.rules !== null
        ? restrictedEntry.rules
        : {};
    const setting = rules["no-restricted-imports"];

    expect(Array.isArray(setting)).toBe(true);
    const config = Array.isArray(setting) ? setting[1] : undefined;
    const paths =
      config &&
      typeof config === "object" &&
      "paths" in config &&
      Array.isArray(config.paths)
        ? config.paths
        : [];

    expect(
      paths.some(
        (path) =>
          typeof path === "object" &&
          path !== null &&
          "name" in path &&
          path.name === "@tanstack/react-query",
      ),
    ).toBe(true);
    expect(
      paths.some(
        (path) =>
          typeof path === "object" &&
          path !== null &&
          "name" in path &&
          path.name === "@/lib/query-keys",
      ),
    ).toBe(true);
  });
});
