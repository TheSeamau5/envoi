import { describe, expect, it } from "vitest";

import { ensureQueryValue } from "../project-data";

describe("project data runtime guards", () => {
  it("throws for undefined query results with the affected key", () => {
    expect(() =>
      ensureQueryValue(undefined, ["trajectories", "c-compiler", "traj-1"]),
    ).toThrowError(
      'Query data cannot be undefined. Affected query key: ["trajectories","c-compiler","traj-1"]',
    );
  });

  it("passes through null and concrete values", () => {
    expect(ensureQueryValue(null, ["trajectory"])).toBeNull();
    expect(ensureQueryValue("ok", ["trajectory"])).toBe("ok");
  });
});
