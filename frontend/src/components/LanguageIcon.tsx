import { languageForId } from "../languages";

/** Small colored monogram badge next to a file - avoids maintaining a real logo per language. */
export function LanguageIcon({ id }: { id: string | null | undefined }) {
  const lang = languageForId(id);
  if (!lang) return null;
  return (
    <span className="lang-icon" style={{ background: lang.color }} title={lang.label}>
      {lang.mono}
    </span>
  );
}
