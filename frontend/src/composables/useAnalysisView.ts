// The 3D side of the light path analysis: replayed rays and how they are drawn, the surface inspector and the surface hit map.
// State is shared between the results pane (which asks for rays) and the dock over the 3D view (which filters and draws them).
import { computed, inject, provide, reactive, ref, shallowRef, watch, type InjectionKey, type Ref } from "vue";
import { api, type AnalysisMap, type AnalysisOverview, type FaceReport, type HitMap, type RaysFilter, type RaysResponse } from "../api";
import type { RayColorMode, RayStyle, Slab } from "./useResultsOverlay";
import type { FaceHighlight } from "./useSetupScene";
import type { ExitFrame } from "../utils/analysisText";

/** Colours of the vertex events of a replayed ray (codes of engine/kernels.py EV_*). */
export const EVENTS: { name: string; label: string; color: number }[] = [
  { name: "start", label: "Leaves the LED", color: 0xfacc15 },
  { name: "scatter", label: "Scatters in the material", color: 0x22c55e },
  { name: "specular", label: "Mirror reflection", color: 0x3b82f6 },
  { name: "diffuse", label: "Diffuse reflection (white/matte)", color: 0xf97316 },
  { name: "fresnel", label: "Fresnel reflection", color: 0x8b5cf6 },
  { name: "tir", label: "Total internal reflection", color: 0x06b6d4 },
  { name: "refract", label: "Passes through a surface", color: 0xe5e7eb },
  { name: "absorbed", label: "Absorbed", color: 0xef4444 },
  { name: "exit", label: "Reaches the exit surface", color: 0x10b981 },
  { name: "end", label: "Ends (dropped or escaped)", color: 0x6b7280 },
];

export const LED_PALETTE = [0xf97316, 0x22c55e, 0x3b82f6, 0xe11d48, 0xa855f7, 0x14b8a6, 0xeab308];

export interface RayForm {
  outcome: "" | "exit" | "absorbed" | "escaped" | "lost";
  led: string;
  touched: number; // group index, -1 = any
  detector: number; // exit surface / receiver (index in the setup) the light has to reach, -1 = any
  minBounce: number;
  maxBounce: number; // 0 = no limit
  count: number;
}

/** Surfaces to show in the 3D view (a group, or the steps of a route in their own colours). */
export interface Spot {
  label: string;
  /** CSS colour. */
  color: string;
  faces: { instance: string; face: number | null }[];
}
/** A map (a surface's or a route's footprint on the exit surface) painted on the model in place of the exit map. */
export interface ModelMap {
  label: string;
  values: number[][];
  vmax: number;
  detector: number;
  unit: string;
}

/** The colour scale of one map drawn on the model: 0 up to `vmax` in `unit`, in the colours the map texture uses. */
export interface MapScale {
  label: string;
  vmax: number;
  unit: string;
}

/** Where the light of a surface or route lands on the exit surface (the map the results pane draws for it). */
export interface Footprint {
  kind: "group" | "family";
  id: number | string;
  mode?: "last" | "touched";
  detector: number;
  /** The colour-scale maximum of the exit map (for "same scale as the exit map"). */
  vmax: number;
}
export interface RaySpec {
  filter: RaysFilter;
  label: string;
  gface?: number | null;
  color?: RayColorMode;
  colors?: Record<number, string>;
}
/** One thing picked in the results (a surface, a route, a region of the exit surface) and everything the 3D view can show of it. */
export interface Selection {
  id: string;
  kind: "surface" | "route" | "region";
  label: string;
  spots: Spot[];
  footprint: Footprint | null;
  rays: RaySpec | null;
  /** Start with its rays on (a surface starts without them). */
  showRays: boolean;
}
/** `rays`: where the rays that are shown land (whatever filter they have). */
export type MapLayer = "run" | "selection" | "rays" | "off";
export type SurfaceLayer = "selection" | "heat" | "off";
/** `custom`: rays that were asked for by something else (the inspector, the fan of a cut). */
export type RayLayer = "off" | "selection" | "exit" | "absorbed" | "escaped" | "custom";

const hexNumber = (css: string) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(css ?? "");
  return m ? parseInt(m[1], 16) : 0x6b7280;
};

export function useAnalysisView(
  runId: Ref<string | null>,
  stateId: Ref<string | undefined>,
  view: Ref<string>,
  ledIds: Ref<string[]>,
  frame: Ref<ExitFrame | null>,
  faceCounts: Ref<Map<string, number>>,
) {
  const overview = shallowRef<AnalysisOverview | null>(null);
  const overviewError = ref("");
  const rays = shallowRef<RaysResponse | null>(null);
  const rayLabel = ref("");
  const rayLoading = ref(false);
  const rayError = ref("");
  const baseFilter = ref<RaysFilter>({});
  const baseGface = ref<number | null>(null);
  /** The full filter of the rays that are shown (the base filter and the choices of the ray options). */
  const shownFilter = shallowRef<RaysFilter | null>(null);
  /** The exit map of the run that the results pane shows: its detector and colour-scale maximum. */
  const mapDetector = ref(0);
  const mapVmax = ref(1);
  const style = reactive({ color: "led" as RayColorMode, density: false, maxBounce: 0, opacity: 0.7, densityGain: 0.35, stopAtExit: false, showOthers: false });
  const form = reactive<RayForm>({ outcome: "", led: "", touched: -1, detector: -1, minBounce: 0, maxBounce: 0, count: 400 });
  /** What the 3D view shows, as three independent layers. */
  const layers = reactive({ map: "run" as MapLayer, surfaces: "off" as SurfaceLayer, rays: "off" as RayLayer, sameScale: false, showScale: false });
  let raysKey = "";

  // ── group colours (from the overview of the run) ───────────────────────────
  async function loadOverview() {
    overview.value = null;
    overviewError.value = "";
    if (!runId.value) return;
    try {
      overview.value = await api.analysis(runId.value, { state: stateId.value, view: view.value });
    } catch (e) {
      overviewError.value = (e as Error).message;
    }
  }
  const groups = computed(() => overview.value?.groups ?? []);
  /** Colours that replace the group colours while a route is shown (every step of the route has its own colour). */
  const colorOverride = ref<Record<number, string>>({});
  /** The CSS colour of a group (the route colour while a route is shown). */
  const groupCss = (g: number) => colorOverride.value[g] ?? groups.value.find((x) => x.index === g)?.color ?? "#6b7280";
  const groupColor = (g: number) => hexNumber(groupCss(g));

  // ── replayed rays ─────────────────────────────────────────────────────────────
  function buildRequest(): { filter: RaysFilter; gface: number | null } {
    const filter: RaysFilter = { ...baseFilter.value };
    if (form.outcome) filter.outcome = form.outcome;
    if (form.led) filter.led = form.led;
    if (form.touched >= 0) filter.touched = form.touched;
    if (form.detector >= 0) filter.detector = form.detector;
    if (form.minBounce > 0 || form.maxBounce > 0) filter.bounces = [Math.max(0, form.minBounce), form.maxBounce > 0 ? form.maxBounce : 9999];
    return { filter, gface: baseGface.value };
  }
  async function fetchRays() {
    if (!runId.value) return;
    rayLoading.value = true;
    rayError.value = "";
    try {
      const { filter, gface } = buildRequest();
      rays.value = await api.analysisRays(runId.value, {
        filter,
        count: Math.max(1, Math.min(3000, Math.round(form.count))),
        state: stateId.value,
        view: view.value,
        gface,
        maxPoints: 256,
      });
      shownFilter.value = filter;
      style.maxBounce = 0;
      style.stopAtExit = filter.outcome === "exit" || (!filter.outcome && !!(filter.region || filter.family || filter.lastGroup !== undefined || filter.detector !== undefined));
    } catch (e) {
      rays.value = null;
      rayError.value = (e as Error).message;
    } finally {
      rayLoading.value = false;
    }
  }
  /** Show the rays of a selection (an exit region, a route family, a group...). The form's filters start empty again. */
  async function loadRays(base: RaysFilter, label: string, o: { gface?: number | null; color?: RayColorMode; colors?: Record<number, string> } = {}) {
    baseFilter.value = base;
    baseGface.value = o.gface ?? null;
    rayLabel.value = label;
    Object.assign(form, { outcome: base.outcome ?? "", led: "", touched: -1, detector: base.detector ?? -1, minBounce: 0, maxBounce: 0 });
    if (o.color) style.color = o.color;
    colorOverride.value = o.colors ?? {};
    await fetchRays();
  }
  /** Rays that are not one of the layer's own choices (the rays at an inspected surface, the fan of a cut). */
  async function showRays(base: RaysFilter, label: string, o: { gface?: number | null; color?: RayColorMode; colors?: Record<number, string> } = {}) {
    layers.rays = "custom";
    raysKey = "";
    await loadRays(base, label, o);
  }
  function dropRays() {
    rays.value = null;
    shownFilter.value = null;
    rayLabel.value = "";
    rayError.value = "";
    colorOverride.value = {};
  }
  function clearRays() {
    layers.rays = "off";
    raysKey = "";
    dropRays();
  }
  const longestRay = computed(() => Math.max(1, ...(rays.value?.paths ?? []).map((p) => p.points.length - 1)));

  /** Set by the section tool: draw only the rays' part inside a layer beside the cut (a ray fan). */
  const slab = ref<Slab | null>(null);
  /** The detectors at which a ray is cut when "stop at the exit" is on: the chosen one, else every detector, receivers included (only the pass-through exits, which let the light go on by design, are left out). The light of a receiver does go on in the simulation; the cut is only for the picture. */
  const stopDets = computed<number[] | null>(() => {
    const f = shownFilter.value;
    const chosen = f?.detector ?? f?.region?.detector;
    if (chosen !== undefined) return [chosen];
    const all = rays.value?.detectors ?? [];
    if (!all.length) return null;
    return all.map((d, i) => (d.through ? -1 : i)).filter((i) => i >= 0);
  });
  const rayStyle = computed<RayStyle>(() => {
    const keys = ledIds.value;
    return {
      slab: slab.value,
      color: style.color,
      density: style.density,
      maxBounce: style.maxBounce,
      stopAtExit: style.stopAtExit,
      stopDets: stopDets.value,
      opacity: style.opacity,
      densityGain: style.densityGain,
      focusFace: inspected.value?.face.gface ?? null,
      showOthers: style.showOthers,
      ledColor: (_key, led) => LED_PALETTE[Math.max(0, keys.indexOf(led)) % LED_PALETTE.length],
      // (the palette matches the LED markers in the 3D view)
      groupColor,
      eventColor: (e) => EVENTS[e]?.color ?? 0x6b7280,
    };
  });

  // ── the surface inspector and the surface hit map ───────────────────────────────
  const inspecting = ref(false);
  const inspected = shallowRef<FaceReport | null>(null);
  const inspectError = ref("");
  async function inspectFace(instance: string, face: number) {
    if (!runId.value) return;
    inspectError.value = "";
    try {
      inspected.value = await api.analysisFace(runId.value, { instance, face, state: stateId.value });
      if (style.color !== "face" && rays.value) style.color = "face";
    } catch (e) {
      inspected.value = null;
      inspectError.value = (e as Error).message;
    }
  }
  function clearInspection() {
    inspected.value = null;
    inspectError.value = "";
    if (style.color === "face") style.color = "led";
  }

  const hitQuantity = ref<"" | "incident" | "absorbed" | "reflected" | "transmitted">("");
  const hitMap = shallowRef<HitMap | null>(null);
  const hitError = ref("");
  async function loadHits() {
    hitMap.value = null;
    hitError.value = "";
    if (!runId.value || !hitQuantity.value) return;
    try {
      hitMap.value = await api.analysisHits(runId.value, { quantity: hitQuantity.value, state: stateId.value });
    } catch (e) {
      hitError.value = (e as Error).message;
    }
  }

  /** A warm colour ramp for a value 0..1 (dark red -> yellow -> white). */
  const heat = (t: number) => {
    const x = Math.min(1, Math.max(0, t));
    const r = Math.min(1, x * 2.2);
    const g = Math.min(1, Math.max(0, (x - 0.35) * 2));
    const b = Math.min(1, Math.max(0, (x - 0.8) * 5));
    return (Math.round(40 + r * 215) << 16) | (Math.round(g * 255) << 8) | Math.round(b * 255);
  };
  const heatCss = (t: number) => `#${heat(t).toString(16).padStart(6, "0")}`;

  // ── what the 3D view shows: a selection from the results and three independent layers ──────────
  const spotlight = shallowRef<Spot[]>([]);
  const modelMap = shallowRef<ModelMap | null>(null);
  /** Counts up whenever the results pane wants the 3D view to be shown (the view listens). */
  const viewRequest = ref(0);
  /** The 3D view is what is on screen (the layers only do work then). */
  const viewActive = ref(false);
  const selection = shallowRef<Selection | null>(null);

  /** The results pane says "view this in 3D": remember it and set the layers to what suits that kind of selection. */
  function viewSelection(sel: Selection) {
    selection.value = sel;
    layers.map = sel.footprint ? "selection" : "run";
    layers.surfaces = sel.spots.length ? "selection" : "off";
    layers.rays = sel.rays && sel.showRays ? "selection" : "off";
    raysKey = "";
    viewRequest.value++;
  }

  // the map layer: where the light of the selection, or of the rays that are shown, lands on the exit surface
  interface MapSource {
    key: string;
    label: string;
    detector: number;
    /** The colour-scale maximum of the exit map of the run (for "same scale"). */
    runMax: number;
    fetch: () => Promise<AnalysisMap>;
  }
  let mapSeq = 0;
  let fpKey = "";
  let fpMap: AnalysisMap | null = null;
  function mapSource(): MapSource | null {
    const rid = runId.value;
    if (!rid) return null;
    const c = (detector: number) => ({ state: stateId.value, view: view.value, detector });
    if (layers.map === "selection") {
      const fp = selection.value?.footprint;
      if (!fp) return null;
      return {
        key: `sel|${rid}|${stateId.value}|${view.value}|${fp.kind}|${fp.id}|${fp.mode ?? ""}|${fp.detector}`,
        label: `${selection.value!.label} — where its light lands`,
        detector: fp.detector,
        runMax: fp.vmax,
        fetch: () => (fp.kind === "group" ? api.analysisMap(rid, { group: fp.id as number, mode: fp.mode ?? "last", ...c(fp.detector) }) : api.analysisFamilyMap(rid, { family: fp.id as string, ...c(fp.detector) })),
      };
    }
    if (layers.map === "rays") {
      const base = shownFilter.value;
      if (!base || !rays.value) return null;
      // an inspected face narrows the map to the rays that went through its surface group (what the rays show in face colours)
      const face = inspected.value;
      const g = face?.group?.index;
      const filter: RaysFilter = g === undefined ? base : { ...base, touched: [...(base.touched === undefined ? [] : [base.touched].flat()), g] };
      const det = mapDetector.value;
      return {
        key: `rays|${rid}|${stateId.value}|${view.value}|${det}|${JSON.stringify(filter)}`,
        label: `${rayLabel.value || "The rays that are shown"}${face?.group ? ` through ${face.group.name}` : ""} — where they land`,
        detector: det,
        runMax: mapVmax.value,
        fetch: () => api.analysisRaysMap(rid, { filter, ...c(det) }),
      };
    }
    return null;
  }
  function applyMap(src: MapSource | null) {
    if (!src || !fpMap || fpKey !== src.key) {
      modelMap.value = null;
      return;
    }
    const own = Math.max(1e-9, ...fpMap.values.map((r) => r.reduce((a, b) => (b > a ? b : a), 0))) * 1.02;
    modelMap.value = { label: src.label, values: fpMap.values, vmax: layers.sameScale ? src.runMax : own, detector: src.detector, unit: fpMap.unit };
  }
  async function syncMap() {
    const src = mapSource();
    if (!src || (src.key === fpKey && fpMap)) {
      applyMap(src);
      return;
    }
    const mine = ++mapSeq;
    try {
      const m = await src.fetch();
      if (mine !== mapSeq) return;
      fpKey = src.key;
      fpMap = m;
    } catch {
      if (mine !== mapSeq) return;
      fpKey = "";
      fpMap = null;
    }
    applyMap(src);
  }
  // a new set of rays changes the map that follows them
  watch([rays, inspected], () => {
    if (viewActive.value && layers.map === "rays") void syncMap();
  });
  const RAY_PRESETS: Record<string, { filter: RaysFilter; label: string }> = {
    exit: { filter: { outcome: "exit" }, label: "Light that reaches the exit" },
    absorbed: { filter: { outcome: "absorbed" }, label: "Light that is absorbed" },
    escaped: { filter: { outcome: "escaped" }, label: "Light that escapes elsewhere" },
  };
  function syncRays() {
    const m = layers.rays;
    if (m === "custom") return;
    if (m === "off") {
      if (rays.value || rayLabel.value) dropRays();
      raysKey = "";
      return;
    }
    const sel = selection.value;
    let key: string;
    let spec: { filter: RaysFilter; label: string; gface?: number | null; color?: RayColorMode; colors?: Record<number, string> } | null;
    if (m === "selection") {
      spec = sel?.rays ?? null;
      key = `sel:${sel?.id}`;
    } else {
      spec = { ...RAY_PRESETS[m], color: "led" };
      key = m;
    }
    key = `${runId.value}|${stateId.value}|${view.value}|${key}`;
    if (!spec) {
      dropRays();
      raysKey = "";
      return;
    }
    if (key === raysKey && rays.value) return;
    raysKey = key;
    void loadRays(spec.filter, spec.label, spec);
  }

  function sync() {
    if (!viewActive.value || !runId.value) return;
    if (layers.map === "rays" && layers.rays === "off") layers.map = "run"; // nothing to follow
    spotlight.value = layers.surfaces === "selection" && selection.value ? selection.value.spots : [];
    if (layers.surfaces === "heat") {
      if (!hitQuantity.value) hitQuantity.value = "incident";
    } else if (hitQuantity.value) hitQuantity.value = "";
    void syncMap();
    syncRays();
  }
  watch([viewActive, selection, () => layers.map, () => layers.surfaces, () => layers.rays, () => layers.sameScale, stateId, view], sync);

  /** Back to a plain 3D view: exit map only, nothing picked. */
  function resetLayers() {
    selection.value = null;
    layers.map = "run";
    layers.surfaces = "off";
    layers.rays = "off";
    layers.sameScale = false;
    raysKey = "";
    dropRays();
    clearInspection();
  }

  const highlights = computed<FaceHighlight[]>(() => {
    const out: FaceHighlight[] = [];
    spotlight.value.forEach((s, si) => {
      for (const f of s.faces) {
        const nums = f.face !== null ? [f.face] : Array.from({ length: Math.min(200, faceCounts.value.get(f.instance) ?? 0) }, (_, i) => i);
        for (const n of nums) out.push({ key: `spot:${si}:${f.instance}:${n}`, instanceId: f.instance, face: n, color: hexNumber(s.color), opacity: 0.85 });
      }
    });
    const h = hitMap.value;
    if (h && hitQuantity.value && h.maxDensity > 0) {
      for (const f of h.faces) {
        // square-root scaling keeps the weak surfaces visible next to the strongest one
        out.push({ key: `hit:${f.instance}:${f.face}`, instanceId: f.instance, face: f.face, color: heat(Math.sqrt(f.density / h.maxDensity)), opacity: 0.9 });
      }
    }
    if (inspected.value) {
      const f = inspected.value.face;
      out.push({ key: "inspected", instanceId: f.instance, face: f.face, color: 0x06b6d4, opacity: 0.95 });
    }
    return out;
  });

  function reset() {
    overview.value = null;
    resetLayers();
    hitQuantity.value = "";
    hitMap.value = null;
    inspecting.value = false;
    spotlight.value = [];
    modelMap.value = null;
    fpKey = "";
    fpMap = null;
  }

  watch(runId, () => {
    reset();
    void loadOverview();
  });
  watch([stateId, view], () => {
    if (!runId.value) return;
    void loadOverview();
    if (hitQuantity.value) void loadHits();
  });
  watch(hitQuantity, () => void loadHits());

  return {
    overview,
    overviewError,
    groups,
    groupColor,
    groupCss,
    frame,
    spotlight,
    modelMap,
    viewRequest,
    viewActive,
    selection,
    layers,
    shownFilter,
    mapDetector,
    mapVmax,
    viewSelection,
    resetLayers,
    colorOverride,
    loadOverview,
    rays,
    rayLabel,
    rayLoading,
    rayError,
    style,
    form,
    slab,
    rayStyle,
    longestRay,
    showRays,
    fetchRays,
    clearRays,
    inspecting,
    inspected,
    inspectError,
    inspectFace,
    clearInspection,
    hitQuantity,
    hitMap,
    hitError,
    heatCss,
    highlights,
  };
}

export type AnalysisView = ReturnType<typeof useAnalysisView>;
const KEY: InjectionKey<AnalysisView> = Symbol("refract-analysis-view");
export const provideAnalysisView = (a: AnalysisView) => provide(KEY, a);
export function injectAnalysisView(): AnalysisView {
  const a = inject(KEY);
  if (!a) throw new Error("analysis view not provided");
  return a;
}

/** What the analysis tabs of the results pane need to know about the map that is shown. */
export interface AnalysisCtx {
  runId: string;
  state?: string;
  view: "cone" | "all";
  detector: number;
  uMm: number;
  vMm: number;
  valid: number[][] | null;
  /** The exit map that is shown (luminance or exitance) and its colour-scale maximum. */
  values: number[][];
  /** The statistics region chosen on the exit map ([u0, v0, u1, v1] fractions), if any. */
  roi: number[] | null;
  vmax: number;
  unit: string;
}

/** The message of a 404 of the analysis endpoints: the run kept no analysis data. */
export const NO_DATA = "This run was made without analysis data (or before the analysis tools existed). Run it again with the analysis on.";
