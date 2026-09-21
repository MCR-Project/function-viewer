import { create } from "zustand";

export type Theme = "light" | "dark";
export type ThemePreference = "system" | "light" | "dark";

/** Click order of the topbar toggle. */
const ORDER: ThemePreference[] = ["system", "light", "dark"];

// The inline script in index.html applies the theme before first paint using
// this same key and the same resolution rules. Change one, change the other.
const STORAGE_KEY = "fv-theme-preference";
const LIGHT_QUERY = "(prefers-color-scheme: light)";

function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // storage blocked or unavailable: fall through to System
  }
  return "system";
}

function writePreference(preference: ThemePreference) {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // storage blocked or unavailable: the choice just won't survive a reload
  }
}

/** The System theme: Light only when the OS asks for it; no preference means Dark. */
function readSystemTheme(): Theme {
  return typeof matchMedia === "function" && matchMedia(LIGHT_QUERY).matches ? "light" : "dark";
}

function resolveTheme(preference: ThemePreference, system: Theme): Theme {
  return preference === "system" ? system : preference;
}

export function nextPreference(preference: ThemePreference): ThemePreference {
  return ORDER[(ORDER.indexOf(preference) + 1) % ORDER.length];
}

interface ThemeState {
  preference: ThemePreference;
  /** What the OS currently asks for; only shown while the preference is System. */
  system: Theme;
  cycle: () => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  preference: readPreference(),
  system: readSystemTheme(),
  cycle: () => {
    const preference = nextPreference(get().preference);
    writePreference(preference);
    set({ preference });
  },
}));

/** The Theme actually on screen. */
export const selectTheme = (s: ThemeState): Theme => resolveTheme(s.preference, s.system);

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

applyTheme(selectTheme(useTheme.getState()));
useTheme.subscribe((state) => applyTheme(selectTheme(state)));

if (typeof matchMedia === "function") {
  matchMedia(LIGHT_QUERY).addEventListener("change", (e) => {
    useTheme.setState({ system: e.matches ? "light" : "dark" });
  });
}
