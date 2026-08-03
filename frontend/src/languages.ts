/** Frontend mirror of backend/languages/: every language the app can read, in one place. */
export interface LanguageDef {
  id: string;
  label: string;
  extensions: string[];
  color: string;
  mono: string;
}

export const LANGUAGES: LanguageDef[] = [
  { id: "python", label: "Python", extensions: [".py"], color: "#3776ab", mono: "PY" },
  { id: "rust", label: "Rust", extensions: [".rs"], color: "#dea584", mono: "RS" },
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
