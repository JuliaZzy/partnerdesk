/** Image types the chat composer sends as data URLs. */

export const CHAT_IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp"]);
export const CHAT_DOC_EXTS = new Set(["pdf", "docx", "txt", "md"]);

export function fileExt(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

export function clipAttachedFiles(files: File[], maxImages: number): { files: File[]; overflow: boolean } {
  const kept: File[] = [];
  let images = 0;
  let overflow = false;
  for (const file of files) {
    if (CHAT_IMAGE_EXTS.has(fileExt(file.name))) {
      if (images >= maxImages) {
        overflow = true;
        continue;
      }
      images += 1;
    }
    kept.push(file);
  }
  return { files: kept, overflow };
}
