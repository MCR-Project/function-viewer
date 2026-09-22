/** Frontend mirror of backend/languages/: every language the app can read, in one place. */
export interface LanguageDef {
  id: string;
  label: string;
  extensions: string[];
  color: string;
  mono: string;
  /** Prism grammar to highlight with, when it isn't named like the language id. */
  prism?: string;
  /** What a card shows after `→` for a function with no return annotation ("None" when unset); null hides the arrow. */
  unannotatedReturn?: string | null;
}

export const LANGUAGES: LanguageDef[] = [
  { id: "python", label: "Python", extensions: [".py"], color: "#3776ab", mono: "PY" },
  { id: "rust", label: "Rust", extensions: [".rs"], color: "#dea584", mono: "RS" },
  // The superset grammars: they also cover JSX/TSX markup, and highlight plain code the same.
  // A missing return annotation there means "not stated", not "returns nothing".
  {
    id: "typescript",
    label: "TypeScript",
    extensions: [".ts", ".tsx", ".mts", ".cts"],
    color: "#3178c6",
    mono: "TS",
    prism: "tsx",
    unannotatedReturn: null,
  },
  {
    id: "javascript",
    label: "JavaScript",
    extensions: [".js", ".jsx", ".mjs", ".cjs"],
    color: "#f7df1e",
    mono: "JS",
    prism: "jsx",
    unannotatedReturn: null,
  },
];

/** Flat extension list, also used as the file picker's `accept` attribute. */
export const SUPPORTED_EXTENSIONS: string[] = LANGUAGES.flatMap((lang) => lang.extensions);

const BY_ID = new Map(LANGUAGES.map((lang) => [lang.id, lang]));
const BY_EXTENSION = new Map(LANGUAGES.flatMap((lang) => lang.extensions.map((ext) => [ext, lang] as const)));

export function languageForId(id: string | null | undefined): LanguageDef | undefined {
  return id ? BY_ID.get(id) : undefined;
}

export function languageForPath(path: string): LanguageDef | undefined {
  const dot = path.lastIndexOf(".");
  return dot === -1 ? undefined : BY_EXTENSION.get(path.slice(dot));
}
