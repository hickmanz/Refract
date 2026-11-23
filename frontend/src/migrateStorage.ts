// The app was called LightSim before it was Refract: carry the saved UI settings over to the new key names.
// Imported first in main.ts so it runs before any module reads localStorage.
const KEYS = ["theme", "solver", "planeHandle", "sectionCaps"];

try {
  for (const k of KEYS) {
    const old = localStorage.getItem(`lightsim.${k}`);
    if (old !== null && localStorage.getItem(`refract.${k}`) === null) localStorage.setItem(`refract.${k}`, old);
    localStorage.removeItem(`lightsim.${k}`);
  }
} catch {
  // storage unavailable: nothing to carry over
}
