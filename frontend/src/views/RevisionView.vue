<template>
  <div class="checker lightsim">
    <!-- ───────── top bar ───────── -->
    <header class="topbar">
      <RouterLink to="/" class="tbtn icon" title="All projects" aria-label="All projects"><ArrowLeft :size="18" /></RouterLink>
      <div class="topbar-title">
        <strong>{{ ws.project.value?.name ?? ws.meta.value?.filename ?? "Loading…" }}</strong>
        <span v-if="ws.ready.value && ws.partsDoc.value" class="muted">
          {{ ws.parts.value.length }} parts · {{ saveText }}
        </span>
        <span v-else-if="ws.isActive.value" class="muted">{{ ws.meta.value?.progress.phase || "Working…" }}</span>
      </div>
      <div class="spacer" />
      <label v-if="variants.length > 0" class="rev-picker" title="Variant: a design alternative (LED spacing, reflector, light pipe...) with its own setup">
        <GitBranch :size="14" />
        <select :value="ws.variantId.value" @change="goVariant(($event.target as HTMLSelectElement).value)">
          <option v-for="v in variants" :key="v.id" :value="v.id">{{ v.name }}</option>
        </select>
      </label>
      <button class="tbtn icon" :disabled="!ws.variant.value" title="Rename this variant" aria-label="Rename this variant" @click="renameVariant"><Pencil :size="15" /></button>
      <button class="tbtn icon" :disabled="variants.length < 2" title="Delete this variant with its revisions and runs" aria-label="Delete this variant" @click="deleteVariant"><Trash2 :size="15" /></button>
      <button class="tbtn" :disabled="!ws.ready.value || variantUploading" title="Add a design alternative: upload another STEP file as a new variant that starts with a copy of this setup" @click="variantFile?.click()">
        <Plus :size="16" /><span class="tlabel">New variant</span>
      </button>
      <input ref="variantFile" type="file" accept=".step,.stp,.STEP,.STP" hidden @change="onVariantFile" />
      <label v-if="revisions.length > 0" class="rev-picker" title="Revision of this variant">
        <History :size="14" />
        <select :value="id" @change="goRevision(($event.target as HTMLSelectElement).value)">
          <option v-for="(r, i) in revisions" :key="r.id" :value="r.id">
            Rev {{ revisions.length - i }}{{ r.label ? ` “${r.label}”` : "" }} · {{ r.filename }} · {{ fmtAgo(r.created) }}
          </option>
        </select>
      </label>
      <span class="tsep" />
      <div class="tgroup">
        <button class="tbtn" :disabled="!ws.ready.value" title="Materials, finishes and LED models" @click="libraryOpen = true"><BookOpen :size="16" /><span class="tlabel">Library</span></button>
        <button class="tbtn" :disabled="!ws.ready.value" title="Suggest materials, LEDs and the exit surface from the CAD" @click="onSuggest"><Wand2 :size="16" /><span class="tlabel">Auto-setup</span></button>
        <button class="tbtn" :disabled="!ws.ready.value" title="Keep this setup but follow the geometry: add or remove LEDs, regenerate the 'only' states" @click="onAdapt"><Replace :size="16" /><span class="tlabel">Adapt setup</span></button>
      </div>
      <span class="tsep" />
      <button class="tbtn icon" :class="{ on: showHotkeys }" title="Keyboard shortcuts" aria-label="Keyboard shortcuts" @click="showHotkeys = !showHotkeys">
        <Keyboard :size="17" />
      </button>
      <button class="tbtn icon" title="Toggle light / dark" aria-label="Toggle light / dark" @click="toggleTheme">
        <component :is="isDark ? Sun : Moon" :size="17" />
      </button>
    </header>

    <div class="checker-body">
      <!-- ───────── left: setup tabs ───────── -->
      <aside class="side">
        <template v-if="ws.ready.value && ws.setup.value && ws.library.value">
          <nav class="side-tabs">
            <button v-for="t in tabs" :key="t.id" :class="{ on: tab === t.id }" @click="tab = t.id">
              <component :is="t.icon" :size="15" />
              <span>{{ t.label }}</span>
              <span v-if="t.count !== undefined" class="tab-count">{{ t.count }}</span>
            </button>
          </nav>
          <div v-if="ws.suggestNotes.value.length" class="notice info">
            <Info :size="15" />
            <span>
              <span v-for="n in ws.suggestNotes.value" :key="n" class="block">{{ n }}</span>
              <a href="#" @click.prevent="ws.suggestNotes.value = []">dismiss</a>
            </span>
          </div>
          <KeepAlive>
            <PartsTab v-if="tab === 'parts'" />
            <LedsTab v-else-if="tab === 'leds'" />
            <DetectorTab v-else-if="tab === 'detector'" />
            <GroupsTab v-else-if="tab === 'groups'" />
            <StatesTab v-else-if="tab === 'states'" />
            <RunTab v-else-if="tab === 'runs'" @started="onStarted" @sweep-started="tab = 'results'" @show-results="tab = 'results'" />
            <ResultsTab v-else :active-run="activeRun" @open="openRun" @new-run="tab = 'runs'" />
          </KeepAlive>
        </template>

        <div v-else class="side-state">
          <template v-if="ws.isActive.value">
            <div class="spinner" />
            <h3>{{ phaseTitle }}</h3>
            <p class="muted">{{ ws.meta.value?.progress.phase }}</p>
            <div class="progress"><div class="progress-bar" :style="{ width: overallPercent + '%' }" /></div>
            <p class="muted small">{{ overallPercent }}%</p>
            <button class="btn" @click="cancelJob"><X :size="15" /> Cancel</button>
          </template>
          <template v-else-if="ws.meta.value?.status === 'failed' || ws.meta.value?.status === 'cancelled'">
            <AlertTriangle :size="30" class="warn-icon" />
            <h3>{{ ws.meta.value.status === "cancelled" ? "Cancelled" : "Import failed" }}</h3>
            <p class="muted">{{ ws.meta.value.error }}</p>
            <button class="btn primary" @click="reimport"><RefreshCw :size="15" /> Try again</button>
          </template>
          <template v-else-if="ws.loadError.value">
            <AlertTriangle :size="30" class="warn-icon" />
            <h3>Could not open this revision</h3>
            <p class="muted">{{ ws.loadError.value }}</p>
            <RouterLink to="/" class="btn">Back to projects</RouterLink>
          </template>
          <template v-else-if="ws.ready.value">
            <div class="spinner" />
            <p class="muted">Loading the setup…</p>
          </template>
        </div>
      </aside>

      <!-- ───────── right: 3D view and results ───────── -->
      <main class="stage" :class="{ picking: !!ws.pick.value, 'tree-open': treeShown }">
        <canvas ref="canvasEl" class="viewer-host" @contextmenu.prevent="onCanvasContextMenu" />

        <div v-if="ws.ready.value || ws.meta.value?.model_ready" class="viewbar" @click.stop>
          <div class="tgroup" title="Cut through the model. Drag the plane's handle in the 3D view to move the cut.">
            <span class="tgroup-icon"><Scissors :size="14" /></span>
            <button class="tbtn" :class="{ on: activePlanes.size === 0 }" @click="viewer.resetCuttingPlanes()">Off</button>
            <button v-for="a in ['x', 'y', 'z'] as const" :key="a" class="tbtn" :class="{ on: activePlanes.has(a) }" @click="togglePlane(a)">{{ a.toUpperCase() }}</button>
            <button
              class="tbtn icon"
              :class="{ on: planeVisible }"
              :title="planeVisible ? 'Hide the cutting-plane handle (P)' : 'Show the cutting-plane handle (P)'"
              aria-label="Show or hide the cutting-plane handle"
              @click="setPlaneVisible(!planeVisible)"
            >
              <component :is="planeVisible ? Eye : EyeOff" :size="15" />
            </button>
            <button class="tbtn icon" :class="{ on: sectionPanelOpen }" title="Section planes: distances and flip" aria-label="Section planes" @click="sectionPanelOpen = !sectionPanelOpen">
              <Slice :size="16" />
            </button>
          </div>
          <div class="tgroup">
            <button class="tbtn icon" :class="{ on: treeOpen }" title="Model tree (T)" aria-label="Model tree" @click="toggleTree"><ListTree :size="16" /></button>
            <button v-if="sceneCtl.anyHidden.value" class="tbtn" title="Show every part again (Alt+H)" @click="showAll"><Eye :size="15" /><span class="tlabel">Show all</span></button>
            <button class="tbtn" :class="{ on: dimOthers }" title="Fade the other parts when one is selected" @click="dimOthers = !dimOthers"><Ghost :size="15" /><span class="tlabel">Fade others</span></button>
            <button class="tbtn" :class="{ on: allTransparent }" title="Make every part transparent, to see what is inside" @click="allTransparent = !allTransparent"><Blend :size="15" /><span class="tlabel">See-through</span></button>
            <button class="tbtn" :class="{ on: showLeds }" title="Show LED positions and directions" @click="showLeds = !showLeds"><Lightbulb :size="15" /><span class="tlabel">LEDs</span></button>
            <button class="tbtn icon" title="Fit the whole model" aria-label="Fit the whole model" @click="viewer.resetView()"><Maximize :size="16" /></button>
          </div>
          <div class="tgroup">
            <button class="tbtn" :class="{ on: livePanelOpen || live.enabled.value }" title="Live physics while you edit, and a photoreal view of the result" @click="livePanelOpen = !livePanelOpen"><Zap :size="15" /><span class="tlabel">Live</span></button>
          </div>
          <div v-if="activeRun" class="tgroup">
            <button class="tbtn" :class="{ on: resultsShown }" title="Show the results of the opened run" @click="resultsShown = !resultsShown"><BarChart3 :size="15" /><span class="tlabel">Results</span></button>
            <button class="tbtn" :class="{ on: dockShown }" title="What to show in the 3D view: the map, surfaces and rays; click a surface to inspect it" @click="toggleDock"><ScanSearch :size="15" /><span class="tlabel">Analysis</span></button>
          </div>
        </div>

        <LiveViewOverlay
          v-if="view.on && live.enabled.value && live.viewRunId.value && !resultsCover"
          :viewer="viewer"
          :run-id="live.viewRunId.value"
          :result-key="liveKey"
          :sim-running="live.phase.value === 'running' || live.phase.value === 'starting'"
          :exposure="view.exposure"
          :ambient="view.ambient"
          :white-balance="view.whiteBalance"
          :mode="view.mode"
          :resolution="view.resolution"
          :state="live.shownState.value"
          @status="(s) => (view.status = s)"
        />
        <LivePanel v-if="livePanelOpen && ws.ready.value && !resultsCover" :live="live" :view="view" @close="livePanelOpen = false" />

        <div v-if="ws.pick.value && pickInfo" class="pick-pill" role="status">
          <span class="pp-dot" :style="{ background: pickInfo.color }" />
          <div class="pp-text">
            <div class="pp-title">{{ pickInfo.title }}</div>
            <div class="pp-sub">{{ pickInfo.sub }}</div>
            <div class="pp-live" :class="pickLive.tone">{{ pickLive.text }}</div>
          </div>
          <button class="btn small primary" @click="stopPick">Done <kbd>Esc</kbd></button>
        </div>
        <div v-if="hoverText && !ws.pick.value" class="legend hover-tip">{{ hoverText }}</div>

        <ModelTreeOverlay
          :visible="treeShown"
          :tree-data="treeData"
          :selected-node-ids="viewer.selectedNodeIds.value"
          @node-click="onTreeClick"
          @visibility-toggle="onTreeVisibility"
          @node-contextmenu="onTreeContextMenu"
        />

        <div v-if="!resultsCover" class="viewer-right-panels" :style="dockShown ? { right: '354px' } : undefined">
          <SectionPanel
            :visible="sectionPanelOpen"
            :active-planes="activePlanes"
            :flip-x="flip.x"
            :flip-y="flip.y"
            :flip-z="flip.z"
            :flip-face="false"
            :has-selected-face="false"
            :plane-distances="viewer.planeDistances"
            :show-caps="viewer.showSectionCaps.value"
            @update:show-caps="setCaps"
            @update:visible="(v: boolean) => (sectionPanelOpen = v)"
            @update:flip-x="(v: boolean) => (flip.x = v)"
            @update:flip-y="(v: boolean) => (flip.y = v)"
            @update:flip-z="(v: boolean) => (flip.z = v)"
            @reset="viewer.resetCuttingPlanes()"
            @plane-x="(fc: boolean) => togglePlane('x', fc)"
            @plane-y="(fc: boolean) => togglePlane('y', fc)"
            @plane-z="(fc: boolean) => togglePlane('z', fc)"
            @set-distance="(a: 'x' | 'y' | 'z', d: number) => viewer.setPlaneDistance(a, d)"
          >
            <SectionLight v-if="activeRun" :sl="sectionLight" :av="av" />
          </SectionPanel>
        </div>

        <AnalysisDock v-if="dockShown" :ctx="dockCtx" @close="dockOpen = false" @open-results="resultsShown = true" />

        <HotkeyOverlay :hotkeys="viewer.hotkeys.activeHotkeys.value" :visible="showHotkeys" />

        <div v-if="viewer.error.value" class="stage-msg error">{{ viewer.error.value }}</div>
        <div v-else-if="ws.meta.value?.model_ready && !viewer.modelLoaded.value" class="stage-msg"><div class="spinner small" /> {{ viewer.status.value }}</div>
        <div v-else-if="!ws.meta.value?.model_ready && !viewer.modelLoaded.value && ws.isActive.value" class="stage-msg"><div class="spinner small" /> Preparing the 3D model…</div>

        <!-- results (a run opened from the Results tab) -->
        <!-- (kept mounted while the 3D view is shown, so Back to results finds the same tab, selection and scroll position) -->
        <section v-if="activeRun" v-show="resultsShown" class="results-pane">
          <div class="results-head">
            <strong>{{ activeRunName }}</strong>
            <span class="spacer" />
            <button class="btn small" @click="resultsShown = false"><Box :size="14" /> Look at the 3D view</button>
            <button class="icon-btn" title="Close results" @click="closeResults"><X :size="16" /></button>
          </div>
          <div class="results-scroll">
            <ResultsView :run-id="activeRun" @loaded="onResultsLoaded" />
          </div>
        </section>
        <button v-if="activeRun && !resultsShown && !dockShown" class="back-to-results btn primary" @click="resultsShown = true"><BarChart3 :size="15" /> Back to results</button>
      </main>
    </div>

    <ContextMenu :visible="menu.visible" :items="menuItems" :x="menu.x" :y="menu.y" @pick="onMenuPick" @close="menu.visible = false" />
    <LibraryDialog v-if="libraryOpen" @close="libraryOpen = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch, type Component } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Blend,
  BookOpen,
  Box,
  Boxes,
  Crosshair,
  Eye,
  EyeOff,
  Focus,
  Ghost,
  GitBranch,
  History,
  Info,
  Keyboard,
  Lightbulb,
  ListTree,
  Maximize,
  Moon,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Replace,
  RotateCcw,
  ScanSearch,
  Scissors,
  Shapes,
  Slice,
  SlidersHorizontal,
  Sun,
  Trash2,
  Wand2,
  X,
  Zap,
} from "lucide-vue-next";
import * as THREE from "three";
import { api, type RunScene, type StateResult } from "../api";
import { createWorkspace, hexToNumber, provideWorkspace } from "../composables/useWorkspace";
import { provideAnalysisView, useAnalysisView, type AnalysisCtx } from "../composables/useAnalysisView";
import { useSectionLight } from "../composables/useSectionLight";
import SectionLight from "../components/analysis/SectionLight.vue";
import { useThreeViewer, type TreeNode } from "../composables/viewer/useThreeViewer";
import { useSetupScene, type FaceHighlight } from "../composables/useSetupScene";
import { useResultsOverlay } from "../composables/useResultsOverlay";
import { useLivePreview } from "../composables/useLivePreview";
import { isDark, toggleTheme } from "../composables/useTheme";
import { newId, partPattern, resolvePart, ruleFor, sigOf } from "../utils/setup";
import { drawMap } from "../utils/drawMap";
import { exitFrame } from "../utils/analysisText";
import { fmtAgo } from "../utils/format";
import PartsTab from "../components/PartsTab.vue";
import LedsTab from "../components/LedsTab.vue";
import DetectorTab from "../components/DetectorTab.vue";
import GroupsTab from "../components/GroupsTab.vue";
import AnalysisDock from "../components/AnalysisDock.vue";
import StatesTab from "../components/StatesTab.vue";
import RunTab from "../components/RunTab.vue";
import ResultsTab from "../components/ResultsTab.vue";
import LivePanel, { type PhotorealView } from "../components/LivePanel.vue";
import LiveViewOverlay from "../components/LiveViewOverlay.vue";
import ResultsView from "../components/ResultsView.vue";
import LibraryDialog from "../components/LibraryDialog.vue";
import ContextMenu, { type MenuItem } from "../components/ContextMenu.vue";
import ModelTreeOverlay from "../components/viewer/ModelTreeOverlay.vue";
import SectionPanel from "../components/viewer/SectionPanel.vue";
import HotkeyOverlay from "../components/viewer/HotkeyOverlay.vue";

const props = defineProps<{ id: string }>();
const router = useRouter();

// ── workspace shared with the panels ──────────────────────────────────────────
const ws = createWorkspace(computed(() => props.id) as never);
provideWorkspace(ws);
ws.start();

const route = useRoute();
const variants = computed(() => ws.project.value?.variants ?? []);
/** Revisions of the current variant, newest first. */
const revisions = computed(() => (ws.project.value?.revisionList ?? []).filter((r) => !ws.variantId.value || r.variantId === ws.variantId.value));
function goRevision(rid: string) {
  if (rid !== props.id) router.push(`/r/${rid}`);
}
function goVariant(vid: string) {
  const latest = variants.value.find((v) => v.id === vid)?.latest;
  if (latest && latest.id !== props.id) router.push(`/r/${latest.id}`);
}
async function renameVariant() {
  const v = ws.variant.value;
  const pid = ws.meta.value?.projectId;
  if (!v || !pid) return;
  const name = window.prompt("Rename variant", v.name);
  if (!name?.trim()) return;
  try {
    await api.renameVariant(pid, v.id, name.trim());
    await ws.loadProject();
  } catch (e) {
    alert((e as Error).message);
  }
}
async function deleteVariant() {
  const v = ws.variant.value;
  const pid = ws.meta.value?.projectId;
  if (!v || !pid || variants.value.length < 2) return;
  if (!confirm(`Delete the variant "${v.name}" with its ${v.revisions.length} revision(s), its setup and every simulation run?`)) return;
  try {
    const project = await api.deleteVariant(pid, v.id);
    const next = project.variants[0]?.latest ?? project.latest;
    router.push(next ? `/r/${next.id}` : "/");
  } catch (e) {
    alert((e as Error).message);
  }
}

// A new variant is a design alternative: another STEP file that starts from a copy of this variant's setup.
const variantFile = ref<HTMLInputElement | null>(null);
const variantUploading = ref(false);
async function onVariantFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  const pid = ws.meta.value?.projectId;
  if (!file || !pid) return;
  const suggested = file.name.replace(/\.[^.]+$/, "");
  const name = window.prompt("Name of the new variant", suggested);
  if (name === null) return;
  variantUploading.value = true;
  try {
    const res = await api.upload(file, { projectId: pid, variantId: "new", variantName: name.trim() || suggested, copySetupFrom: ws.variantId.value });
    router.push({ path: `/r/${res.id}`, query: { adapt: "1" } }); // the copied setup is adapted to the new geometry once it is imported
  } catch (err) {
    alert((err as Error).message);
  } finally {
    variantUploading.value = false;
  }
}
async function onAdapt() {
  try {
    await ws.reconcile();
  } catch (e) {
    alert((e as Error).message);
  }
}
// arriving from "New variant": adapt the copied setup to the geometry as soon as it can be read
let adapted = false;
watch(
  () => ws.ready.value && ws.setup.value !== null,
  async (ok) => {
    if (!ok || adapted || route.query.adapt !== "1") return;
    adapted = true;
    router.replace({ path: route.path });
    await onAdapt();
  },
  { immediate: true },
);
const saveText = computed(() => ({ idle: "", saving: "saving…", saved: "saved", error: `not saved: ${ws.saveError.value}` })[ws.saveState.value]);

// ── import progress ───────────────────────────────────────────────────────────
const phaseTitle = computed(
  () => ({ queued: "Waiting to start…", import: "Reading the STEP file…", mesh: "Building the 3D and optical models…", done: "Done" })[ws.meta.value?.progress.stage ?? "queued"] ?? "Working…",
);
const overallPercent = computed(() => Math.round((ws.meta.value?.progress.fraction ?? 0) * 100));
async function cancelJob() {
  await api.cancelImport(props.id);
  await ws.refreshMeta();
}
async function reimport() {
  await api.reimport(props.id);
  await ws.refreshMeta();
  ws.schedulePoll();
}

// ── tabs ────────────────────────────────────────────────────────────────────
type TabId = "parts" | "leds" | "detector" | "groups" | "states" | "runs" | "results";
const tab = ref<TabId>("parts");
const tabs = computed<{ id: TabId; label: string; icon: Component; count?: number }[]>(() => [
  { id: "parts", label: "Parts", icon: Boxes },
  { id: "leds", label: "LEDs", icon: Lightbulb, count: ws.setup.value?.leds.length },
  { id: "detector", label: "Exit", icon: Crosshair, count: ws.setup.value?.detectors.length },
  { id: "groups", label: "Groups", icon: Shapes, count: ws.setup.value?.analysisGroups?.length || undefined },
  { id: "states", label: "States", icon: SlidersHorizontal, count: ws.setup.value?.states.length },
  { id: "runs", label: "Run", icon: Play },
  { id: "results", label: "Results", icon: BarChart3, count: ws.runs.value.length || undefined },
]);

const libraryOpen = ref(false);
async function onSuggest() {
  if (ws.setup.value && (ws.setup.value.leds.length || ws.setup.value.detectors.length) && !confirm("Replace the current setup with a suggestion made from the CAD?")) return;
  try {
    await ws.suggest();
  } catch (e) {
    alert((e as Error).message);
  }
}

// ── 3D viewer ───────────────────────────────────────────────────────────────
const canvasEl = ref<HTMLCanvasElement | null>(null);
const meshUrl = ref("");
const viewer = useThreeViewer(canvasEl, { meshUrl, metadataUrl: ref("") });
(window as unknown as { __viewer?: unknown }).__viewer = viewer;
watch(
  () => [ws.meta.value?.model_ready, ws.meta.value?.finished ?? ws.meta.value?.started ?? 0] as const,
  ([ready, version]) => {
    if (ready && !meshUrl.value) meshUrl.value = `${api.fileUrl(props.id, "model.glb")}?v=${version}`;
  },
  { immediate: true },
);

const dimOthers = ref(false);
const allTransparent = ref(false);
const showLeds = ref(true);

// What is selected in the viewport, as part ids (an assembly stands for all the parts inside it).
const partIds = computed(() => new Set(ws.parts.value.map((p) => p.id)));
function partIdsUnder(nodeIds: number[]): string[] {
  const out = new Set<string>();
  for (const nid of nodeIds) {
    viewer.scene.value?.getObjectById(nid)?.traverse((c) => {
      const id = c.userData?.cadreview_instanceId as string | undefined;
      if (id && partIds.value.has(id)) out.add(id);
    });
  }
  return [...out];
}
const selectedIds = computed(() => new Set(partIdsUnder(viewer.selectedNodeIds.value)));
// ── light path analysis (of the run opened in the results) ──────────────────────────────
const activeRun = ref<string | null>(null);
const current = shallowRef<{ result: StateResult; vmax: number; detector: number } | null>(null);
const runScene = shallowRef<RunScene | null>(null); // the geometry the opened run was simulated with
const av = useAnalysisView(
  activeRun,
  computed(() => current.value?.result.state.id),
  computed(() => (current.value?.result.detectors[current.value.detector]?.quantity === "exitance" ? "all" : "cone")),
  computed(() => (ws.setup.value?.leds ?? []).map((l) => l.id)),
  // the frame of the opened run's own exit surface (its setup may differ from the current one, e.g. a flipped map), else the current setup's
  computed(() => {
    const di = current.value?.detector ?? 0;
    const id = current.value?.result.detectors[di]?.id;
    const own = id ? runScene.value?.detectors.find((d) => d.id === id) : undefined;
    return exitFrame(own ?? ws.check.value?.detectors[di]);
  }),
  computed(() => new Map(ws.parts.value.map((p) => [p.id, p.faceCount]))),
);
provideAnalysisView(av);
// the results pane asks for the 3D view (to show surfaces or a footprint on the model)
watch(
  () => av.viewRequest.value,
  () => {
    resultsShown.value = false;
    dockOpen.value = true;
  },
);
const dockOpen = ref(false);
const dockShown = computed(() => !!activeRun.value && !resultsShown.value && dockOpen.value);
const dockCtx = computed<AnalysisCtx | null>(() => {
  const c = current.value;
  const d = c?.result.detectors[c.detector];
  if (!c || !d || !activeRun.value) return null;
  return {
    runId: activeRun.value,
    state: c.result.state.id,
    view: d.quantity === "exitance" ? "all" : "cone",
    detector: c.detector,
    uMm: d.uExtentMm,
    vMm: d.vExtentMm,
    valid: d.valid,
    values: d.map ?? d[d.quantity === "luminance" ? "luminance" : "exitance"],
    roi: null,
    vmax: c.vmax,
    unit: d.unit,
  };
});
function toggleDock() {
  if (dockShown.value) dockOpen.value = false;
  else {
    dockOpen.value = true;
    resultsShown.value = false;
  }
}
// the layers of the analysis only work while the 3D view is on screen; clicking a face inspects it while the panel is open
watch(() => !!activeRun.value && !resultsShown.value, (v) => (av.viewActive.value = v), { immediate: true });
watch(dockShown, (v) => (av.inspecting.value = v), { immediate: true });
/** Faces painted by the light path analysis (inspected face, surface hit map). */
const analysisHighlights = computed<FaceHighlight[]>(() => av.highlights.value);
const allHighlights = computed(() => [...ws.highlights.value, ...analysisHighlights.value]);
const sceneCtl = useSetupScene(viewer, {
  parts: ws.parts,
  setup: ws.setup,
  library: ws.library,
  selectedIds,
  highlights: allHighlights,
  dimOthers,
  allTransparent,
});
const overlay = useResultsOverlay(viewer);

// ── live physics and the photoreal view ──────────────────────────────────────────────────
const live = useLivePreview(ws);
const livePanelOpen = ref(false);
const view = reactive<PhotorealView>({ on: false, exposure: 0, ambient: 0.15, whiteBalance: false, mode: "map", resolution: 0, status: null });
const liveKey = computed(() => `${live.run.value?.live?.rays ?? 0}:${live.run.value?.status ?? ""}`);
watch(
  () => live.enabled.value,
  (on) => {
    if (on) livePanelOpen.value = true;
    else {
      view.on = false;
      view.status = null;
    }
  },
);
watch(() => view.on, (on) => !on && (view.status = null));

// ── one selection: the viewport and the Parts tab follow each other ───────────────────
/** The part (not assembly) that is selected last in the viewport. */
function selectedPartOfViewer(): string | null {
  const ids = viewer.selectedNodeIds.value;
  if (!ids.length) return null;
  const id = viewer.scene.value?.getObjectById(ids[ids.length - 1])?.userData?.cadreview_instanceId as string | undefined;
  return id && partIds.value.has(id) ? id : null;
}
let selectionFromTree = false;
watch(
  () => viewer.selectedNodeIds.value,
  () => {
    const id = selectedPartOfViewer();
    const fromTree = selectionFromTree;
    selectionFromTree = false;
    if (ws.selectedPart.value === id) return;
    ws.selectedPart.value = id;
    // picking a part in the 3D view brings up its properties (a click in the tree leaves the tab alone)
    if (id && !fromTree && tab.value !== "parts" && tab.value !== "runs" && tab.value !== "results") tab.value = "parts";
  },
);
watch(
  () => ws.selectedPart.value,
  (id) => {
    if (id === selectedPartOfViewer()) return;
    const node = id ? viewer.findNodeByInstanceId(id) : null;
    if (node) viewer.selectNode(node.id);
    else viewer.clearSelection();
  },
);
// While faces are being picked, a click must not change the part selection.
watch(
  () => !!ws.pick.value || av.inspecting.value,
  (picking) => (picking ? viewer.interaction.suppress("partSelect") : viewer.interaction.unsuppress("partSelect")),
  { immediate: true },
);

// ── section planes ────────────────────────────────────────────────────────────────
const PLANE_KEY = "lightsim.planeHandle";
const sectionPanelOpen = ref(false);
const flip = reactive({ x: false, y: false, z: false });
/** The plane handle is what you drag to move the cut; it can be hidden to see the model undisturbed. */
const planeVisible = ref(localStorage.getItem(PLANE_KEY) !== "0");
viewer.hideCuttingPlanes(!planeVisible.value);
function setPlaneVisible(v: boolean) {
  planeVisible.value = v;
  localStorage.setItem(PLANE_KEY, v ? "1" : "0");
  viewer.hideCuttingPlanes(!v);
}
/** Cap the cut faces (default) or leave the cut open to look into the parts. Remembered between sessions. */
const CAPS_KEY = "lightsim.sectionCaps";
function setCaps(v: boolean) {
  localStorage.setItem(CAPS_KEY, v ? "1" : "0");
  viewer.setShowSectionCaps(v);
}
viewer.setShowSectionCaps(localStorage.getItem(CAPS_KEY) !== "0");
const activePlanes = computed(() => new Set(viewer.activeCuttingPlanes.value));
function togglePlane(axis: "x" | "y" | "z", fromCheckbox = false) {
  const isActive = viewer.getActivePlanes().includes(axis);
  if (fromCheckbox && isActive) {
    viewer.updateSectionFlip(axis, flip[axis]); // the Flip tick box of a plane that is already on
    return;
  }
  const fn = { x: viewer.toggleCuttingPlaneX, y: viewer.toggleCuttingPlaneY, z: viewer.toggleCuttingPlaneZ }[axis];
  if (fn(flip[axis], isActive)) viewer.hideCuttingPlanes(!planeVisible.value);
}
/** Light at the cut: the flux through the plane and the ray fan (needs an opened run). */
const sectionLight = useSectionLight(
  activeRun,
  computed(() => current.value?.result.state.id),
  viewer,
  overlay,
  av,
  flip,
  computed(() => ws.partsDoc.value?.diagonal ?? 50),
);

// ── model tree and showing / hiding parts ─────────────────────────────────────────────
const treeOpen = ref(false);
const treeData = ref<TreeNode | null>(null);
const resultsCover = computed(() => !!activeRun.value && resultsShown.value);
const treeShown = computed(() => treeOpen.value && !resultsCover.value);
function refreshTree() {
  if (treeOpen.value) treeData.value = viewer.getModelTree(treeData.value);
}
function toggleTree() {
  treeOpen.value = !treeOpen.value;
  refreshTree();
}
// every repaint can change what is shown: keep the eye icons honest
watch(() => sceneCtl.paintVersion.value, () => nextTick(refreshTree));
watch(() => viewer.modelLoaded.value, (v) => v && nextTick(refreshTree));

function findTreeNode(node: TreeNode | null, id: number): TreeNode | null {
  if (!node) return null;
  if (node.nodeId === id) return node;
  for (const c of node.children) {
    const f = findTreeNode(c, id);
    if (f) return f;
  }
  return null;
}
function onTreeClick(nodeId: number) {
  selectionFromTree = true;
  viewer.selectNode(nodeId);
}
function onTreeVisibility(nodeId: number) {
  const node = findTreeNode(treeData.value, nodeId);
  sceneCtl.setNodesVisible([nodeId], node ? node.visibilityState !== "visible" : true);
}
const selectedNodes = () => [...viewer.selectedNodeIds.value];
function hideSelection() {
  const ids = selectedNodes();
  if (!ids.length) return;
  sceneCtl.setNodesVisible(ids, false);
  viewer.clearSelection();
}
function showSelection() {
  const ids = selectedNodes();
  if (ids.length) sceneCtl.setNodesVisible(ids, true);
}
/** Hide everything except the selected part(s). */
function isolateSelection() {
  const keep = new Set(sceneCtl.leafIdsUnder(selectedNodes()));
  if (keep.size) sceneCtl.isolate(keep);
}
const showAll = () => sceneCtl.showAll();

// ── right-click menu (viewport and tree) ───────────────────────────────────────────────
const menu = reactive({ visible: false, x: 0, y: 0 });
const hasSelection = computed(() => viewer.selectedNodeIds.value.length > 0);
const menuItems = computed<MenuItem[]>(() => [
  { id: "hide", label: "Hide", icon: EyeOff, shortcut: "H", disabled: !hasSelection.value },
  { id: "show", label: "Show", icon: Eye, disabled: !hasSelection.value },
  { id: "isolate", label: "Hide everything else", icon: Focus, shortcut: "I", disabled: !hasSelection.value },
  { id: "showAll", label: "Show all", icon: RotateCcw, shortcut: "Alt+H", disabled: !sceneCtl.anyHidden.value, separator: true },
  { id: "deselect", label: "Deselect", icon: X, shortcut: "Esc", disabled: !hasSelection.value },
  { id: "fit", label: "Fit whole model", icon: Maximize, shortcut: "Home", separator: true },
  { id: "noCut", label: "Clear section cuts", icon: Scissors, disabled: activePlanes.value.size === 0 },
]);
function openMenu(x: number, y: number) {
  menu.x = x;
  menu.y = y;
  menu.visible = true;
}
function isInsideSelection(node: THREE.Object3D): boolean {
  const selected = new Set(viewer.selectedNodeIds.value);
  for (let cur: THREE.Object3D | null = node; cur; cur = cur.parent) if (selected.has(cur.id)) return true;
  return false;
}
function onCanvasContextMenu(e: MouseEvent) {
  // a right-drag pans the view; only a plain right-click opens the menu
  if (viewer.rightClickWasDrag.value) {
    viewer.rightClickWasDrag.value = false;
    return;
  }
  const hit = viewer.raycastFromCursor(e)[0];
  const node = hit ? viewer.findParentInstanceNode(hit.object) : null;
  if (node && !isInsideSelection(node)) viewer.selectNode(node.id);
  else if (!hit) viewer.clearSelection(); // right-clicking empty space: the menu is about nothing
  openMenu(e.clientX, e.clientY);
}
function onTreeContextMenu(nodeId: number, x: number, y: number) {
  if (!viewer.selectedNodeIds.value.includes(nodeId)) onTreeClick(nodeId);
  openMenu(x, y);
}
function onMenuPick(id: string) {
  switch (id) {
    case "hide": return hideSelection();
    case "show": return showSelection();
    case "isolate": return isolateSelection();
    case "showAll": return showAll();
    case "deselect": return viewer.clearSelection();
    case "fit": return viewer.resetView();
    case "noCut": return viewer.resetCuttingPlanes();
  }
}

// ── keyboard shortcuts ────────────────────────────────────────────────────────────────
const showHotkeys = ref(false);
const unregister: Array<() => void> = [];
function hk(id: string, keys: string, label: string, action: () => void) {
  unregister.push(viewer.hotkeys.register({ id, keys, label, category: "View", action, when: () => viewer.modelLoaded.value }));
}

// ── picking faces ─────────────────────────────────────────────────────────────────────
const hoverText = ref("");
const pickHint = computed(() => {
  const p = ws.pick.value;
  if (!p) return "";
  if (p.kind === "led") return p.ledId === "__new__" ? "Click the emitting face of each LED." : "Click the emitting face of this LED.";
  if (p.kind === "detector") return "Click the faces you look at (click again to remove). Rays leaving through them are measured.";
  if (p.kind === "override") return "Click faces of the highlighted part to give them their own finish.";
  if (p.kind === "group") return "Click the faces that make up this surface (click again to remove).";
  return "";
});

/** The pill at the top of the 3D view while faces are being picked: what is picked for, and what a click does. */
const pickInfo = computed(() => {
  const p = ws.pick.value;
  const s = ws.setup.value;
  if (!p) return null;
  const toggle = "Click a face to add it, click it again to remove it";
  if (p.kind === "group") {
    const g = s?.analysisGroups?.find((x) => x.id === p.groupId);
    const color = g?.color || ws.check.value?.groups?.find((x) => x.id === p.groupId)?.color || "#8b5cf6";
    return { title: `Picking faces for ${g?.name || "this group"}`, sub: toggle, color };
  }
  if (p.kind === "detector") {
    const d = s?.detectors.find((x) => x.id === p.detectorId);
    return { title: `Picking the exit faces of ${d?.name || "this detector"}`, sub: `${toggle}. Rays leaving through them are measured`, color: "#06b6d4" };
  }
  if (p.kind === "led") return { title: p.ledId === "__new__" ? "Picking the emitting face of each LED" : "Picking the emitting face of this LED", sub: "Click the face the LED shines out of", color: "#f97316" };
  if (p.kind === "override") {
    const inst = ws.parts.value.find((x) => x.id === p.instanceId);
    return { title: `Picking faces of ${inst?.name || "the part"} with their own finish`, sub: toggle, color: "#a855f7" };
  }
  return { title: "Picking faces", sub: pickHint.value, color: "#8b5cf6" };
});
/** What the last click did (shown for a moment in the pill) and what the next one would do. */
const pickNote = ref<{ text: string; tone: "add" | "remove" | "warn" } | null>(null);
let pickNoteTimer: number | undefined;
function flashPick(text: string, tone: "add" | "remove" | "warn") {
  pickNote.value = { text, tone };
  window.clearTimeout(pickNoteTimer);
  pickNoteTimer = window.setTimeout(() => (pickNote.value = null), tone === "warn" ? 3500 : 1600);
}
/** `add` / `remove` when the face under the cursor would be added to / removed from the list being picked into. */
const hoverAction = ref<"add" | "remove" | "">("");
const pickLive = computed(() => {
  if (pickNote.value) return pickNote.value;
  if (!hoverText.value) return { text: "Hover a face to see what a click does", tone: "" as const };
  const verb = hoverAction.value === "add" ? " · click to add" : hoverAction.value === "remove" ? " · click to remove" : "";
  return { text: hoverText.value + verb, tone: hoverAction.value };
});
function stopPick() {
  ws.pick.value = null;
}
/** The face list (group or detector) that faces are being picked into, or null for the other kinds of picking. */
function pickList(s: NonNullable<typeof ws.setup.value>, pick: NonNullable<typeof ws.pick.value>) {
  if (pick.kind === "group") return s.analysisGroups?.find((g) => g.id === pick.groupId)?.faces ?? null;
  if (pick.kind === "detector") return s.detectors.find((d) => d.id === pick.detectorId)?.faces ?? null;
  return null;
}
/** Add the clicked face to the group / detector (or take it out when it is already there). The list changes at once; the signature is stored a moment later. */
function togglePickedFace(pick: NonNullable<typeof ws.pick.value>, instId: string, face: number) {
  const s = ws.setup.value;
  const inst = ws.parts.value.find((p) => p.id === instId);
  if (!s || !inst) return;
  const label = `face ${face} · ${inst.name}`;
  if (!resolvePart(inst, s.parts).enabled) {
    flashPick(`${inst.name} is ignored by the simulation. Enable it in the Parts tab first.`, "warn");
    return;
  }
  const pattern = partPattern(inst, ws.parts.value);
  let result = null as "added" | "removed" | null;
  ws.mutate((st) => {
    const list = pickList(st, pick);
    if (!list) return;
    const at = list.findIndex((f) => f.part === pattern && f.face === face);
    if (at >= 0) {
      list.splice(at, 1);
      result = "removed";
    } else {
      list.push({ part: pattern, face, sig: null });
      result = "added";
    }
  });
  if (!result) return;
  flashPick(result === "added" ? `Added ${label}` : `Removed ${label}`, result === "added" ? "add" : "remove");
  applyHover(instId, face); // the cursor is still on that face: it now offers the opposite action
  if (result === "added") {
    api
      .face(props.id, instId, face)
      .then((info) =>
        ws.mutate((st) => {
          const entry = pickList(st, pick)?.find((f) => f.part === pattern && f.face === face);
          if (entry && !entry.sig) entry.sig = sigOf(info);
        }),
      )
      .catch(() => undefined); // without a signature the face index is trusted as it is
  }
}

/**
 * Plain clicks (select a part, click empty space to deselect) are handled by the viewer; this only handles the
 * clicks that pick faces. The viewer marks a click as consumed after an orbit / pan drag or a drag of the
 * cutting-plane handle, so those never pick anything.
 */
async function onClick(e: MouseEvent) {
  const setup = ws.setup.value;
  const pick = ws.pick.value;
  // A plain click on empty space clears the selection, whatever made it (the viewport, the Parts tab or the tree). The viewer does this
  // for its own selection; the Parts tab can hold a selection the viewer does not have (e.g. a hidden part), so it is cleared here too.
  if (!pick && !av.inspecting.value && viewer.modelLoaded.value && !viewer.interaction.isClickConsumed() && !e.ctrlKey && !e.metaKey && e.button === 0) {
    if ((ws.selectedPart.value || viewer.selectedNodeIds.value.length) && viewer.raycastFromCursor(e).length === 0) {
      viewer.clearSelection();
      ws.selectedPart.value = null;
    }
  }
  if (!pick && av.inspecting.value && activeRun.value && viewer.modelLoaded.value && !viewer.interaction.isClickConsumed()) {
    // the surface inspector of the light path analysis
    const face = sceneCtl.pickFace(e);
    if (face) {
      dockOpen.value = true;
      await av.inspectFace(face.instanceId, face.face);
    }
    return;
  }
  if (!pick || !setup || !viewer.modelLoaded.value || viewer.interaction.isClickConsumed()) return;
  const hit = sceneCtl.pickFace(e);
  if (!hit) return;
  const inst = ws.parts.value.find((p) => p.id === hit.instanceId);
  if (!inst) return;
  // group and detector faces: the list changes (and the face is painted) right away, without waiting for the server
  if (pick.kind === "group" || pick.kind === "detector") {
    togglePickedFace(pick, hit.instanceId, hit.face);
    return;
  }
  const pattern = partPattern(inst, ws.parts.value);
  let sig;
  try {
    sig = sigOf(await api.face(props.id, hit.instanceId, hit.face));
  } catch {
    sig = null;
  }

  if (pick.kind === "led") {
    ws.mutate((s) => {
      const placement = { type: "face" as const, part: pattern, face: hit.face, sig, flip: false };
      if (pick.ledId === "__new__") {
        const lid = newId("led", s.leds.map((l) => l.id));
        s.leds.push({ id: lid, name: `LED ${s.leds.length + 1}`, model: ws.newLedModel.value, enabled: true, scale: 1, placement });
      } else {
        const led = s.leds.find((l) => l.id === pick.ledId);
        if (led) led.placement = { ...placement, flip: led.placement.flip };
      }
    });
    if (pick.ledId !== "__new__") ws.pick.value = null;
  } else if (pick.kind === "override") {
    if (hit.instanceId !== pick.instanceId) {
      ws.selectedPart.value = hit.instanceId;
      return;
    }
    ws.mutate((s) => {
      const rule = ruleFor(s, inst, ws.parts.value);
      const key = String(hit.face);
      const faces = { ...(rule.faces ?? {}) };
      if (faces[key] === pick.finish) {
        delete faces[key];
        if (rule.face_sigs) delete rule.face_sigs[key];
      } else {
        faces[key] = pick.finish;
        if (sig) rule.face_sigs = { ...(rule.face_sigs ?? {}), [key]: sig };
      }
      rule.faces = faces;
    });
  }
}

let hoverSeq = 0;
function onPointerMove(e: PointerEvent) {
  const pick = ws.pick.value ?? (av.inspecting.value && activeRun.value ? ({ kind: "inspect" } as const) : null);
  // no surface highlight while a button is held (orbit, pan, dragging a handle); a pending highlight is dropped too
  if (e.buttons !== 0) {
    hoverSeq++;
    if (sceneCtl.hover.value) sceneCtl.setHover(null);
    hoverText.value = "";
    hoverAction.value = "";
    return;
  }
  if (!pick || !viewer.modelLoaded.value) {
    sceneCtl.setHover(null);
    hoverText.value = "";
    hoverAction.value = "";
    return;
  }
  const seq = ++hoverSeq;
  window.requestAnimationFrame(() => {
    if (seq !== hoverSeq) return;
    const hit = sceneCtl.pickFace(e);
    if (!hit) {
      sceneCtl.setHover(null);
      hoverText.value = "";
      hoverAction.value = "";
      return;
    }
    applyHover(hit.instanceId, hit.face);
  });
}
/** Tint the face under the cursor: the pick colour to add it, red when a click would take it out of the group / detector. */
function applyHover(instanceId: string, face: number) {
  const pick = ws.pick.value ?? (av.inspecting.value && activeRun.value ? ({ kind: "inspect" } as const) : null);
  if (!pick) return;
  const inst = ws.parts.value.find((p) => p.id === instanceId);
  let color = pick.kind === "led" ? 0xf97316 : pick.kind === "detector" || pick.kind === "inspect" ? 0x06b6d4 : pick.kind === "group" ? hexToNumber(ws.setup.value?.analysisGroups?.find((g) => g.id === pick.groupId)?.color) : 0xa855f7;
  let action: "add" | "remove" | "" = "";
  const s = ws.setup.value;
  if (inst && s && (pick.kind === "group" || pick.kind === "detector")) {
    const pattern = partPattern(inst, ws.parts.value);
    const inList = !!pickList(s, pick)?.some((f) => f.part === pattern && f.face === face);
    action = inList ? "remove" : "add";
    if (inList) color = 0xef4444;
  }
  hoverAction.value = action;
  sceneCtl.setHover({ key: "hover", instanceId, face, color, opacity: 0.6 });
  hoverText.value = `${inst?.name ?? instanceId} · face ${face}`;
}

function onKey(e: KeyboardEvent) {
  if (e.key !== "Escape" || menu.visible) return;
  if (ws.pick.value) {
    ws.pick.value = null;
    sceneCtl.setHover(null);
    hoverText.value = "";
  } else if (av.inspected.value) {
    av.clearInspection();
  } else if (!["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement | null)?.tagName ?? "")) {
    viewer.clearSelection();
  }
}
watch(
  () => ws.pick.value,
  (p) => {
    if (!p) {
      sceneCtl.setHover(null);
      hoverText.value = "";
      hoverAction.value = "";
      pickNote.value = null;
    }
    if (p?.kind === "override") ws.selectedPart.value = p.instanceId;
  },
);

// ── results ─────────────────────────────────────────────────────────────────────
const resultsShown = ref(true);
/** The exit map is drawn on the model unless the map layer is switched off. */
const drape = computed(() => av.layers.map !== "off");
const pendingRun = ref<string | null>(null);
const activeRunName = computed(() => ws.runs.value.find((r) => r.id === activeRun.value)?.name ?? "Results");

function openRun(rid: string) {
  activeRun.value = rid;
  resultsShown.value = true;
}
function onStarted(rid: string) {
  pendingRun.value = rid;
  tab.value = "results"; // the new run shows its progress in the list of runs, and opens when it is done
}
watch(
  () => ws.runs.value.map((r) => `${r.id}:${r.status}`).join("|"),
  () => {
    const rid = pendingRun.value;
    if (!rid) return;
    const run = ws.runs.value.find((r) => r.id === rid);
    if (run?.status === "ready") {
      pendingRun.value = null;
      openRun(rid);
    } else if (run && (run.status === "failed" || run.status === "cancelled")) pendingRun.value = null;
  },
);
function closeResults() {
  activeRun.value = null;
  current.value = null;
  runScene.value = null;
  refreshOverlay();
}
watch(activeRun, async (rid) => {
  runScene.value = null;
  current.value = null;
  if (rid) {
    try {
      runScene.value = await api.runScene(rid);
    } catch {
      /* the results panel shows its own error */
    }
  }
  refreshOverlay();
});
function onResultsLoaded(result: StateResult, detector: number, vmax: number) {
  current.value = { result, vmax, detector };
  av.mapDetector.value = detector;
  av.mapVmax.value = vmax;
  refreshOverlay();
}

const LED_COLORS = [0xf97316, 0x22c55e, 0x3b82f6, 0xe11d48, 0xa855f7, 0x14b8a6, 0xeab308];
function refreshOverlay() {
  overlay.clear();
  const diag = ws.partsDoc.value?.diagonal ?? 50;
  if (showLeds.value && ws.check.value) {
    const seen = new Set<string>();
    const items: { origin: number[]; axis: number[]; color: number; ex?: number[]; ey?: number[]; size?: number[] }[] = [];
    ws.check.value.channels.forEach((c) => {
      if (seen.has(c.led)) return;
      seen.add(c.led);
      items.push({ origin: c.origin, axis: c.axis, ex: c.ex, ey: c.ey, size: c.size, color: LED_COLORS[(seen.size - 1) % LED_COLORS.length] });
    });
    overlay.setLedMarkers(items, diag);
  }
  // the live simulation draws its map on the model; otherwise the opened run does
  const liveOn = live.enabled.value && !!live.result.value && !!live.scene.value;
  const curResult = liveOn ? live.result.value : (current.value?.result ?? null);
  const sc = liveOn ? live.scene.value : runScene.value;
  if (drape.value && curResult && sc && (liveOn || activeRun.value)) {
    const maps: Parameters<typeof overlay.setMaps>[0] = [];
    const part = !liveOn ? av.modelMap.value : null; // a footprint from the analysis replaces the exit map of its detector
    curResult.detectors.forEach((d, di) => {
      const s = sc.detectors.find((x) => x.id === d.id);
      if (!s) return;
      const canvas = document.createElement("canvas");
      if (part && part.detector === di) drawMap(canvas, part.values, null, 0, part.vmax, { flipV: false });
      else {
        const vmax = (d.stats.max ?? 1) * 1.02 || 1;
        drawMap(canvas, d.map ?? d[d.quantity === "luminance" ? "luminance" : "exitance"], d.valid, 0, vmax, { flipV: false });
      }
      maps.push({ corners: s.corners, normal: s.n, canvas, mesh: s.mesh });
    });
    overlay.setMaps(maps, Math.max(diag * 0.0006, 0.01));
  }
  // the orientation of the exit map (its axes) is only drawn while the Exit tab is open
  // (they follow the map that is drawn on the model: the opened run's own geometry when its map is shown, else the current setup)
  if (tab.value === "detector") {
    const shown = drape.value && !liveOn && activeRun.value && runScene.value ? runScene.value.detectors : null;
    const dets = shown ?? ws.check.value?.detectors ?? [];
    if (dets.length) overlay.setMapAxes(dets.map((d) => ({ corners: d.corners, normal: d.n })));
  }
  refreshRays();
}
/** The replayed rays of the light path analysis; they are redrawn alone when their style changes. */
function refreshRays() {
  if (av.rays.value && activeRun.value) overlay.setRays(av.rays.value.paths, av.rayStyle.value);
  else overlay.clearRays();
}
watch([() => av.rays.value, () => av.rayStyle.value], refreshRays);
watch(() => av.modelMap.value, refreshOverlay);
// does the opened run's exit map lie differently (flipped) than the current setup's? Then its results are of the previous orientation.
watch(
  [runScene, () => ws.check.value, activeRun],
  () => {
    const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const edges = (c: number[][]) => [c[1].map((x, i) => x - c[0][i]), c[3].map((x, i) => x - c[0][i])];
    ws.runOrientationStale.value =
      !!activeRun.value &&
      (ws.check.value?.detectors ?? []).some((d) => {
        const own = runScene.value?.detectors.find((x) => x.id === d.id);
        if (!own || own.corners.length < 4 || d.corners.length < 4) return false;
        const [cu, cv] = edges(d.corners);
        const [ou, ov] = edges(own.corners);
        return dot(cu, ou) < 0 || dot(cv, ov) < 0;
      });
  },
  { immediate: true },
);
watch([drape, showLeds, tab, () => ws.check.value, () => live.result.value, () => live.scene.value, () => live.enabled.value], refreshOverlay);
watch(() => viewer.modelLoaded.value, (v) => v && refreshOverlay());

onMounted(() => {
  const el = canvasEl.value;
  el?.addEventListener("click", onClick);
  el?.addEventListener("pointermove", onPointerMove);
  window.addEventListener("keydown", onKey);

  hk("vw-hide", "h", "Hide the selected part(s)", hideSelection);
  hk("vw-isolate", "i", "Hide everything but the selected part(s)", isolateSelection);
  hk("vw-showall", "alt+h", "Show all parts", showAll);
  hk("vw-tree", "t", "Model tree", toggleTree);
  hk("vw-plane", "p", "Show / hide the cutting-plane handle", () => setPlaneVisible(!planeVisible.value));
  hk("vw-fit", "home", "Fit whole model", () => viewer.resetView());
  unregister.push(viewer.hotkeys.register({ id: "vw-help", keys: "shift+?", label: "Toggle this help", category: "View", action: () => (showHotkeys.value = !showHotkeys.value) }));
});
onBeforeUnmount(() => {
  const el = canvasEl.value;
  el?.removeEventListener("click", onClick);
  el?.removeEventListener("pointermove", onPointerMove);
  window.removeEventListener("keydown", onKey);
  unregister.forEach((u) => u());
  viewer.interaction.unsuppress("partSelect");
});
</script>
