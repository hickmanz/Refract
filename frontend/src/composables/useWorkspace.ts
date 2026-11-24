// Everything the revision page shares between its panels: the revision, its parts, the project setup (auto-saved),
// the library, the server's validation of the setup and the simulation runs.
import { computed, inject, onBeforeUnmount, provide, ref, shallowRef, watch, type InjectionKey, type Ref } from "vue";
import { api, type CheckResult, type GroupIssue, type Library, type PartDoc, type PartsDoc, type Project, type RevisionMeta, type RunMeta, type Setup } from "../api";
import { emptySetup, instanceForPattern } from "../utils/setup";
import type { FaceHighlight } from "./useSetupScene";

export type PickMode =
  | { kind: "led"; ledId: string }
  | { kind: "detector"; detectorId: string }
  | { kind: "override"; instanceId: string; finish: string }
  | { kind: "part" }
  | { kind: "group"; groupId: string }
  | null;

/** Colour of an analysis group (#rrggbb) as a three.js number. */
export const hexToNumber = (hex: string | undefined, fallback = 0x8b5cf6) => {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex ?? "").trim());
  return m ? parseInt(m[1], 16) : fallback;
};

/**
 * How a revision is called in lists: "Rev 2" (plus its label), "fine mesh of Rev 2" for a re-mesh, otherwise its label or file name.
 * The number is stored on the revision, so deleting another revision does not renumber it. The server's compare uses the same wording.
 */
export function revisionName(rev: RevisionMeta, all: RevisionMeta[] = []): string {
  if (rev.number) return `Rev ${rev.number}${rev.label ? ` “${rev.label}”` : ""}`;
  const source = rev.remeshedFrom ? all.find((r) => r.id === rev.remeshedFrom) : undefined;
  if (source?.number) return `${rev.meshQuality ?? "normal"} mesh of Rev ${source.number}`;
  return rev.label || rev.filename;
}

export function createWorkspace(rid: Ref<string>) {
  const meta = ref<RevisionMeta | null>(null);
  const project = ref<Project | null>(null);
  const loadError = ref<string | null>(null);
  const partsDoc = shallowRef<PartsDoc | null>(null);
  const library = ref<Library | null>(null);
  const setup = ref<Setup | null>(null);
  const saveState = ref<"idle" | "saving" | "saved" | "error">("idle");
  const saveError = ref("");
  const check = ref<CheckResult | null>(null);
  const checkError = ref("");
  const checking = ref(false);
  const runs = ref<RunMeta[]>([]);
  const pick = ref<PickMode>(null);
  const selectedPart = ref<string | null>(null);
  const meshParts = ref<string[]>([]); // instances whose optical (ray-traced) mesh is drawn over the model
  const meshAll = ref(false); // draw the optical mesh of every part shown in the viewport (the toolbar "Mesh" toggle)
  const shownParts = ref<string[]>([]); // instances currently shown in the viewport (kept in sync by the revision view)
  const suggestNotes = ref<string[]>([]);
  const newLedModel = ref("white_generic"); // library model used when LEDs are added by picking faces
  const overrideFinish = ref("mirror"); // finish applied to faces picked as overrides
  const showGroups = ref(false); // paint the analysis groups on the model (set while the Groups tab is open)
  const focusGroup = ref<string | null>(null); // the group highlighted more strongly
  const runOrientationStale = ref(false); // the opened run's exit map is flipped compared with the current setup (set by the page)

  const parts = computed<PartDoc[]>(() => partsDoc.value?.instances ?? []);
  const isActive = computed(() => meta.value?.status === "queued" || meta.value?.status === "running");
  const ready = computed(() => meta.value?.status === "ready");
  /** The variant of this revision; its setup is the one edited here. */
  const variantId = computed(() => meta.value?.variantId ?? project.value?.variants?.[0]?.id);
  const variant = computed(() => project.value?.variants?.find((v) => v.id === variantId.value) ?? null);
  /** The design the variant is in. */
  const design = computed(() => project.value?.designs?.find((d) => d.id === variant.value?.designId) ?? null);
  const fetchSetup = () => api.getVariantSetup(meta.value!.projectId, variantId.value!);
  const storeSetup = (s: Setup) => api.putVariantSetup(meta.value!.projectId, variantId.value!, s);

  // ── polling the import job ─────────────────────────────────────────────────
  let pollTimer: number | undefined;
  async function refreshMeta() {
    try {
      meta.value = await api.getRevision(rid.value);
      loadError.value = null;
      if (meta.value.projectId && (!project.value || project.value.id !== meta.value.projectId)) loadProject();
    } catch (e) {
      loadError.value = (e as Error).message;
    }
  }
  async function loadProject() {
    if (!meta.value) return;
    try {
      project.value = await api.getProject(meta.value.projectId);
    } catch {
      project.value = null;
    }
  }
  function schedulePoll() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      await refreshMeta();
      if (isActive.value) schedulePoll();
    }, 700);
  }

  // ── setup (auto-saved) ─────────────────────────────────────────────────────
  let saveTimer: number | undefined;
  let checkTimer: number | undefined;
  let loadingSetup = false;
  async function loadSetup() {
    if (!meta.value) return;
    loadingSetup = true;
    try {
      const s = await fetchSetup();
      setup.value = { ...emptySetup(), ...s, parts: s.parts?.length ? s.parts : emptySetup().parts };
    } finally {
      // the watcher fires after this tick; ignore that change
      window.setTimeout(() => (loadingSetup = false), 0);
    }
    scheduleCheck(0);
  }

  async function saveNow() {
    if (!setup.value || !meta.value) return;
    saveState.value = "saving";
    try {
      await storeSetup(setup.value);
      saveState.value = "saved";
      saveError.value = "";
    } catch (e) {
      saveState.value = "error";
      saveError.value = (e as Error).message;
    }
  }
  watch(
    setup,
    () => {
      if (loadingSetup || !setup.value) return;
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(saveNow, 500);
      scheduleCheck(pick.value ? 200 : 900); // faster while faces are being picked
    },
    { deep: true },
  );

  function scheduleCheck(delay: number) {
    window.clearTimeout(checkTimer);
    checkTimer = window.setTimeout(runCheck, delay);
  }
  async function runCheck() {
    if (!setup.value || !ready.value) return;
    checking.value = true;
    try {
      check.value = await api.check(rid.value, setup.value);
      checkError.value = "";
    } catch (e) {
      check.value = null;
      checkError.value = (e as Error).message;
    } finally {
      checking.value = false;
    }
  }

  /** Apply an edit to the setup (the auto-save and re-validation follow). */
  function mutate(fn: (s: Setup) => void) {
    if (!setup.value) return;
    fn(setup.value);
  }

  async function suggest(): Promise<void> {
    const s = await api.suggestSetup(rid.value);
    suggestNotes.value = s.notes;
    setup.value = { parts: s.parts, leds: s.leds, detectors: s.detectors, states: s.states };
  }

  /** Adapt the variant's setup to this revision's geometry (LEDs added or removed, "only" states regenerated). */
  async function reconcile(): Promise<void> {
    const r = await api.reconcileSetup(rid.value);
    suggestNotes.value = r.warnings;
    setup.value = { ...emptySetup(), ...r.setup };
  }

  /** Store the faces that were re-matched on this revision in the setup, so the "re-matched" notices go away. */
  async function acceptFaceMatches(): Promise<string[]> {
    if (!setup.value) return [];
    const r = await api.acceptFaceMatches(rid.value, setup.value);
    if (r.changes.length) setup.value = { ...emptySetup(), ...r.setup }; // the auto-save and re-check follow
    return r.changes;
  }

  // ── library ───────────────────────────────────────────────────────────────
  async function loadLibrary() {
    library.value = await api.library();
  }

  // ── runs ─────────────────────────────────────────────────────────────────
  let runTimer: number | undefined;
  async function loadRuns() {
    try {
      runs.value = await api.listRuns(rid.value);
    } catch {
      /* not imported yet */
    }
    window.clearTimeout(runTimer);
    if (runs.value.some((r) => r.status === "queued" || r.status === "running")) runTimer = window.setTimeout(loadRuns, 800);
  }

  // ── group faces that do not exist in this model (reported by the server's check) ──────
  type FaceSel = { part: string; face: number | null };
  const sameSel = (a: FaceSel, b: { part: string; face: number | null }) => a.part === b.part && (a.face ?? null) === (b.face ?? null);
  /** The missing selections of a group that are still in the setup (an edit since the last check is taken into account). */
  function issuesFor(groupId: string): GroupIssue["missing"] {
    const issue = check.value?.groupIssues?.find((i) => i.id === groupId);
    const def = setup.value?.analysisGroups?.find((g) => g.id === groupId);
    if (!issue || !def) return [];
    return issue.missing.filter((m) => def.faces.some((f) => sameSel(f, m)));
  }
  const isMissing = (groupId: string, f: FaceSel) => issuesFor(groupId).some((m) => sameSel(f, m));
  /** Groups that have missing faces, and how many selections are missing in total. */
  const groupsWithIssues = computed(() => (setup.value?.analysisGroups ?? []).filter((g) => issuesFor(g.id).length));
  const missingCount = computed(() => groupsWithIssues.value.reduce((n, g) => n + issuesFor(g.id).length, 0));
  /** Take every missing face selection out of the groups; returns how many were removed. */
  function removeMissingFaces(): number {
    let removed = 0;
    mutate((s) => {
      for (const g of s.analysisGroups ?? []) {
        const miss = issuesFor(g.id);
        if (!miss.length) continue;
        const before = g.faces.length;
        g.faces = g.faces.filter((f) => !miss.some((m) => sameSel(f, m)));
        removed += before - g.faces.length;
      }
    });
    return removed;
  }

  // ── 3D highlights derived from the (server-resolved) setup ───────────────────────
  const highlights = computed<FaceHighlight[]>(() => {
    const out: FaceHighlight[] = [];
    const s = setup.value;
    if (!s) return out;
    const res = check.value?.resolved;
    // the exit surface that faces are being picked into is painted from the setup itself, like a picked group, so a click shows at once
    // (the server check, which resolves the faces of all the others, follows later)
    const pickedDetId = pick.value?.kind === "detector" ? pick.value.detectorId : null;
    for (const d of s.detectors) {
      if (d.id === pickedDetId) {
        d.faces.forEach((f, i) => {
          if (f.face === null || f.face === undefined) return;
          for (const inst of instanceForPattern(f.part, parts.value)) out.push({ key: `det:${d.id}:${i}:${inst.id}`, instanceId: inst.id, face: f.face, color: 0x06b6d4, opacity: 0.75 });
        });
        continue;
      }
      const list = res?.detectors?.[d.id];
      if (list) list.forEach((f, i) => out.push({ key: `det:${d.id}:${i}`, instanceId: f.instance, face: f.face, color: 0x06b6d4, opacity: 0.75 }));
    }
    for (const led of s.leds) {
      const f = res?.leds?.[led.id];
      if (f) out.push({ key: `led:${led.id}`, instanceId: f.instance, face: f.face, color: 0xf97316, opacity: 0.85 });
    }
    if (showGroups.value) {
      // the group that faces are being picked into is painted from the setup itself, so a click shows at once (the server check follows later)
      const picked = pick.value?.kind === "group" ? pick.value.groupId : null;
      const pickedDef = picked ? s.analysisGroups?.find((g) => g.id === picked) : undefined;
      if (pickedDef) {
        const color = hexToNumber(pickedDef.color || check.value?.groups?.find((g) => g.id === picked)?.color);
        pickedDef.faces.forEach((f, i) => {
          if (f.face === null || f.face === undefined || isMissing(pickedDef.id, f)) return;
          for (const inst of instanceForPattern(f.part, parts.value)) out.push({ key: `grp:${picked}:${i}:${inst.id}`, instanceId: inst.id, face: f.face, color, opacity: 0.85 });
        });
      }
      for (const g of check.value?.groups ?? []) {
        if (g.index >= 62 || g.id === picked) continue; // "other tagged faces"
        const color = hexToNumber(g.color);
        const focus = focusGroup.value === g.id;
        g.faces.forEach((f, i) => {
          if (f.face === null || f.face === undefined) return;
          out.push({ key: `grp:${g.id}:${i}`, instanceId: f.instance, face: f.face, color, opacity: focus ? 0.95 : focusGroup.value ? 0.3 : 0.65 });
        });
      }
    }
    return out;
  });

  function start() {
    refreshMeta().then(() => {
      if (isActive.value) schedulePoll();
    });
  }

  watch(
    () => meta.value?.status,
    async (status) => {
      if (status === "ready" && !partsDoc.value) {
        partsDoc.value = await api.parts(rid.value);
        await Promise.all([loadSetup(), loadLibrary(), loadRuns()]);
      }
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    window.clearTimeout(pollTimer);
    window.clearTimeout(saveTimer);
    window.clearTimeout(checkTimer);
    window.clearTimeout(runTimer);
    // flush a pending save
    if (saveTimer !== undefined && setup.value && meta.value) void storeSetup(setup.value);
  });

  return {
    rid,
    meta,
    project,
    loadError,
    partsDoc,
    parts,
    library,
    setup,
    saveState,
    saveError,
    check,
    checkError,
    checking,
    runs,
    pick,
    selectedPart,
    meshParts,
    meshAll,
    shownParts,
    showGroups,
    focusGroup,
    runOrientationStale,
    suggestNotes,
    newLedModel,
    overrideFinish,
    isActive,
    ready,
    variantId,
    variant,
    design,
    reconcile,
    acceptFaceMatches,
    issuesFor,
    isMissing,
    groupsWithIssues,
    missingCount,
    removeMissingFaces,
    highlights,
    start,
    schedulePoll,
    refreshMeta,
    loadProject,
    loadLibrary,
    loadRuns,
    mutate,
    suggest,
    runCheck,
    scheduleCheck,
    saveNow,
  };
}

export type Workspace = ReturnType<typeof createWorkspace>;
const KEY: InjectionKey<Workspace> = Symbol("refract-workspace");
export const provideWorkspace = (ws: Workspace) => provide(KEY, ws);
export function useWorkspace(): Workspace {
  const ws = inject(KEY);
  if (!ws) throw new Error("workspace not provided");
  return ws;
}
