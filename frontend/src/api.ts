// Thin client for the local Python server.

export type JobStatus = "queued" | "running" | "ready" | "failed" | "cancelled";

export interface Progress {
  stage: string;
  phase: string;
  fraction: number;
}

export interface RevisionMeta {
  id: string;
  filename: string;
  label?: string;
  size: number;
  created: number;
  status: JobStatus;
  error?: string | null;
  model_ready?: boolean;
  projectId: string;
  variantId?: string;
  instances?: number;
  triangles?: number;
  started?: number;
  finished?: number;
  summary?: { parts: number; warnings: string[] } | null;
  progress: Progress;
}

/** A design alternative of a project (other LED pitch, reflector, light pipe...): its own setup and revisions. */
export interface Variant {
  id: string;
  name: string;
  created: number;
  revisions: string[];
  latest: RevisionMeta | null;
}

export interface Project {
  id: string;
  name: string;
  stem: string;
  created: number;
  lastOpened: number;
  revisions: string[];
  variants: Variant[];
  latest: RevisionMeta | null;
  revisionList?: RevisionMeta[];
}

// ── optical parts ───────────────────────────────────────────────────────────
export interface PartDoc {
  ordinal: number;
  id: string;
  path: string;
  name: string;
  partName?: string;
  faceCount: number;
  closed: boolean;
  volume: number;
  area: number;
  bbox: number[][];
  notes?: string[];
}
export interface PartsDoc {
  instances: PartDoc[];
  warnings: string[];
  diagonal: number;
  triangles: number;
}
export interface FaceInfo {
  centroid: number[];
  normal: number[];
  area: number;
  planar: boolean;
  bbox: number[][];
}
export interface Sig {
  centroid: number[];
  normal: number[];
  area: number;
}

// ── setup ───────────────────────────────────────────────────────────────────
export interface PartRule {
  match: string;
  enabled?: boolean;
  material?: string | null;
  finish?: string | null;
  faces?: Record<string, string>;
  face_sigs?: Record<string, Sig>;
}
export interface FaceRef {
  part: string;
  face: number;
  sig?: Sig | null;
}
export interface LedPlacement {
  type: "face" | "frame";
  part?: string;
  face?: number;
  sig?: Sig | null;
  flip?: boolean;
  start_body?: string | null;
  origin?: number[];
  axis?: number[];
  up?: number[];
  size?: number[];
}
export interface LedDef {
  id: string;
  name: string;
  model: string;
  enabled?: boolean;
  scale?: number;
  placement: LedPlacement;
}
export interface DetectorDef {
  id: string;
  name: string;
  faces: FaceRef[];
  cone_deg: number;
  resolution: number;
  /** How the surface is flattened into the map: automatic (curved faces are unwrapped), flat projection, or unwrap. */
  mapping?: "auto" | "plane" | "unwrap";
  /** Mirror the map left-right (the red arrow) / top-bottom (the green arrow). */
  flip_u?: boolean;
  flip_v?: boolean;
}
export interface StateDef {
  id: string;
  name: string;
  drive: Record<string, number>;
}
/** A set of CAD faces that the light analysis treats as one surface (for example one facet of the reflector). */
export interface AnalysisGroupDef {
  id: string;
  name: string;
  /** #rrggbb; a colour from the palette is used when empty. */
  color?: string;
  /** `face` null stands for every face of the part. */
  faces: { part: string; face: number | null; sig?: Sig | null }[];
}
export interface Setup {
  parts: PartRule[];
  leds: LedDef[];
  detectors: DetectorDef[];
  states: StateDef[];
  /** When empty, the faces with an opaque finish become groups automatically (the largest 62). */
  analysisGroups?: AnalysisGroupDef[];
}
export interface SuggestedSetup extends Setup {
  notes: string[];
}

// ── library ─────────────────────────────────────────────────────────────────
export type Curve = number | { type: string; [k: string]: unknown };
export interface MaterialDoc {
  id: string;
  name: string;
  builtin?: boolean;
  n: Curve;
  absorption: Curve;
  scattering?: { type: string; [k: string]: unknown };
  notes?: string;
}
export interface FinishDoc {
  id: string;
  name: string;
  builtin?: boolean;
  type: "dielectric" | "opaque";
  sigma?: number;
  rdiff?: number;
  rspec?: number;
  /** Surface colour of an opaque finish as #rrggbb; empty means a neutral grey surface. */
  color?: string;
  /** Colour the mirror part too (metals). */
  tint_specular?: boolean;
}
export interface LedChannel {
  id: string;
  name?: string;
  flux_lm: number;
  spectrum: { type: string; [k: string]: unknown };
  pattern: { type: string; [k: string]: unknown };
  /** A measured ray set instead of the angular pattern (generic text ray list). */
  source?: RaySource | null;
}
export interface RaySource {
  type: "rays";
  file: string;
  filename?: string;
  count: number;
  hasWavelength: boolean;
  extentMm: number[];
  backwardFraction: number;
  angles?: number[];
  intensity?: number[];
}
export interface LedModel {
  id: string;
  name: string;
  builtin?: boolean;
  channels: LedChannel[];
  emitter?: { shape: string; size: number[] };
  notes?: string;
}
export interface Library {
  materials: Record<string, MaterialDoc>;
  finishes: Record<string, FinishDoc>;
  leds: Record<string, LedModel>;
}
export type LibraryKind = keyof Library;

// ── checks and runs ─────────────────────────────────────────────────────────
export interface CheckResult {
  ok: boolean;
  warnings: string[];
  bodies: { path: string; material: string | null; finish: string; transparent: boolean }[];
  detectors: { id: string; name: string; nu: number; nv: number; areaMm2: number; corners: number[][]; n: number[] }[];
  channels: { key: string; led: string; channel: string; fluxLm: number; origin: number[]; axis: number[]; startBody: number | null; ex?: number[]; ey?: number[]; size?: number[] }[];
  triangles: number;
  resolved: { detectors: Record<string, { instance: string; face: number }[]>; leds: Record<string, { instance: string; face: number }> };
  /** The analysis groups as the simulation sees them (the automatic ones when none are defined). */
  groups?: { index: number; id: string; name: string; color: string; auto: boolean; areaMm2: number; faces: { instance: string; face: number | null }[] }[];
  /** Face selections of the analysis groups that do not exist in this model (the part or the face is gone). */
  groupIssues?: GroupIssue[];
}
export interface GroupIssue {
  id: string;
  name: string;
  missing: { part: string; face: number | null; reason: "face" | "part" }[];
}

export interface RunSettings {
  rays: number;
  seed: number;
  rr_threshold: number;
  max_bounces: number;
  theta_bins: number;
  preview_paths: number;
  max_path_points: number;
  live?: boolean;
  /** Keep the data of the light path analysis (default on) and for how many rays per channel (default 2,000,000). */
  analysis?: boolean;
  analysis_rays?: number;
  /** Stop before `rays` once the median relative noise per map pixel is below this (e.g. 0.05); with `analysis: true` one final pass over the first `analysis_rays` provides the light path analysis. */
  target_noise?: number;
  /** Quick look: trace only the brightest driven channel of every LED (shows the spread of the light, not colour or absolute luminance). */
  one_channel_per_led?: boolean;
  /** "cpu" (default) or "gpu" (NVIDIA Warp on CUDA; see `api.solvers`). */
  solver?: "cpu" | "gpu";
}
export interface RunSummaryState {
  id: string;
  name: string;
  emittedLm: number;
  detectors: {
    name: string;
    efficiency: number;
    exitLm: number;
    mean: number | null;
    minOverMax: number | null;
    cv: number | null;
    maxDuv: number | null;
    hotspots: number;
  }[];
}
export interface RenderSettings {
  state?: string;
  mode: "map" | "transport";
  width: number;
  spp: number;
  azimuth: number;
  elevation: number;
  zoom: number;
  exposure: number;
  ambient?: number;
  white_balance: boolean;
}
export interface RenderState {
  available: boolean;
  reason: string;
  render: null | {
    status: "queued" | "running" | "ready" | "failed" | "cancelled";
    error?: string | null;
    hasImage: boolean;
    finished?: number;
    seconds?: number;
    settings: RenderSettings;
    progress: { phase: string; fraction: number };
    info?: { warnings: string[]; state: string; size: number[] };
  };
}
export interface SolverInfo {
  id: "cpu" | "gpu";
  available: boolean;
  /** The device (GPU) or the engine (CPU). */
  name: string | null;
  /** Why it is not available. */
  reason: string | null;
}
export interface RunMeta {
  id: string;
  revisionId: string;
  projectId: string;
  name: string;
  created: number;
  status: JobStatus;
  error?: string | null;
  settings: RunSettings;
  progress: Progress;
  timing?: { seconds: number; raysTotal: number; raysPerSecond: number };
  /** Rays per channel actually traced (less than `settings.rays` when the run stopped at its noise target). */
  /** The solver that traced the run, and the GPU's name when it was the GPU. */
  solver?: "cpu" | "gpu";
  solverName?: string;
  raysTraced?: number;
  /** Median relative noise per map pixel when the run stopped at a noise target. */
  mapNoise?: number;
  warnings?: string[];
  maxAccountingError?: number;
  /** True when the run kept the data of the light path analysis. */
  hasAnalysis?: boolean;
  summary?: { states: RunSummaryState[] };
  sweep?: SweepInfo;
  /** Live previews only: how far the progressive simulation has got. */
  live?: { rays: number; target: number; t: number } | null;
}

/** What a live-preview request changes in the setup without touching it. */
export interface LiveOverride {
  parameter: string;
  part: string;
  value: number;
}
export interface LiveCamera {
  position: number[];
  target: number[];
  up: number[];
  fov: number;
  orthoHalfH?: number | null;
}
export interface LiveFrameRequest {
  camera: LiveCamera;
  width: number;
  height: number;
  spp: number;
  quality?: number;
  exposure?: number;
  ambient?: number;
  state?: string | null;
  white_balance?: boolean;
  mode?: "map" | "transport";
}
export type LiveFrame =
  | { kind: "image"; blob: Blob; spp: number; rays: number; ms: number; variant: string }
  | { kind: "status"; status: "starting" | "waiting" | "error"; reason?: string; error?: string };
export interface SweepInfo {
  id: string;
  name: string;
  parameter: string;
  label: string;
  unit: string;
  value: number;
  index: number;
  part: string;
}

// ── results ─────────────────────────────────────────────────────────────────
export interface Analysis {
  roi?: [number, number, number, number];
  margin?: number;
  smooth?: number;
  /** Gaussian smoothing (pixels) of the map that is drawn; the statistics use `smooth`. */
  map_smooth?: number;
  hotspot_ratio?: number;
  hotspot_mm?: number;
  view?: "cone" | "all";
  /** Half angle (deg) of the viewing cone; absent = the angle the run was traced with. Limited per exit surface to `DetectorResult.coneRange`. */
  cone_deg?: number;
}
/** How noisy the Monte Carlo map is: relative standard error per pixel over the region of interest. */
export interface MapNoise {
  median: number;
  p90: number;
  target: number;
  raysPerChannel: number;
  converged: boolean;
  /** Only when the map is noisier than the target: what it would take to get there. */
  raysForTarget?: number;
  resolutionForTarget?: number;
}
export interface Hotspot {
  u: number;
  v: number;
  ratio: number;
  world: number[];
}
export interface LedResult {
  id: string;
  name: string;
  emittedLm: number;
  exitLm: number;
  efficiency: number;
  shareOfExit: number;
  map?: number[][];
}
export interface DetectorResult {
  id: string;
  name: string;
  nu: number;
  nv: number;
  areaMm2: number;
  uExtentMm: number;
  vExtentMm: number;
  /** The viewing cone of the luminance maps: the traced one unless another angle was asked for (`Analysis.cone_deg`). */
  coneDeg: number;
  /** The viewing cone the run was traced with (the one set on the exit surface). */
  tracedConeDeg?: number;
  /** The smallest and largest cone angle that can be shown without tracing again; null/absent for runs made before the angle rings existed. */
  coneRange?: [number, number] | null;
  exitLm: number;
  efficiency: number;
  efficiencyRadiometric: number;
  quantity: "luminance" | "exitance";
  unit: string;
  luminance: number[][];
  exitance: number[][];
  /** The active quantity, smoothed by `mapSmoothPx` for drawing (absent in results from older servers). */
  map?: number[][];
  mapSmoothPx?: number;
  /** Absent for runs made before the noise estimate existed. */
  noise?: MapNoise | null;
  mask: number[][];
  valid: number[][];
  stats: {
    mean?: number;
    max?: number;
    min?: number;
    minOverMax?: number;
    minOverAvg?: number;
    maxOverAvg?: number;
    cv?: number;
    p5OverP95?: number;
    pixels?: number;
  };
  hotspots: Hotspot[];
  color: { x: number; y: number; u: number; v: number; cct: number; maxDuv: number; p95Duv: number; meanDuv: number } | null;
  duvMap?: number[][];
  chromaticityXY?: number[][][];
  appearance?: number[][][];
  profileU: (number | null)[];
  profileV: (number | null)[];
  angular: { angles: number[]; intensityCd: number[]; relative: number[]; duvFromNormal: number[] | null };
  leds: LedResult[];
}
export interface LossItem {
  name: string;
  path?: string;
  kind: "exit" | "absorbed" | "escaped" | "error" | "noise";
  fraction: number;
  volume?: number;
  surface?: number;
}
export interface StateResult {
  state: { id: string; name: string };
  analysis: Record<string, unknown>;
  emitted: { lm: number; w: number };
  rays: number;
  detectors: DetectorResult[];
  loss: LossItem[];
  lossSum: number;
  states: { id: string; name: string }[];
  channels: { key: string; led_id: string; led_name: string; channel_id: string; channel_name: string; flux_lm: number; color: number[] }[];
  warnings: string[];
}
export interface RunScene {
  bodies: { index: number; path: string; name: string; material: string | null; finish: string; transparent: boolean }[];
  detectors: { id: string; name: string; origin: number[]; u: number[]; v: number[]; n: number[]; ext: number[]; nu: number; nv: number; corners: number[][]; cone_deg: number; area_total: number; mode?: number; mesh?: { positions: number[]; normals: number[]; uv: number[] } }[];
  channels: StateResult["channels"];
  states: { id: string; name: string; drive: Record<string, number> }[];
  warnings: string[];
  settings: RunSettings;
}
export interface RayPath {
  led: string;
  channel: string;
  points: number[][];
}

// ── light path analysis ─────────────────────────────────────────────────────
export interface GroupGeometry {
  bbox: number[][];
  centroid: number[];
  /** Mean surface normal (unit); null for whole parts or surfaces that face every way. */
  normal: number[] | null;
  flat: boolean;
}
export interface AnalysisGroupInfo {
  index: number;
  id: string;
  name: string;
  color: string;
  auto: boolean;
  areaMm2: number;
  faces: { instance: string; path: string; face: number | null }[];
  /** Where the surfaces of the group are (model units, mm); null when the model is gone. */
  geometry?: GroupGeometry | null;
  /** Share of the exit light whose last tagged face is this group / that touched it at any point. */
  lastFraction: number;
  touchedFraction: number;
  /** Fractions of the emitted flux that arrive at, are absorbed by, reflected by and transmitted through the faces of the group. */
  incident: number;
  absorbed: number;
  reflected: number;
  transmitted: number;
  absorbedOfIncident: number;
}
export interface AnalysisOverview {
  groups: AnalysisGroupInfo[];
  directFraction: number;
  exitLm: number;
  view: string;
  detector: number;
  map: { nu: number; nv: number };
  loggedRays: number;
  nRays: number;
}
export interface AnalysisMap {
  nu: number;
  nv: number;
  mode: string;
  group: number | null;
  values: number[][];
  fraction: number;
  unit: string;
}
export interface RegionReport {
  fluxFraction: number;
  rows: number;
  byGroup: { group: number; name: string; color: string; fraction: number }[];
  byLed: { led: string; name: string; fraction: number }[];
  byBounces: { label: string; fraction: number }[];
  meanBounces?: number;
  byEvent: { name: string; perRay: number }[];
  families: AnalysisFamily[];
}
export interface AnalysisFamily {
  id: string;
  led: string;
  ledName: string;
  route: number[];
  routeLength: number;
  label: string;
  fraction: number;
  deposits: number;
  meanBounces: number;
  colors: string[];
}
/** Routes with the share of the exit light each delivers to every band of the exit surface (cut along `along`). */
export interface FlowDiagram {
  families: AnalysisFamily[];
  bands: number;
  along: "u" | "v";
  matrix: number[][];
  covered: number;
}
export interface FlowItem {
  slot: number;
  group: number;
  name: string;
  color: string;
  fraction: number;
  ofEmitted: number;
}
export interface FaceReport {
  face: { gface: number; instance: string; path: string; partName: string; face: number; areaMm2: number };
  group: { index: number; name: string; color: string } | null;
  tagged: boolean;
  incident: number;
  absorbed: number;
  reflected: number;
  transmitted: number;
  comesFrom?: FlowItem[];
  goesTo?: FlowItem[];
}
export interface HitMap {
  quantity: string;
  faces: { instance: string; face: number; value: number; areaMm2: number; density: number }[];
  maxDensity: number;
  total: number;
}
export interface Guidance {
  nu: number;
  nv: number;
  unit: string;
  target: number;
  deficit: number[][];
  actual: number[][];
  valid: number[][];
  current: { cv: number; minOverAvg: number; maxOverAvg: number; mean: number };
  predicted: { cv: number; minOverAvg: number; maxOverAvg: number; mean: number };
  suggestions: { group: number; name: string; color: string; share: number; scale: number; changePct: number; advice: string }[];
  note: string;
}
export interface RaysFilter {
  led?: string;
  region?: { detector: number; polygon: number[][] };
  lastGroup?: number;
  touched?: number | number[]; // several: the rays have to touch all of these groups
  family?: string;
  outcome?: "exit" | "absorbed" | "escaped" | "lost";
  bounces?: [number, number];
}
export interface RaysRequest {
  filter: RaysFilter;
  count?: number;
  state?: string;
  view?: string;
  gface?: number | null;
  maxPoints?: number;
  seed?: number;
}
/** A ray traced again in full: every vertex with the CAD face it hit, the event there and the weight afterwards. */
export interface AnalysisRayPath {
  id: number;
  led: string;
  channel: string;
  key: string;
  color: number[] | null;
  flux: number;
  points: number[][];
  gface: number[];
  group: number[];
  event: number[];
  w: number[];
}
export interface RaysResponse {
  paths: AnalysisRayPath[];
  matched: number;
  fluxFraction: number;
  events: string[];
}
export interface SliceRequest {
  origin: number[];
  normal: number[];
  direction?: "both" | "along" | "against";
  outcome?: "" | "exit" | "absorbed" | "other";
  count?: number;
  state?: string;
  seed?: number;
  grid?: number;
}
/** Flux through a cut: values are fractions of the emitted flux per mm², rows run along v (v-min first). */
export interface SliceResponse {
  nu: number;
  nv: number;
  values: number[][];
  max: number;
  total: number;
  crossings: number;
  rays: number;
  corners: number[][];
  u: number[];
  v: number[];
  normal: number[];
  uMm: number;
  vMm: number;
}
export interface CompareBrief {
  state: { id: string; name: string };
  emittedLm: number;
  efficiency: number;
  exitLm: number;
  stats: DetectorResult["stats"];
  color: DetectorResult["color"];
  hotspots: number;
  profileU: (number | null)[];
  angular: DetectorResult["angular"];
  loss: LossItem[];
}
export interface CompareResult {
  a: CompareBrief;
  b: CompareBrief;
  unit: string;
  maps: { nu: number; nv: number; a: number[][]; b: number[][]; diff: number[][]; ratio: number[][]; validA: number[][]; validB: number[][]; vmax: number; dmax: number };
}

/** One run in a project-wide comparison (any number of runs, possibly of different variants). */
export interface ProjectCompareRun extends Partial<CompareBrief> {
  runId: string;
  name?: string;
  revisionId: string;
  revision?: string;
  variantId?: string;
  variant?: string;
  created?: number;
  detectorName?: string;
  quantityUnit?: string;
  requestedState?: string | null;
  stateFallback?: boolean;
  uExtentMm?: number | null;
  vExtentMm?: number | null;
  error?: string;
}
export interface ProjectCompare {
  unit: string | null;
  runs: ProjectCompareRun[];
}

export interface ReconcileResult {
  setup: Setup;
  warnings: string[];
  fromSuggestion: boolean;
}
export interface AcceptFaceMatchesResult {
  setup: Setup;
  changes: string[];
}

export interface MieResult {
  lambda: number[];
  mus: number[];
  g: number[];
  musReduced: number[];
  volumeFraction: number;
  ballisticTransmission: number[];
  thicknessMm: number;
}
export interface FitSample {
  thickness_mm: number;
  total_transmission: number;
  haze?: number | null;
}
export interface FitResult {
  mus: number;
  g: number;
  cost: number;
  predictions: { thickness_mm: number; total_transmission: number; haze: number | null; measured_total: number; measured_haze: number | null }[];
  material: MaterialDoc["scattering"];
  notes?: string[];
}

export interface AppConfig {
  dataDir: string;
  configuredDir: string | null;
  defaultDir: string;
  fromEnvironment: boolean;
  version: string;
  mode: "window" | "browser" | "headless";
  copied?: number;
  update: UpdateSettings;
  /** True in the installed app: only there can an update be installed. */
  canInstallUpdates: boolean;
}

export interface UpdateSettings {
  source: "github" | "custom";
  /** Custom source: a folder (also a network share) or a web address with the installer. */
  location: string;
  auto: boolean;
  prerelease: boolean;
}

export interface UpdateOffer {
  version: string;
  file: string;
  notes: string;
  page: string | null;
  size: number | null;
  hasChecksum: boolean;
}

export interface UpdateCheck {
  current: string;
  latest: string | null;
  available: boolean;
  offer: UpdateOffer | null;
  source: string;
  checkedAt: number;
}

export interface UpdateStatus {
  state: "idle" | "downloading" | "ready" | "failed" | "launching";
  done: number;
  total: number;
  message: string;
  version: string | null;
  canInstall: boolean;
}

/** A failed request. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      const d = body.detail;
      if (typeof d === "string") message = d;
      else if (Array.isArray(d)) message = d.map((x: { msg?: string }) => x.msg).filter(Boolean).join("; ") || message;
    } catch {
      /* ignore */
    }
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
});
const post = <T>(url: string, body?: unknown) => request<T>(url, jsonInit("POST", body));

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return q ? `?${q}` : "";
}

export function analysisParams(a?: Analysis): Record<string, string | number | undefined> {
  if (!a) return {};
  return {
    roi: a.roi ? a.roi.join(",") : undefined,
    margin: a.margin,
    smooth: a.smooth,
    map_smooth: a.map_smooth,
    hotspot_ratio: a.hotspot_ratio,
    hotspot_mm: a.hotspot_mm,
    view: a.view,
    cone_deg: a.cone_deg,
  };
}

export const api = {
  // projects
  listProjects: () => request<Project[]>("/api/projects"),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  renameProject: (id: string, name: string) => request<Project>(`/api/projects/${id}`, jsonInit("PATCH", { name })),
  deleteProject: (id: string) => request<{ ok: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),
  getSetup: (pid: string) => request<Setup>(`/api/projects/${pid}/setup`),
  putSetup: (pid: string, setup: Setup) => request<Setup>(`/api/projects/${pid}/setup`, jsonInit("PUT", setup)),

  // variants (design alternatives with their own setup)
  getVariantSetup: (pid: string, vid: string) => request<Setup>(`/api/projects/${pid}/variants/${vid}/setup`),
  putVariantSetup: (pid: string, vid: string, setup: Setup) => request<Setup>(`/api/projects/${pid}/variants/${vid}/setup`, jsonInit("PUT", setup)),
  createVariant: (pid: string, body: { name?: string; copyFrom?: string }) => post<Project>(`/api/projects/${pid}/variants`, body),
  renameVariant: (pid: string, vid: string, name: string) => request<Project>(`/api/projects/${pid}/variants/${vid}`, jsonInit("PATCH", { name })),
  deleteVariant: (pid: string, vid: string) => request<Project>(`/api/projects/${pid}/variants/${vid}`, { method: "DELETE" }),
  compareRuns: (pid: string, runs: string[], opts: { state?: string; detector?: number; analysis?: Analysis }) =>
    request<ProjectCompare>(`/api/projects/${pid}/compare${query({ runs: runs.join(","), state: opts.state, detector: opts.detector, ...analysisParams(opts.analysis) })}`),

  // revisions
  upload: (file: File, target?: { projectId?: string; projectName?: string; variantId?: string; variantName?: string; copySetupFrom?: string }) => {
    const form = new FormData();
    form.append("file", file);
    if (target?.projectId) form.append("project_id", target.projectId);
    if (target?.projectName) form.append("project_name", target.projectName);
    if (target?.variantId) form.append("variant_id", target.variantId);
    if (target?.variantName) form.append("variant_name", target.variantName);
    if (target?.copySetupFrom) form.append("copy_setup_from", target.copySetupFrom);
    return request<{ id: string; projectId: string }>("/api/revisions", { method: "POST", body: form });
  },
  getRevision: (id: string) => request<RevisionMeta>(`/api/revisions/${id}`),
  labelRevision: (id: string, label: string) => request<RevisionMeta>(`/api/revisions/${id}`, jsonInit("PATCH", { label })),
  deleteRevision: (id: string) => request<{ ok: boolean }>(`/api/revisions/${id}`, { method: "DELETE" }),
  reimport: (id: string) => post<{ id: string }>(`/api/revisions/${id}/reimport`),
  cancelImport: (id: string) => post<{ cancelled: boolean }>(`/api/revisions/${id}/cancel`),
  parts: (id: string) => request<PartsDoc>(`/api/revisions/${id}/parts`),
  face: (id: string, instanceId: string, face: number) => request<FaceInfo>(`/api/revisions/${id}/faces/${instanceId}/${face}`),
  fileUrl: (id: string, name: string) => `/api/revisions/${id}/files/${name}`,
  check: (id: string, setup?: Setup) => post<CheckResult>(`/api/revisions/${id}/check`, setup),
  suggestSetup: (id: string) => post<SuggestedSetup>(`/api/revisions/${id}/suggest-setup`),
  reconcileSetup: (id: string) => post<ReconcileResult>(`/api/revisions/${id}/reconcile-setup`),
  acceptFaceMatches: (id: string, setup: Setup) => post<AcceptFaceMatchesResult>(`/api/revisions/${id}/accept-face-matches`, setup),
  listRuns: (id: string) => request<RunMeta[]>(`/api/revisions/${id}/runs`),
  createRun: (id: string, body: { name?: string; settings?: Partial<RunSettings> }) => post<RunMeta>(`/api/revisions/${id}/runs`, body),
  importRays: async (file: File) =>
    request<{ file: string; count: number; hasWavelength: boolean; extentMm: number[]; backwardFraction: number; angles: number[]; intensity: number[]; spectrum?: { type: string; [k: string]: unknown } }>("/api/library/rays", {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: await file.arrayBuffer(),
    }),
  createSweep: (id: string, body: { name?: string; parameter: string; part: string; values: number[]; settings?: Partial<RunSettings> }) =>
    post<{ sweepId: string; runs: RunMeta[] }>(`/api/revisions/${id}/sweeps`, body),

  // runs
  getRun: (id: string) => request<RunMeta>(`/api/runs/${id}`),
  renameRun: (id: string, name: string) => request<RunMeta>(`/api/runs/${id}`, jsonInit("PATCH", { name })),
  deleteRun: (id: string) => request<{ ok: boolean }>(`/api/runs/${id}`, { method: "DELETE" }),
  cancelRun: (id: string) => post<{ cancelled: boolean }>(`/api/runs/${id}/cancel`),
  results: (id: string, state?: string, analysis?: Analysis, ledMaps = true) =>
    request<StateResult>(`/api/runs/${id}/results${query({ state, ...analysisParams(analysis), led_maps: ledMaps })}`),
  runScene: (id: string) => request<RunScene>(`/api/runs/${id}/scene`),
  paths: (id: string) => request<{ paths: RayPath[] }>(`/api/runs/${id}/paths`),

  // light path analysis of a run
  analysis: (id: string, o: { state?: string; view?: string; detector?: number }) => request<AnalysisOverview>(`/api/runs/${id}/analysis${query(o)}`),
  analysisMap: (id: string, o: { group?: number | null; mode: string; state?: string; view?: string; detector?: number }) =>
    request<AnalysisMap>(`/api/runs/${id}/analysis/group-map${query(o)}`),
  analysisRegion: (id: string, body: { polygon: number[][]; detector?: number; state?: string; view?: string }) =>
    post<RegionReport>(`/api/runs/${id}/analysis/region`, body),
  analysisFace: (id: string, o: { instance: string; face: number; state?: string }) => request<FaceReport>(`/api/runs/${id}/analysis/face${query(o)}`),
  analysisHits: (id: string, o: { quantity: string; state?: string }) => request<HitMap>(`/api/runs/${id}/analysis/hits${query(o)}`),
  analysisFamilies: (id: string, o: { state?: string; view?: string }) => request<{ families: AnalysisFamily[] }>(`/api/runs/${id}/analysis/families${query(o)}`),
  analysisFlow: (id: string, o: { state?: string; view?: string; detector?: number; bands?: number }) => request<FlowDiagram>(`/api/runs/${id}/analysis/flow${query(o)}`),
  analysisFamilyMap: (id: string, o: { family: string; state?: string; view?: string; detector?: number }) =>
    request<AnalysisMap>(`/api/runs/${id}/analysis/family-map${query(o)}`),
  analysisGuidance: (id: string, o: { state?: string; view?: string; detector?: number; roi?: number[] | null; target?: number }) =>
    request<Guidance>(`/api/runs/${id}/analysis/guidance${query({ ...o, roi: o.roi ? o.roi.join(",") : undefined })}`),
  analysisRays: (id: string, body: RaysRequest) => post<RaysResponse>(`/api/runs/${id}/analysis/rays`, body),
  analysisRaysMap: (id: string, o: { filter: RaysFilter; state?: string; view?: string; detector?: number }) => post<AnalysisMap>(`/api/runs/${id}/analysis/rays-map`, o),
  analysisSlice: (id: string, body: SliceRequest) => post<SliceResponse>(`/api/runs/${id}/analysis/slice`, body),
  compare: (id: string, other: string, opts: { state?: string; otherState?: string; detector?: number; analysis?: Analysis }) =>
    request<CompareResult>(
      `/api/runs/${id}/compare${query({ other, state: opts.state, other_state: opts.otherState, detector: opts.detector, ...analysisParams(opts.analysis) })}`,
    ),
  imageUrl: (id: string, opts: { state?: string; detector?: number; kind?: string; vmax?: number; scale?: number; analysis?: Analysis }) =>
    `/api/runs/${id}/image.png${query({ state: opts.state, detector: opts.detector, kind: opts.kind, vmax: opts.vmax, scale: opts.scale, ...analysisParams(opts.analysis) })}`,
  getRender: (id: string) => request<RenderState>(`/api/runs/${id}/render`),
  createRender: (id: string, body: RenderSettings) => request<RenderState>(`/api/runs/${id}/render`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  cancelRender: (id: string) => request<{ cancelled: boolean }>(`/api/runs/${id}/render/cancel`, { method: "POST" }),
  renderUrl: (id: string, v: string | number = 0) => `/api/runs/${id}/render.png?v=${v}`,
  exportUrl: (id: string) => `/api/runs/${id}/export.zip`,

  // live preview
  startLive: (rid: string, body: { setup?: Setup | null; rays?: number; overrides?: LiveOverride[]; max_resolution?: number; target_noise?: number; solver?: "cpu" | "gpu" }) => post<RunMeta>(`/api/revisions/${rid}/live`, body),
  getLive: (rid: string) => request<{ run: RunMeta | null }>(`/api/revisions/${rid}/live`),
  keepLive: (runid: string) => post<RunMeta>(`/api/runs/${runid}/keep`),
  closeLiveView: () => post<{ ok: boolean }>("/api/live-view/close"),
  liveFrame: async (runid: string, body: LiveFrameRequest, signal?: AbortSignal): Promise<LiveFrame> => {
    const res = await fetch(`/api/runs/${runid}/live-frame`, { ...jsonInit("POST", body), signal });
    if (res.status === 202) return { kind: "status", ...(await res.json()) };
    if (!res.ok) {
      let message = res.statusText;
      try {
        const d = (await res.json()).detail;
        if (typeof d === "string") message = d;
      } catch {
        /* ignore */
      }
      throw new ApiError(message, res.status);
    }
    const h = (k: string) => Number(res.headers.get(k) ?? 0);
    return { kind: "image", blob: await res.blob(), spp: h("X-Spp"), rays: h("X-Rays"), ms: h("X-Ms"), variant: res.headers.get("X-Variant") ?? "" };
  },

  // library
  library: () => request<Library>("/api/library"),
  solvers: () => request<{ solvers: SolverInfo[] }>("/api/solvers"),
  saveLibraryEntry: <T>(kind: LibraryKind, doc: unknown) => post<T>(`/api/library/${kind}`, doc),
  deleteLibraryEntry: (kind: LibraryKind, id: string) => request<{ ok: boolean }>(`/api/library/${kind}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  mie: (body: { tio2_wt_pct: number; diameter_um: number; host: string; sigma_g: number; efficiency: number; thickness_mm: number }) => post<MieResult>("/api/diffuser/mie", body),
  fitDiffuser: (body: { samples: FitSample[]; host: string; rays?: number }) => post<FitResult>("/api/diffuser/fit", body),

  // app
  config: () => request<AppConfig>("/api/config"),
  setDataDir: (dataDir: string, copyExisting: boolean) => request<AppConfig>("/api/config", jsonInit("PUT", { dataDir, copyExisting })),
  pickFolder: (initial?: string) => post<{ path: string | null; error?: string }>(`/api/fs/pick-folder${query({ initial })}`),
  quit: () => post<{ ok: boolean }>("/api/app/quit"),
  setUpdateSettings: (s: Partial<UpdateSettings>) => request<UpdateSettings>("/api/update/settings", jsonInit("PUT", s)),
  checkUpdate: () => post<UpdateCheck>("/api/update/check"),
  updateStatus: () => request<UpdateStatus>("/api/update/status"),
  downloadUpdate: () => post<UpdateStatus>("/api/update/download"),
  installUpdate: () => post<{ launched: boolean; message: string; path: string }>("/api/update/install"),
};
