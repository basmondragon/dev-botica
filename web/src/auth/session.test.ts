import { describe, expect, it } from "vitest";
import { returnPath } from "./session";

describe("the sign-in return path", () => {
  it("keeps a path back into this application", () => {
    expect(returnPath("/inventory")).toBe("/inventory");
    expect(returnPath("/dashboard?settings=people")).toBe(
      "/dashboard?settings=people",
    );
  });

  it("refuses anything that leaves the origin", () => {
    expect(returnPath("https://evil.example/x")).toBeUndefined();
    expect(returnPath("//evil.example/x")).toBeUndefined();
    expect(returnPath("/\\evil.example/x")).toBeUndefined();
    expect(returnPath(42)).toBeUndefined();
  });

  it("refuses the sign-in page itself", () => {
    expect(returnPath("/login")).toBeUndefined();
    expect(returnPath("/login?next=/inventory")).toBeUndefined();
  });
});
