import { ref } from "vue";
import * as radix from "@radix-ui/colors";

export type ThemeMode = "light" | "dark" | "system";

export interface AccentOption {
  id: string;
  label: string;
  /** Radix scale the accent steps come from. */
  scale: string;
  /** Swatch colour shown in the picker (the step-9 colour in dark mode). */
  swatch: string;
}

export const ACCENTS: AccentOption[] = [
  { id: "pink", label: "Pink", scale: "pink", swatch: "#ec4899" },
  { id: "violet", label: "Violet", scale: "violet", swatch: "#6e56cf" },
  { id: "blue", label: "Blue", scale: "blue", swatch: "#0090ff" },
  { id: "cyan", label: "Cyan", scale: "cyan", swatch: "#00a2c7" },
  { id: "green", label: "Green", scale: "grass", swatch: "#46a758" },
  { id: "orange", label: "Orange", scale: "orange", swatch: "#f76b15" },
];

const THEME_KEY = "refract.theme";
const ACCENT_KEY = "refract.accent";

const savedMode = localStorage.getItem(THEME_KEY) as ThemeMode | null;
const savedAccent = localStorage.getItem(ACCENT_KEY);

// Dark is the default look; "system" is still available as a choice.
export const themeMode = ref<ThemeMode>(savedMode === "light" || savedMode === "dark" || savedMode === "system" ? savedMode : "dark");
export const accentId = ref<string>(ACCENTS.some((a) => a.id === savedAccent) ? (savedAccent as string) : "pink");
export const isDark = ref(true);

/** Graphite: near-neutral greys with a trace of cool blue, 12 steps with the Radix meaning (1 app background ... 12 text). */
const GRAPHITE_DARK = [0.19, 0.228, 0.262, 0.292, 0.327, 0.372, 0.44, 0.53, 0.63, 0.72, 0.83, 0.95];
const GRAPHITE_LIGHT = [0.985, 0.972, 0.955, 0.935, 0.91, 0.88, 0.83, 0.74, 0.58, 0.51, 0.41, 0.22];

function graphite(dark: boolean): string[] {
  const steps = dark ? GRAPHITE_DARK : GRAPHITE_LIGHT;
  return steps.map((l, i) => `oklch(${l} ${i < 9 ? 0.005 : 0.004} 265)`);
}

function accentScale(a: AccentOption, dark: boolean): string[] {
  const table = radix as unknown as Record<string, Record<string, string>>;
  const obj = table[dark ? `${a.scale}Dark` : a.scale];
  const steps = Array.from({ length: 12 }, (_, i) => obj[`${a.scale}${i + 1}`]);
  if (a.id === "pink") {
    // a livelier pink than Radix's step 9, matching the logo
    steps[8] = dark ? "#ec4899" : "#db2777";
    steps[9] = dark ? "#f472b6" : "#be185d";
  }
  return steps;
}

/** Writes the --accent-N / --neutral-N variables every stylesheet relies on. */
function applyScales(dark: boolean) {
  const el = document.documentElement;
  const accent = accentScale(ACCENTS.find((a) => a.id === accentId.value) ?? ACCENTS[0], dark);
  const neutral = graphite(dark);
  for (let i = 0; i < 12; i++) {
    el.style.setProperty(`--accent-${i + 1}`, accent[i]);
    el.style.setProperty(`--neutral-${i + 1}`, neutral[i]);
  }
  // text on a filled accent: every offered accent is dark enough for white
  el.style.setProperty("--on-accent", "#ffffff");
}

export function applyTheme() {
  const dark =
    themeMode.value === "dark" ||
    (themeMode.value === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  isDark.value = dark;
  applyScales(dark);
}

export function setThemeMode(mode: ThemeMode) {
  themeMode.value = mode;
  localStorage.setItem(THEME_KEY, mode);
  applyTheme();
}

export function setAccent(id: string) {
  if (!ACCENTS.some((a) => a.id === id)) return;
  accentId.value = id;
  localStorage.setItem(ACCENT_KEY, id);
  applyTheme();
}

/** Background of the 3D viewport: a touch lighter than the panels around it in dark, a soft grey in light. */
export function viewportBackground(): string {
  return isDark.value ? "#25272b" : "#e4e5e8";
}

export function toggleTheme() {
  setThemeMode(isDark.value ? "light" : "dark");
}

export function initTheme() {
  applyTheme();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (themeMode.value === "system") applyTheme();
  });
}
