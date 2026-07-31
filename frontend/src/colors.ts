/** Unreal-ish node header palette; each loaded file gets a stable color. */
const PALETTE = [
  "#2f81f7", // blueprint blue
  "#1fb2a6", // teal
  "#d29922", // amber
  "#a371f7", // violet
  "#db6d8f", // rose
  "#57ab5a", // green
  "#e8794a", // ember
  "#58a6ff", // sky
];

export function fileColor(file: string): string {
  let hash = 0;
  for (let i = 0; i < file.length; i++) hash = (hash * 31 + file.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}
