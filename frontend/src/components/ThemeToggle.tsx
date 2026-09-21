import { nextPreference, selectTheme, useTheme, type ThemePreference } from "../theme";

const GLYPHS: Record<ThemePreference, string> = { system: "◐", light: "☀", dark: "☾" };
const NAMES: Record<ThemePreference, string> = { system: "System", light: "Light", dark: "Dark" };

export function ThemeToggle() {
  const preference = useTheme((s) => s.preference);
  const theme = useTheme(selectTheme);
  const cycle = useTheme((s) => s.cycle);

  const current = preference === "system" ? `System (currently ${NAMES[theme]})` : NAMES[preference];

  return (
    <button
      className="btn theme-toggle"
      title={`Theme: ${current}. Click for ${NAMES[nextPreference(preference)]}.`}
      onClick={cycle}
    >
      {GLYPHS[preference]} {NAMES[preference]}
    </button>
  );
}
