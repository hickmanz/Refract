// Client-side mirror of the server's part matching, so the UI can show what a setup resolves to.
import type { LedDef, PartDoc, PartRule, Setup, Sig } from "../api";

/** Escape fnmatch special characters in a literal name. */
export function esc(text: string): string {
  return text.replace(/([[\]*?])/g, "[$1]");
}

/** fnmatch (case-insensitive) -> RegExp. */
export function fnmatchRegex(pattern: string): RegExp {
  let out = "";
  const p = pattern.trim().toLowerCase();
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (c === "*") out += ".*";
    else if (c === "?") out += ".";
    else if (c === "[") {
      const j = p.indexOf("]", i + 2);
      if (j < 0) out += "\\[";
      else {
        let cls = p.slice(i + 1, j);
        let neg = false;
        if (cls.startsWith("!")) {
          neg = true;
          cls = cls.slice(1);
        }
        out += `[${neg ? "^" : ""}${cls.replace(/[\\^\]]/g, "\\$&")}]`;
        i = j;
      }
    } else out += c.replace(/[.+^${}()|\\]/g, "\\$&");
  }
  return new RegExp(`^${out}$`, "s");
}

export function nameMatches(pattern: string, inst: PartDoc): boolean {
  if (!pattern.trim()) return false;
  const re = fnmatchRegex(pattern);
  return [inst.path, inst.name, inst.partName ?? ""].some((c) => re.test(c.toLowerCase()));
}

/** A part pattern that survives revisions: the instance name when unique, else the path below the root. */
export function partPattern(inst: PartDoc, all: PartDoc[]): string {
  if (all.filter((i) => i.name === inst.name).length === 1) return esc(inst.name);
  const segs = inst.path.split("/");
  return segs.length > 1 ? `*/${esc(segs.slice(1).join("/"))}` : esc(inst.path);
}

/** An opaque surface material (colour + reflectances) as opposed to a volume material (index, absorption, scattering). */
export function isSurfaceMaterial(doc: { mode?: string } | null | undefined): boolean {
  return doc?.mode === "surface";
}

/** Is this material id a volume (transparent, diffusing or translucent) material in the library? */
export function isVolumeMaterial(lib: { materials: Record<string, { mode?: string }> } | null | undefined, id: string | null | undefined): boolean {
  return !!id && !!lib?.materials[id] && !isSurfaceMaterial(lib.materials[id]);
}

/** A face override value that stands for an opaque surface material rather than a finish. */
export const MAT_PREFIX = "mat:";

export interface EffectivePart {
  enabled: boolean;
  material: string | null;
  finish: string;
  faces: Record<string, string>;
}

export function resolvePart(inst: PartDoc, rules: PartRule[]): EffectivePart {
  const out: EffectivePart = { enabled: true, material: null, finish: "black", faces: {} };
  for (const r of rules) {
    if (!nameMatches(r.match, inst)) continue;
    if (r.enabled !== undefined) out.enabled = r.enabled;
    if (r.material !== undefined) out.material = r.material;
    if (r.finish) out.finish = r.finish;
    Object.assign(out.faces, r.faces ?? {});
  }
  return out;
}

/** Edit (creating if needed) the one rule that targets exactly this part. */
export function ruleFor(setup: Setup, inst: PartDoc, all: PartDoc[]): PartRule {
  const pat = partPattern(inst, all);
  let rule = setup.parts.find((r) => r.match === pat);
  if (!rule) {
    rule = { match: pat };
    setup.parts.push(rule);
  }
  return rule;
}

export function instanceForPattern(pattern: string, parts: PartDoc[]): PartDoc[] {
  return parts.filter((i) => nameMatches(pattern, i));
}

export function newId(prefix: string, existing: string[]): string {
  let n = existing.length + 1;
  while (existing.includes(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

export function emptySetup(): Setup {
  return { parts: [{ match: "*", finish: "black" }], leds: [], detectors: [], states: [] };
}

export function sigOf(info: { centroid: number[]; normal: number[]; area: number }): Sig {
  return { centroid: info.centroid, normal: info.normal, area: info.area };
}

export function ledChannelKeys(led: LedDef, library: { leds: Record<string, { channels: { id: string; name?: string }[] }> }): { key: string; label: string }[] {
  const model = library.leds[led.model];
  return (model?.channels ?? []).map((c) => ({ key: `${led.id}/${c.id}`, label: `${led.name} / ${c.name ?? c.id}` }));
}
