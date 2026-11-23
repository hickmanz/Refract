<template>
  <canvas ref="canvasEl" class="live-view-canvas" :class="{ shown: hasFrame }" />
</template>

<script setup lang="ts">
// The photoreal live view: a picture of the model lit by the simulated exit surface, drawn over the 3D view with the
// same camera. It is rendered on the GPU by Mitsuba in a helper process that keeps accumulating samples while the
// camera is still, so the picture sharpens by itself. While the camera moves the picture is hidden and the plain CAD
// view underneath is used to position it; the picture comes back when the camera has been still for a moment.
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, ApiError, type LiveCamera } from "../api";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Viewer = any;

const props = defineProps<{
  viewer: Viewer;
  runId: string;
  /** Changes whenever the simulation has new results; the picture then starts over with the new light. */
  resultKey: string;
  simRunning: boolean;
  exposure: number;
  ambient: number;
  whiteBalance: boolean;
  /** "map": the exit surface from the simulation. "transport": the LEDs light the whole model (noisy, slow to clean up). */
  mode: "map" | "transport";
  /** Long side of the picture in pixels; 0 follows the size of the 3D view. */
  resolution: number;
  /** The state (which LEDs are on) to draw; empty for the first one. */
  state: string;
  /** Exit surfaces (detector indices) left dark in the picture; they do not take part in its exposure either. */
  hiddenExits: number[];
}>();
const emit = defineEmits<{
  status: [s: { state: "starting" | "waiting" | "live" | "error"; message: string; spp: number; ms: number; variant: string; fps: number }];
}>();

const canvasEl = ref<HTMLCanvasElement | null>(null);
const hasFrame = ref(false);

const targetSpp = () => (props.mode === "transport" ? 4000 : 192);
const stillLongSide = () => (props.mode === "transport" ? 800 : 1100);
const STILL_MS = 220; // the camera counts as still after this long without a change

let token = 0;
let abort: AbortController | null = null;
let camKey = "";
let camChange = 0;
let watcher: number | undefined;

// Runs on its own timer, not in the render loop (which may be waiting for a picture): the moment the camera moves the
// photoreal picture is hidden, so orbiting is as smooth as the plain CAD view.
function watchCamera() {
  const c = cameraNow();
  if (!c) return;
  if (c.key !== camKey) {
    if (camKey !== "") hasFrame.value = false;
    camKey = c.key;
    camChange = performance.now();
  }
}

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

function cameraNow(): { cam: LiveCamera; aspect: number; key: string } | null {
  const s = props.viewer.getCameraState?.();
  if (!s) return null;
  // an orthographic view shows `orthoHalfH / zoom` above the target
  const ortho = s.projection === "orthographic";
  const half = ortho ? (s.orthoHalfH ?? 1) / (s.orthoZoom || 1) : null;
  const cam: LiveCamera = {
    position: [s.position.x, s.position.y, s.position.z],
    target: [s.target.x, s.target.y, s.target.z],
    up: [s.up.x, s.up.y, s.up.z],
    fov: s.fov,
    orthoHalfH: half,
  };
  const key = [...cam.position, ...cam.target, ...cam.up, cam.fov, half, s.aspect].map((v) => (typeof v === "number" ? v.toFixed(5) : String(v))).join(",");
  return { cam, aspect: s.aspect, key };
}

async function draw(blob: Blob) {
  const canvas = canvasEl.value;
  if (!canvas) return;
  const bmp = await createImageBitmap(blob);
  if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
    canvas.width = bmp.width;
    canvas.height = bmp.height;
  }
  canvas.getContext("2d")?.drawImage(bmp, 0, 0);
  bmp.close();
  hasFrame.value = true;
}

async function loop(my: number) {
  let lastView = "";
  let sinceMove = 0; // pictures drawn since the camera last moved
  let sentForResult = "";
  let lastSpp = 0;
  let fpsT = performance.now();
  let frames = 0;
  let fps = 0;
  let notReady = 0;

  while (my === token) {
    const c = cameraNow();
    if (!c || !props.viewer.modelLoaded?.value) {
      await sleep(200);
      continue;
    }
    watchCamera();
    const moving = performance.now() - camChange < STILL_MS;
    if (moving) {
      lastSpp = 0;
      sinceMove = 0;
      await sleep(30); // the CAD view is on show: do not spend the GPU on pictures nobody sees
      continue;
    }
    const resultKey = props.resultKey;
    const vk = `${props.exposure}|${props.ambient}|${props.whiteBalance}|${props.mode}|${props.resolution}|${props.state}|${props.hiddenExits.join(",")}`;
    if (vk !== lastView) {
      lastView = vk;
      lastSpp = 0;
      sinceMove = 0;
      sentForResult = ""; // look and light settings changed: draw once more
    }

    // nothing to do: the picture is as sharp as it gets, the camera is still and the light has not changed
    const settled = !moving && lastSpp >= targetSpp() && sentForResult === resultKey && !props.simRunning;
    if (settled) {
      await sleep(80);
      continue;
    }

    let L = props.resolution;
    if (!L) {
      // follow the 3D view: no more pixels than the screen shows
      const host = canvasEl.value;
      const cap = host ? Math.max(320, Math.round(Math.max(host.clientWidth, host.clientHeight) * (window.devicePixelRatio || 1))) : stillLongSide();
      L = Math.min(stillLongSide(), cap);
    }
    // a big picture costs more per sample: ask for fewer at a time so it still answers quickly
    const share = L > 2800 ? 4 : L > 1800 ? 2 : 1;
    const width = c.aspect >= 1 ? L : Math.round(L * c.aspect);
    const height = c.aspect >= 1 ? Math.round(L / c.aspect) : L;

    abort = new AbortController();
    try {
      const reply = await api.liveFrame(
        props.runId,
        {
          camera: c.cam,
          width: Math.max(64, width),
          height: Math.max(64, height),
          // the first picture after a move comes quickly, then it refines
          spp: Math.max(1, Math.round((sinceMove === 0 ? 4 : props.mode === "transport" ? 16 : lastSpp < 32 ? 8 : 16) / share)),
          quality: 88,
          exposure: props.exposure,
          ambient: props.ambient,
          white_balance: props.whiteBalance,
          mode: props.mode,
          state: props.state || null,
          hidden_exits: props.hiddenExits,
        },
        abort.signal,
      );
      if (my !== token) return;
      if (reply.kind === "status") {
        if (reply.status === "error") {
          emit("status", { state: "error", message: reply.error || "The live view failed", spp: 0, ms: 0, variant: "", fps: 0 });
          return;
        }
        notReady++;
        emit("status", {
          state: reply.status === "starting" ? "starting" : "waiting",
          message: reply.status === "starting" ? "Starting the renderer…" : "Waiting for the first rays…",
          spp: 0,
          ms: 0,
          variant: "",
          fps: 0,
        });
        await sleep(notReady < 5 ? 250 : 500);
        continue;
      }
      notReady = 0;
      // the camera moved while this picture was being made: it is of the wrong view, drop it
      if (cameraNow()?.key !== c.key) {
        lastSpp = 0;
        sinceMove = 0;
        continue;
      }
      sentForResult = resultKey;
      lastSpp = reply.spp;
      sinceMove++;
      await draw(reply.blob);
      frames++;
      const t = performance.now();
      if (t - fpsT > 1000) {
        fps = (frames * 1000) / (t - fpsT);
        frames = 0;
        fpsT = t;
      }
      emit("status", { state: "live", message: "", spp: reply.spp, ms: reply.ms, variant: reply.variant, fps });
      // the simulation is still adding rays: look for the next texture only now and then, the GPU is shared
      if (props.simRunning && !moving && reply.spp >= targetSpp()) await sleep(500);
    } catch (e) {
      if (my !== token || (e as Error).name === "AbortError") return;
      if (e instanceof ApiError && e.status === 404) {
        await sleep(300); // the preview was just restarted and this run is gone
        continue;
      }
      emit("status", { state: "error", message: e instanceof ApiError ? e.message : String(e), spp: 0, ms: 0, variant: "", fps: 0 });
      return;
    }
  }
}

function begin() {
  void loop(++token);
}
function end() {
  token++;
  abort?.abort();
}

onMounted(() => {
  watcher = window.setInterval(watchCamera, 25);
  begin();
});
onBeforeUnmount(() => {
  window.clearInterval(watcher);
  end();
});
// another run (the live preview was restarted): start over with it
watch(
  () => props.runId,
  () => {
    end();
    window.setTimeout(begin, 30);
  },
);
</script>
