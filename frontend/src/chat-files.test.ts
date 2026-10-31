import { describe, expect, it } from "vitest";

import { clipAttachedFiles } from "./chat-files";

function named(name: string): File {
  return new File(["x"], name, { type: "application/octet-stream" });
}

describe("clipAttachedFiles", () => {
  it("keeps documents and only max_images pictures", () => {
    const picked = [
      named("a.png"), named("b.jpg"), named("notes.pdf"),
      named("c.webp"), named("d.jpeg"), named("e.png"), named("spec.txt"),
    ];
    const clipped = clipAttachedFiles(picked, 3);
    expect(clipped.overflow).toBe(true);
    expect(clipped.files.map((file) => file.name)).toEqual([
      "a.png", "b.jpg", "notes.pdf", "c.webp", "spec.txt",
    ]);
  });

  it("does not invent a limit when the selection fits", () => {
    const clipped = clipAttachedFiles([named("a.png"), named("b.pdf")], 5);
    expect(clipped.overflow).toBe(false);
    expect(clipped.files.map((file) => file.name)).toEqual(["a.png", "b.pdf"]);
  });
});
