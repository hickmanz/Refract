// Live physics: a progressive simulation of the setup as it is in the editor right now. Results appear within a
// couple of seconds and sharpen as more rays are traced; changing the setup (or the diffuser slider) restarts it.
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { api, type DetectorResult, type LiveOverride, type RunMeta, type RunScene, type StateResult } from "../api";
import { partPattern, resolvePart } from "../utils/setup";
import { useSolvers } from "./useSolvers";
import type { Workspace } from "./useWorkspace";

export const LIVE_RAY_CHOICES = [100_000, 400_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000];
/** The TiO2 slider works on a logarithmic scale between these loadings (wt %). */
export const TIO2_RANGE = [0.0005, 5] as const;

export function useLivePreview(ws: Workspace) {
  const { solver, gpu, chosen } = useSolvers();
  const enabled = ref(false);
  const rays = ref(10_000_000);
  /** 0 = trace all `rays`; otherwise stop earlier once the median noise per map pixel is below this fraction. */
  const targetNoise = ref(0);
  const run = shallowRef<RunMeta | null>(null);
  /** The run the photoreal view draws from; survives "keep" so the picture does not vanish. */
  const viewRunId = ref<string | null>(null);
  const result = shallowRef<StateResult | null>(null);
  const scene = shallowRef<RunScene | null>(null);
  const starting = ref(false);
  const error = ref("");
  const notice = ref("");

  // ── which state (set of LEDs that are on) is shown ──────────────────────────────
  // The simulation traces every LED channel, so switching state needs no new run: the results and the photoreal view
  // just combine the channels differently. A setup without states has the one state "all" (as on the server).
  const states = computed(() => {
    const list = (ws.setup.value?.states ?? []).filter((s) => s.id);
    return list.length ? list.map((s) => ({ id: s.id, name: s.name || s.id })) : [{ id: "all", name: "All LEDs on" }];
  });
  const stateId = ref("");
  const shownState = computed(() => (states.value.some((s) => s.id === stateId.value) ? stateId.value : states.value[0].id));

  // ── what the slider changes ──────────────────────────────────────────────────
  const partId = ref("");
  const tio2 = ref<number | null>(null); // null: the value of the setup
  const diffusers = computed(() => {
    const s = ws.setup.value;
    const lib = ws.library.value;
    if (!s || !lib) return [];
    return ws.parts.value
      .map((inst) => {
        const eff = resolvePart(inst, s.parts);
        const mat = eff.enabled && eff.material ? lib.materials[eff.material] : null;
        const sc = mat?.scattering as { type?: string; tio2_wt_pct?: number } | undefined;
        return { inst, material: mat?.name ?? "", mie: sc?.type === "mie", base: sc?.type === "mie" ? Number(sc.tio2_wt_pct ?? 0) : null };
      })
      .filter((x) => x.mie);
  });
  watch(
    diffusers,
    (d) => {
      if (!d.some((x) => x.inst.id === partId.value)) {
        partId.value = d[0]?.inst.id ?? "";
        tio2.value = null;
      }
    },
    { immediate: true },
  );
  const baseTio2 = computed(() => diffusers.value.find((d) => d.inst.id === partId.value)?.base ?? null);
  const shownTio2 = computed(() => tio2.value ?? baseTio2.value);

  function overrides(): LiveOverride[] {
    const inst = ws.parts.value.find((p) => p.id === partId.value);
    if (!inst || tio2.value === null || baseTio2.value === null) return [];
    return [{ parameter: "tio2_wt_pct", part: partPattern(inst, ws.parts.value), value: tio2.value }];
  }

  // ── the run ────────────────────────────────────────────────────────────────
  let inFlight = false;
  let again = false;
  let pollTimer: number | undefined;
  let debounce: number | undefined;
  let generation = 0;

  async function start() {
    if (!enabled.value || !ws.setup.value) return;
    if (inFlight) {
      again = true; // one start at a time; the latest setup is used when this one has returned
      return;
    }
    inFlight = true;
    starting.value = true;
    try {
      do {
        again = false;
        error.value = "";
        try {
          const setup = JSON.parse(JSON.stringify(ws.setup.value));
          const r = await api.startLive(ws.rid.value, { setup, rays: rays.value, overrides: overrides(), target_noise: targetNoise.value, solver: solver.value });
          if (!enabled.value) {
            void api.cancelRun(r.id).catch(() => undefined);
            break;
          }
          generation++;
          run.value = r;
          viewRunId.value = r.id;
          notice.value = "";
          schedulePoll(0, generation, -1);
        } catch (e) {
          error.value = (e as Error).message;
          run.value = null;
        }
      } while (again && enabled.value);
    } finally {
      inFlight = false;
      starting.value = false;
    }
  }

  function schedulePoll(delay: number, gen: number, lastRays: number) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(() => poll(gen, lastRays), delay);
  }

  async function poll(gen: number, lastRays: number) {
    if (gen !== generation || !enabled.value) return;
    let next = lastRays;
    let finished = false;
    try {
      const { run: r } = await api.getLive(ws.rid.value);
      if (gen !== generation) return;
      if (!r || r.id !== run.value?.id) {
        schedulePoll(500, gen, lastRays);
        return;
      }
      run.value = r;
      if (r.status === "failed" || r.status === "cancelled") {
        if (r.status === "failed") error.value = r.error || "The live simulation failed";
        return;
      }
      const done = r.live?.rays ?? 0;
      finished = r.status === "ready";
      if ((done > 0 && done !== lastRays) || finished) {
        if (sceneRun !== r.id) await loadScene(r.id);
        const sid = shownState.value;
        const res = await api.results(r.id, sid, undefined, false);
        if (gen !== generation) return;
        if (sid === shownState.value) result.value = res; // else the state was changed meanwhile and its own load follows
        next = done;
        if (finished) return; // the last pass is in
      }
    } catch {
      /* the run may not have written its first pass yet */
    }
    if (gen === generation) schedulePoll(450, gen, next);
  }
  /** Another state was picked: read the results of the run so far again for it (the run itself carries on). */
  async function reloadResult() {
    const r = run.value;
    if (!enabled.value || !r || !(r.live?.rays || r.status === "ready")) return;
    const sid = shownState.value;
    const gen = generation;
    try {
      const res = await api.results(r.id, sid, undefined, false);
      if (gen === generation && sid === shownState.value) result.value = res;
    } catch {
      // no light in this state (yet): do not keep showing the numbers of the previous state
      if (gen === generation && sid === shownState.value) result.value = null;
    }
  }
  watch(shownState, () => void reloadResult());

  let sceneRun = "";
  async function loadScene(rid: string) {
    try {
      scene.value = await api.runScene(rid);
      sceneRun = rid;
    } catch {
      /* not written yet */
    }
  }

  /** Restart soon: the setup was edited. */
  function restartSoon(delay = 900) {
    if (!enabled.value) return;
    window.clearTimeout(debounce);
    debounce = window.setTimeout(start, delay);
  }

  watch(() => ws.setup.value, () => restartSoon(), { deep: true });
  watch(() => ws.library.value, () => restartSoon(), { deep: true });
  watch(rays, () => restartSoon(100));
  watch(targetNoise, () => restartSoon(100));
  watch(solver, () => restartSoon(100));

  function setTio2(v: number | null) {
    tio2.value = v;
    restartSoon(250);
  }
  function selectPart(id: string) {
    partId.value = id;
    tio2.value = null;
    restartSoon(100);
  }

  async function enable() {
    enabled.value = true;
    await start();
  }
  async function disable() {
    enabled.value = false;
    window.clearTimeout(debounce);
    window.clearTimeout(pollTimer);
    generation++;
    const r = run.value;
    run.value = null;
    result.value = null;
    scene.value = null;
    viewRunId.value = null;
    error.value = "";
    if (r) {
      try {
        await api.closeLiveView();
        await api.cancelRun(r.id);
        await api.deleteRun(r.id);
      } catch {
        /* the next live preview clears it up */
      }
    }
  }
  function toggle() {
    return enabled.value ? disable() : enable();
  }

  /** Make the finished preview an ordinary run. */
  async function keep() {
    const r = run.value;
    if (!r) return;
    try {
      const kept = await api.keepLive(r.id);
      await ws.loadRuns();
      notice.value = `Saved as “${kept.name}” in the Results tab. Moving a slider starts a new preview.`;
      window.clearTimeout(pollTimer);
      generation++;
      run.value = null;
    } catch (e) {
      error.value = (e as Error).message;
    }
  }

  const phase = computed(() => {
    if (!enabled.value) return "off";
    if (error.value) return "error";
    if (starting.value || !run.value) return "starting";
    if (run.value.status === "ready") return "done";
    return "running";
  });
  const progress = computed(() => {
    const l = run.value?.live;
    if (!l || !l.target) return 0;
    return Math.min(1, l.rays / l.target);
  });
  const detector = computed<DetectorResult | null>(() => result.value?.detectors[0] ?? null);

  onBeforeUnmount(() => {
    window.clearTimeout(pollTimer);
    window.clearTimeout(debounce);
    generation++;
    if (enabled.value && run.value) {
      void api.closeLiveView().catch(() => undefined);
      void api.cancelRun(run.value.id).catch(() => undefined);
    }
  });

  return {
    enabled, rays, targetNoise, gpu, chosen, run, viewRunId, result, scene, starting, error, notice,
    states, stateId, shownState, diffusers, partId, tio2, baseTio2, shownTio2, phase, progress, detector,
    toggle, keep, setTio2, selectPart, restartSoon,
  };
}

export type LivePreview = ReturnType<typeof useLivePreview>;
