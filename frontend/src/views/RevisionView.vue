<template>
  <div class="checker refract">
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
      <DesignSwitcher
        v-if="variants.length > 0"
        :designs="designs"
        :variants="variants"
        :current-revision-id="id"
        @open="goRevision"
        @new-variant="newVariantInDesign"
        @duplicate="duplicateVariant"
        @rename-design="renameDesign"
        @delete-design="deleteDesign"
        @move-variant="moveVariant"
      />
      <RevisionPicker :revisions="variantRevisions" :current-revision-id="id" @open="goRevision" />
      <button class="tbtn icon" :disabled="!ws.variant.value" title="Rename this variant" aria-label="Rename this variant" @click="renameVariant"><Pencil :size="15" /></button>
      <button class="tbtn icon" :disabled="!canDeleteVariant" :title="canDeleteVariant ? 'Delete this variant with its revisions and runs' : 'The last variant of a design cannot be deleted; delete the design instead'" aria-label="Delete this variant" @click="deleteVariant"><Trash2 :size="15" /></button>
      <button class="tbtn" :disabled="!ws.ready.value || variantUploading" title="New variant in this design: upload another STEP file (for example another diffuser thickness); it starts with a copy of this variant's setup" @click="newVariantInDesign(ws.variant.value?.designId ?? '')">
        <Plus :size="16" /><span class="tlabel">New variant</span>
      </button>
      <button class="tbtn" :disabled="!ws.ready.value || variantUploading" title="Duplicate this variant: same geometry and a copy of its setup, no upload (try other materials or LEDs next to this one)" @click="duplicateVariant(id)">
        <Copy :size="16" /><span class="tlabel">Duplicate</span>
      </button>
      <button class="tbtn" :disabled="!ws.ready.value || variantUploading" title="New design (for example a new reflector): upload its STEP file; its first variant starts with a copy of this variant's setup" @click="newDesign">
        <Layers :size="16" /><span class="tlabel">New design</span>
      </button>
      <input ref="variantFile" type="file" accept=".step,.stp,.STEP,.STP" hidden @change="onVariantFile" />
      <div v-if="ws.ready.value" ref="meshMenuEl" class="mesh-wrap">
        <button
          class="tbtn icon"
          :class="{ on: meshMenuOpen }"
          :disabled="remeshing"
          :title="`Mesh quality: ${currentQuality}. Mesh the same STEP file again at another quality, as a new revision (this one and its runs stay as they are), to check that results do not depend on the mesh.`"
          aria-label="Re-mesh at another quality"
          aria-haspopup="menu"
          :aria-expanded="meshMenuOpen"
          @click="meshMenuOpen = !meshMenuOpen"
        >
          <Loader2 v-if="remeshing" :size="16" class="spin" />
          <Shapes v-else :size="16" />
          <ChevronDown :size="11" class="mesh-caret" />
        </button>
        <div v-if="meshMenuOpen" class="mesh-menu" role="menu">
          <div class="mesh-menu-head">Re-mesh as a new revision</div>
          <button v-for="q in meshQualities" :key="q.id" role="menuitem" :class="{ current: q.id === currentQuality }" @click="onRemesh(q.id)">
            <span class="mesh-menu-name">{{ q.name }}<span v-if="q.id === currentQuality" class="mesh-menu-cur"><Check :size="12" /> current</span></span>
            <span class="mesh-menu-hint">{{ q.hint }}</span>
          </button>
        </div>
      </div>
      <span class="tsep" />
      <div class="tgroup">
        <button class="tbtn" :disabled="!ws.ready.value" title="Materials, finishes and LED models" @click="libraryOpen = true"><BookOpen :size="16" /><span class="tlabel">Library</span></button>
        <button class="tbtn" :disabled="!ws.project.value" title="Every finished run of this project, by design and variant, with the headline results" @click="overviewOpen = true"><Table2 :size="16" /><span class="tlabel">Overview</span></button>
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
              <span v-if="t.warn" class="tab-warn" :title="t.warn" />
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
          <!-- left: how the model is looked at -->
          <div class="vb-left">
            <div class="tgroup">
              <button class="tbtn icon" :class="{ on: treeOpen }" title="Model tree (T)" aria-label="Model tree" @click="toggleTree"><ListTree :size="16" /></button>
              <button
                class="tbtn"
                :class="{ on: sectionsOn }"
                :title="sectionsOn ? 'Turn sections off and remove the cuts' : 'Cut through the model: opens the Sections panel. Drag a plane handle in the 3D view to move the cut.'"
                @click="toggleSections"
              >
                <Scissors :size="15" /><span class="tlabel">Sections</span>
              </button>

              <ToolbarPopover>
                <template #trigger="{ open, toggle }">
                  <button class="tbtn" :class="{ on: open }" title="What is shown in the 3D view" aria-haspopup="menu" :aria-expanded="open" @click="toggle">
                    <Eye :size="15" /><span class="tlabel">Display</span>
                    <span v-if="displayChanged" class="tbadge">{{ displayChanged }}</span>
                    <ChevronDown :size="11" class="tbtn-caret" />
                  </button>
                </template>
                <template #default>
                  <div class="tpop-head">Show in the 3D view</div>
                  <button class="tpop-row" :class="{ on: allTransparent }" role="menuitemcheckbox" :aria-checked="allTransparent" title="Make every part transparent, to see what is inside" @click="allTransparent = !allTransparent">
                    <Blend :size="16" /><span class="tpop-text"><strong>See-through</strong><small>Every part transparent</small></span><span class="tpop-check"><Check v-if="allTransparent" :size="14" /></span>
                  </button>
                  <button class="tpop-row" :class="{ on: dimOthers }" role="menuitemcheckbox" :aria-checked="dimOthers" title="Fade the other parts when one is selected" @click="dimOthers = !dimOthers">
                    <Ghost :size="16" /><span class="tpop-text"><strong>Fade others</strong><small>Fade the rest when a part is selected</small></span><span class="tpop-check"><Check v-if="dimOthers" :size="14" /></span>
                  </button>
                  <button class="tpop-row" :class="{ on: showLeds }" role="menuitemcheckbox" :aria-checked="showLeds" title="Show LED positions and directions" @click="showLeds = !showLeds">
                    <Lightbulb :size="16" /><span class="tpop-text"><strong>LEDs</strong><small>Positions and directions</small></span><span class="tpop-check"><Check v-if="showLeds" :size="14" /></span>
                  </button>
                  <button class="tpop-row" :class="{ on: ws.meshAll.value }" role="menuitemcheckbox" :aria-checked="ws.meshAll.value" title="Show the mesh the rays are traced against (all shown parts)" @click="ws.meshAll.value = !ws.meshAll.value">
                    <Grid3x3 :size="16" /><span class="tpop-text"><strong>Mesh</strong><small>What the rays are traced against</small></span><span class="tpop-check"><Check v-if="ws.meshAll.value" :size="14" /></span>
                  </button>
                  <div class="tpop-sep" />
                  <button class="tpop-row" :disabled="!sceneCtl.anyHidden.value" title="Show every part again (Alt+H)" @click="showAll">
                    <Eye :size="16" /><span class="tpop-text"><strong>Show all parts</strong><small>{{ sceneCtl.anyHidden.value ? "Some parts are hidden" : "Nothing is hidden" }}</small></span>
                  </button>
                </template>
              </ToolbarPopover>

              <ToolbarPopover>
                <template #trigger="{ open, toggle }">
                  <button
                    class="tbtn"
                    :class="{ on: open }"
                    :title="`Camera: ${viewer.currentProjection.value === 'orthographic' ? 'orthographic' : 'perspective'}. It applies to everything: the model, the maps on it, the photoreal view and the pictures rendered from the results.`"
                    aria-label="Camera projection"
                    aria-haspopup="menu"
                    :aria-expanded="open"
                    @click="toggle"
                  >
                    <component :is="viewer.currentProjection.value === 'orthographic' ? OrthographicIcon : PerspectiveIcon" :size="16" />
                    <ChevronDown :size="11" class="tbtn-caret" />
                  </button>
                </template>
                <template #default="{ close }">
                  <div class="tpop-head">Camera</div>
                  <button class="tpop-row" :class="{ on: viewer.currentProjection.value !== 'orthographic' }" role="menuitemradio" :aria-checked="viewer.currentProjection.value !== 'orthographic'" @click="viewer.setProjectionType('perspective'); close()">
                    <PerspectiveIcon :size="18" /><span class="tpop-text"><strong>Perspective</strong><small>Things further away look smaller</small></span><span class="tpop-check"><Check v-if="viewer.currentProjection.value !== 'orthographic'" :size="14" /></span>
                  </button>
                  <button class="tpop-row" :class="{ on: viewer.currentProjection.value === 'orthographic' }" role="menuitemradio" :aria-checked="viewer.currentProjection.value === 'orthographic'" @click="viewer.setProjectionType('orthographic'); close()">
                    <OrthographicIcon :size="18" /><span class="tpop-text"><strong>Orthographic</strong><small>No perspective, sizes stay true</small></span><span class="tpop-check"><Check v-if="viewer.currentProjection.value === 'orthographic'" :size="14" /></span>
                  </button>
                </template>
              </ToolbarPopover>

              <button class="tbtn icon" title="Fit the whole model" aria-label="Fit the whole model" @click="viewer.resetView()"><Maximize :size="16" /></button>
            </div>
          </div>

          <!-- right: the simulation -->
          <div class="vb-right">
            <div class="tgroup">
              <button class="tbtn" :class="{ on: livePanelOpen || live.enabled.value }" title="Live physics while you edit, and a photoreal view of the result" @click="toggleLive"><Zap :size="15" /><span class="tlabel">Live</span></button>
              <button v-if="activeRun" class="tbtn" :class="{ on: resultsShown }" title="Show the results of the opened run" @click="resultsShown = !resultsShown"><BarChart3 :size="15" /><span class="tlabel">Results</span></button>
              <button class="tbtn" :class="{ on: dockShown }" :title="activeRun ? 'What to show in the 3D view: the map, surfaces and rays; click a surface to inspect it' : 'Pick a run to show its map, surfaces and rays on the model'" @click="toggleDock"><ScanSearch :size="15" /><span class="tlabel">Analysis</span></button>
            </div>
          </div>
        </div>

        <LiveViewOverlay
          v-if="view.on && live.enabled.value && live.viewRunId.value && !resultsCover"
          :viewer="viewer"
          :run-id="live.viewRunId.value"
          :result-key="liveKey"
          :sim-running="live.phase.value === 'running' || live.phase.value === 'starting'"
          :exposure="view.exposure"
          :room-lux="view.roomLux"
          :exposure-mode="view.exposureMode"
          :white-balance="view.whiteBalance"
          :mode="view.mode"
          :resolution="view.resolution"
          :state="live.shownState.value"
          :hidden-exits="view.hiddenExits"
          @status="(s) => (view.status = s)"
          @lum="(l) => (view.lum = l)"
        />

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

        <!-- bottom left: the cut controls, with the colour scale of the map above them -->
        <div class="left-dock">
          <MapScaleLegend v-if="mapScales.length" :scales="mapScales" />
          <SectionPanel
            :visible="sectionsOn"
            :active-planes="activePlanes"
            :flip="flip"
            :plane-distances="viewer.planeDistances"
            :show-caps="viewer.showSectionCaps.value"
            :plane-visible="planeVisible"
            @close="turnOffSections"
            @clear="viewer.resetCuttingPlanes()"
            @toggle="(a) => togglePlane(a)"
            @flip="setFlip"
            @set-distance="(a, d) => viewer.setPlaneDistance(a, d)"
            @update:show-caps="setCaps"
            @update:plane-visible="setPlaneVisible"
          >
            <SectionLight v-if="activeRun" :sl="sectionLight" :av="av" />
          </SectionPanel>
        </div>

        <!-- right: Live and Analysis stack under the simulation buttons instead of overlapping -->
        <div v-if="!resultsCover" class="right-dock">
          <LivePanel v-if="livePanelOpen && ws.ready.value" :live="live" :view="view" @close="livePanelOpen = false" />
          <AnalysisDock v-if="dockShown" :ctx="dockCtx" :has-run="!!activeRun" :run-id="activeRun" :runs="pickableRuns" @close="dockOpen = false" @open-results="resultsShown = true" @pick-run="pickRunForDock" />
        </div>

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
            <ResultsView :run-id="activeRun" :projection="viewer.currentProjection.value" @loaded="onResultsLoaded" />
          </div>
        </section>
        <button v-if="activeRun && !resultsShown && !dockShown" class="back-to-results btn primary" @click="resultsShown = true"><BarChart3 :size="15" /> Back to results</button>
      </main>
    </div>

    <ContextMenu :visible="menu.visible" :items="menuItems" :x="menu.x" :y="menu.y" @pick="onMenuPick" @close="menu.visible = false" />
    <LibraryDialog v-if="libraryOpen" @close="libraryOpen = false" />
    <OverviewDialog v-if="overviewOpen" @close="overviewOpen = false" @open="openOverviewRun" />
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
  Check,
  ChevronDown,
  Crosshair,
  Eye,
  EyeOff,
  Focus,
  Ghost,
  Copy,
  Grid3x3,
  Layers,
  Info,
  Keyboard,
  Lightbulb,
  ListTree,
  Loader2,
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
  SlidersHorizontal,
  Sun,
  Table2,
  Trash2,
  Wand2,
  X,
  Zap,
} from "lucide-vue-next";
import * as THREE from "three";
import { api, type DetectorResult, type MeshQuality, type RunScene, type StateResult } from "../api";
import { createWorkspace, hexToNumber, provideWorkspace } from "../composables/useWorkspace";
import { provideAnalysisView, useAnalysisView, type AnalysisCtx, type MapScale, type ModelMap } from "../composables/useAnalysisView";
import { useSectionLight } from "../composables/useSectionLight";
import SectionLight from "../components/analysis/SectionLight.vue";
import { useThreeViewer, type TreeNode } from "../composables/viewer/useThreeViewer";
import { useSetupScene, type FaceHighlight } from "../composables/useSetupScene";
import { useResultsOverlay } from "../composables/useResultsOverlay";
import { useOpticalMeshOverlay } from "../composables/useOpticalMeshOverlay";
import { useLivePreview } from "../composables/useLivePreview";
import { isDark, toggleTheme, viewportBackground } from "../composables/useTheme";
import { newId, partPattern, resolvePart, ruleFor, sigOf } from "../utils/setup";
import { drawMap } from "../utils/drawMap";
import { exitFrame } from "../utils/analysisText";
import DesignSwitcher from "../components/DesignSwitcher.vue";
import RevisionPicker from "../components/RevisionPicker.vue";
import PartsTab from "../components/PartsTab.vue";
import LedsTab from "../components/LedsTab.vue";
import DetectorTab from "../components/DetectorTab.vue";
import GroupsTab from "../components/GroupsTab.vue";
import AnalysisDock from "../components/AnalysisDock.vue";
import MapScaleLegend from "../components/MapScaleLegend.vue";
import StatesTab from "../components/StatesTab.vue";
import RunTab from "../components/RunTab.vue";
import ResultsTab from "../components/ResultsTab.vue";
import LivePanel, { type PhotorealView } from "../components/LivePanel.vue";
import LiveViewOverlay from "../components/LiveViewOverlay.vue";
import { DEFAULT_ROOM_LUX } from "../utils/roomLux";
import ResultsView from "../components/ResultsView.vue";
import LibraryDialog from "../components/LibraryDialog.vue";
import OverviewDialog from "../components/OverviewDialog.vue";
import ContextMenu, { type MenuItem } from "../components/ContextMenu.vue";
import ModelTreeOverlay from "../components/viewer/ModelTreeOverlay.vue";
import SectionPanel from "../components/viewer/SectionPanel.vue";
import ToolbarPopover from "../components/viewer/ToolbarPopover.vue";
import { OrthographicIcon, PerspectiveIcon } from "../components/icons/customIcons";
import HotkeyOverlay from "../components/viewer/HotkeyOverlay.vue";

const props = defineProps<{ id: string }>();
const router = useRouter();

// ── workspace shared with the panels ──────────────────────────────────────────
const ws = createWorkspace(computed(() => props.id) as never);
provideWorkspace(ws);
ws.start();

const route = useRoute();
const variants = computed(() => ws.project.value?.variants ?? []);
const designs = computed(() => ws.project.value?.designs ?? []);
/** Every revision of the project, newest first. */
const allRevisions = computed(() => ws.project.value?.revisionList ?? []);
/** The revisions of the variant being edited (the revision picker shows when there are several). */
const variantRevisions = computed(() => allRevisions.value.filter((r) => r.variantId === ws.variant.value?.id));
const canDeleteVariant = computed(() => {
  const v = ws.variant.value;
  return !!v && variants.value.filter((x) => x.designId === v.designId).length > 1;
});
function goRevision(rid: string) {
  if (rid !== props.id) router.push(`/r/${rid}`);
}
/** Where to go once something was deleted: the latest revision of what is left. */
function goAfterDelete(project: { variants: { latest: { id: string } | null }[]; latest: { id: string } | null }) {
  const next = project.variants.find((v) => v.latest)?.latest ?? project.latest;
  router.push(next ? `/r/${next.id}` : "/");
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
  if (!v || !pid || !canDeleteVariant.value) return;
  if (!confirm(`Delete the variant "${v.name}" with its ${v.revisions.length} revision(s), its setup and every simulation run?`)) return;
  try {
    const project = await api.deleteVariant(pid, v.id);
    // stay in the same design when possible
    const sibling = project.variants.find((x) => x.designId === v.designId && x.latest);
    if (sibling?.latest) router.push(`/r/${sibling.latest.id}`);
    else goAfterDelete(project);
  } catch (e) {
    alert((e as Error).message);
  }
}
async function renameDesign(did: string) {
  const d = designs.value.find((x) => x.id === did);
  const pid = ws.meta.value?.projectId;
  if (!d || !pid) return;
  const name = window.prompt("Rename design", d.name);
  if (!name?.trim()) return;
  try {
    await api.renameDesign(pid, did, name.trim());
    await ws.loadProject();
  } catch (e) {
    alert((e as Error).message);
  }
}
async function deleteDesign(did: string) {
  const d = designs.value.find((x) => x.id === did);
  const pid = ws.meta.value?.projectId;
  if (!d || !pid || designs.value.length < 2) return;
  const inside = variants.value.filter((v) => v.designId === did);
  const revs = inside.reduce((n, v) => n + v.revisions.length, 0);
  if (!confirm(`Delete the design "${d.name}" with its ${inside.length} variant(s), ${revs} revision(s), their setups and every simulation run?`)) return;
  try {
    const project = await api.deleteDesign(pid, did);
    // only leave the page when what is open was deleted
    if (inside.some((v) => v.id === ws.variantId.value)) goAfterDelete(project);
    else await ws.loadProject();
  } catch (e) {
    alert((e as Error).message);
  }
}
async function moveVariant(vid: string) {
  const v = variants.value.find((x) => x.id === vid);
  const pid = ws.meta.value?.projectId;
  if (!v || !pid) return;
  const others = designs.value.filter((d) => d.id !== v.designId);
  const answer = window.prompt(`Move the variant "${v.name}" to which design? Type its name:\n${others.map((d) => `  ${d.name}`).join("\n")}`, others[0]?.name ?? "");
  if (answer === null) return;
  const target = others.find((d) => d.name.trim().toLowerCase() === answer.trim().toLowerCase());
  if (!target) {
    alert(`There is no other design called "${answer.trim()}".`);
    return;
  }
  try {
    await api.moveVariant(pid, vid, target.id);
    await ws.loadProject();
  } catch (e) {
    alert((e as Error).message);
  }
}

// New variants and designs start from another STEP file (or, for "Duplicate", from the same one) and a copy of the setup.
const variantFile = ref<HTMLInputElement | null>(null);
const variantUploading = ref(false);
let pendingUpload: { designId?: string; newDesign?: boolean; copyFrom: string } | null = null;
/** The variant whose setup a new variant of this design starts with: the open one when it is in the design, else the design's newest. */
function setupSourceFor(did: string): string | undefined {
  if (ws.variant.value?.designId === did) return ws.variantId.value;
  const inside = variants.value.filter((v) => v.designId === did);
  return inside[inside.length - 1]?.id ?? ws.variantId.value;
}
function newVariantInDesign(did: string) {
  const copyFrom = setupSourceFor(did);
  if (!did || !copyFrom) return;
  pendingUpload = { designId: did, copyFrom };
  variantFile.value?.click();
}
function newDesign() {
  if (!ws.variantId.value) return;
  pendingUpload = { newDesign: true, copyFrom: ws.variantId.value };
  variantFile.value?.click();
}
/** A new variant with the same geometry: no upload, the CAD is imported again; the setup is a copy to change materials or LEDs in. */
async function duplicateVariant(rid: string) {
  const rev = allRevisions.value.find((r) => r.id === rid) ?? (rid === props.id ? ws.meta.value : null);
  const source = variants.value.find((v) => v.id === rev?.variantId) ?? ws.variant.value;
  const name = window.prompt("Name of the new variant (same geometry, copy of the setup)", `${source?.name ?? "Variant"} (copy)`);
  if (name === null) return;
  variantUploading.value = true;
  try {
    const res = await api.duplicateRevision(rid, name.trim() || undefined);
    router.push(`/r/${res.id}`);
  } catch (err) {
    alert((err as Error).message);
  } finally {
    variantUploading.value = false;
  }
}
async function onVariantFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  const pid = ws.meta.value?.projectId;
  const target = pendingUpload;
  pendingUpload = null;
  if (!file || !pid || !target) return;
  const suggested = file.name.replace(/\.[^.]+$/, "");
  const name = window.prompt(target.newDesign ? "Name of the new design" : "Name of the new variant", suggested);
  if (name === null) return;
  variantUploading.value = true;
  try {
    const res = await api.upload(
      file,
      target.newDesign
        ? { projectId: pid, variantId: "new", designId: "new", designName: name.trim() || suggested, copySetupFrom: target.copyFrom }
        : { projectId: pid, variantId: "new", designId: target.designId, variantName: name.trim() || suggested, copySetupFrom: target.copyFrom },
    );
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
// arriving from "New variant" / "New design": adapt the copied setup to the geometry as soon as it can be read
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
const meshQualities: { id: MeshQuality; name: string; hint: string }[] = [
  { id: "coarse", name: "Coarse", hint: "2x the deflection; fastest" },
  { id: "normal", name: "Normal", hint: "the default" },
  { id: "fine", name: "Fine", hint: "1/4 the deflection, half the facet angle" },
];
const currentQuality = computed<MeshQuality>(() => (ws.partsDoc.value?.quality ?? ws.meta.value?.meshQuality ?? "normal") as MeshQuality);
const remeshing = ref(false);
const meshMenuOpen = ref(false);
const meshMenuEl = ref<HTMLElement | null>(null);
function closeMeshMenuOutside(e: MouseEvent) {
  if (meshMenuOpen.value && !meshMenuEl.value?.contains(e.target as Node)) meshMenuOpen.value = false;
}
function closeMeshMenuKey(e: KeyboardEvent) {
  if (e.key === "Escape") meshMenuOpen.value = false;
}
onMounted(() => {
  document.addEventListener("mousedown", closeMeshMenuOutside);
  document.addEventListener("keydown", closeMeshMenuKey);
});
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", closeMeshMenuOutside);
  document.removeEventListener("keydown", closeMeshMenuKey);
});
async function onRemesh(quality: MeshQuality) {
  meshMenuOpen.value = false;
  if (!confirm(`Mesh this STEP file again at "${quality}" quality as a new revision of this variant? This revision and its runs are not changed.`)) return;
  remeshing.value = true;
  try {
    const res = await api.remesh(props.id, quality);
    router.push(`/r/${res.id}`);
  } catch (err) {
    alert((err as Error).message);
  } finally {
    remeshing.value = false;
  }
}
async function reimport() {
  await api.reimport(props.id);
  await ws.refreshMeta();
  ws.schedulePoll();
}

// ── tabs ────────────────────────────────────────────────────────────────────
type TabId = "parts" | "leds" | "detector" | "groups" | "states" | "runs" | "results";
const tab = ref<TabId>("parts");
// `warn` puts a small dot on the tab (its text is the tooltip): something the simulation needs is still missing there
const tabs = computed<{ id: TabId; label: string; icon: Component; warn?: string }[]>(() => [
  { id: "parts", label: "Parts", icon: Boxes },
  { id: "leds", label: "LEDs", icon: Lightbulb, warn: ws.setup.value?.leds.length ? undefined : "No LEDs yet" },
  { id: "detector", label: "Exit", icon: Crosshair, warn: ws.setup.value?.detectors.length ? undefined : "No exit surface yet" },
  { id: "groups", label: "Groups", icon: Shapes },
  { id: "states", label: "States", icon: SlidersHorizontal, warn: ws.setup.value?.states.length ? undefined : "No states yet" },
  { id: "runs", label: "Run", icon: Play },
  { id: "results", label: "Results", icon: BarChart3 },
]);

const libraryOpen = ref(false);
const overviewOpen = ref(false);
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
// the viewport follows the theme (light / dark switch)
watch(isDark, () => viewer.setSceneBackground(viewportBackground()));
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
// with no run open there is no results page to cover the view: the panel then offers the runs to pick from
const dockShown = computed(() => dockOpen.value && (!activeRun.value || !resultsShown.value));
/** The finished runs the panel offers when none is open (like the Results tab: no sweep variants), newest first. */
const pickableRuns = computed(() =>
  // (the run that is open stays in the list even when it would not be offered, so the choice always shows it)
  ws.runs.value.filter((r) => r.id === activeRun.value || (!r.sweep && r.status === "ready")).sort((a, b) => b.created - a.created),
);
/** Choosing a run in the panel is opening it in the results and pressing View in 3D: the exit map of the run on the model, nothing picked. */
function pickRunForDock(rid: string) {
  if (rid === activeRun.value) return;
  tab.value = "results";
  activeRun.value = rid;
  resultsShown.value = false;
  dockOpen.value = true;
}
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
watch(() => dockShown.value && !!activeRun.value, (v) => (av.inspecting.value = v), { immediate: true });
/** Faces painted by the light path analysis (inspected face, surface hit map). */
const analysisHighlights = computed<FaceHighlight[]>(() => av.highlights.value);
// the exit surfaces / receivers are painted only while their tab is open (or faces are being picked into one)
const allHighlights = computed(() => {
  const showDetectors = tab.value === "detector" || ws.pick.value?.kind === "detector";
  const own = showDetectors ? ws.highlights.value : ws.highlights.value.filter((h) => !h.key.startsWith("det:"));
  return [...own, ...analysisHighlights.value];
});
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

// ── optical mesh of the parts ticked in the Parts tab ─────────────────────────────────────
const meshOverlay = useOpticalMeshOverlay(viewer);
watch(() => sceneCtl.shownIds.value.join(","), () => (ws.shownParts.value = sceneCtl.shownIds.value), { immediate: true });
/** Parts whose mesh is drawn: every shown part with the toolbar toggle on, otherwise the ones ticked in the Parts tab. */
const meshIds = computed(() => (ws.meshAll.value ? ws.shownParts.value : ws.meshParts.value));
let meshRequest = 0;
watch(
  () => [meshIds.value.join(","), viewer.modelLoaded.value] as const,
  async ([ids, loaded]) => {
    const req = ++meshRequest;
    if (!ids || !loaded) {
      meshOverlay.clear();
      return;
    }
    try {
      const mesh = await api.opticalMesh(props.id, ids.split(","));
      if (req !== meshRequest) return;
      const sel = ws.parts.value.filter((p) => meshIds.value.includes(p.id));
      const thin = Math.min(...sel.map((p) => Math.min(...p.bbox[1].map((v, k) => v - p.bbox[0][k]))));
      meshOverlay.set(mesh, Math.max(Number.isFinite(thin) ? thin * 0.002 : 0.005, 0.001));
    } catch (e) {
      if (req === meshRequest) alert((e as Error).message);
    }
  },
);

// ── live physics and the photoreal view ──────────────────────────────────────────────────
const live = useLivePreview(ws);
const livePanelOpen = ref(false);
/** The Live button: opening the panel also switches the simulation on; clicking again only hides the panel (it keeps running). */
function toggleLive() {
  if (livePanelOpen.value) {
    livePanelOpen.value = false;
    return;
  }
  livePanelOpen.value = true;
  if (!live.enabled.value) void live.toggle();
}
const view = reactive<PhotorealView>({ on: false, exposure: 0, roomLux: DEFAULT_ROOM_LUX, exposureMode: "fixed", whiteBalance: false, mode: "map", resolution: 0, hiddenExits: [], status: null, lum: null });
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
const PLANE_KEY = "refract.planeHandle";
/** Sections are on while the panel at the bottom left is open; turning them off also removes the cuts. */
const sectionsOn = ref(false);
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
const CAPS_KEY = "refract.sectionCaps";
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
/** Which side of a plane that is on is removed. */
function setFlip(axis: "x" | "y" | "z", v: boolean) {
  flip[axis] = v;
  if (viewer.getActivePlanes().includes(axis)) viewer.updateSectionFlip(axis, v);
}
/** The Sections button: on opens the panel and starts with the X plane so something visibly happens. */
function toggleSections() {
  if (sectionsOn.value) {
    turnOffSections();
    return;
  }
  sectionsOn.value = true;
  if (activePlanes.value.size === 0) togglePlane("x");
}
function turnOffSections() {
  viewer.resetCuttingPlanes();
  sectionsOn.value = false;
}
// a cut made any other way (a hotkey, the palette) brings the panel up
watch(
  () => activePlanes.value.size,
  (n) => {
    if (n > 0) sectionsOn.value = true;
  },
);
/** How many Display options differ from the default, shown as a badge on the button. */
const displayChanged = computed(
  () => Number(dimOthers.value) + Number(allTransparent.value) + Number(ws.meshAll.value) + Number(!showLeds.value) + Number(sceneCtl.anyHidden.value),
);
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
/** A row of the project overview: open the run here, or on its own revision (the run is opened once that revision has loaded). */
function openOverviewRun(run: { runId: string; revisionId: string }) {
  overviewOpen.value = false;
  if (run.revisionId === props.id) {
    tab.value = "results";
    openRun(run.runId);
  } else router.push({ path: `/r/${run.revisionId}`, query: { run: run.runId } });
}
// arriving from the overview with a run to open
let openedFromQuery = false;
watch(
  () => [route.query.run, ws.runs.value.map((r) => `${r.id}:${r.status}`).join("|")] as const,
  ([rid]) => {
    if (typeof rid !== "string") {
      openedFromQuery = false; // the query was cleared: the next link from the overview may open a run again
      return;
    }
    if (openedFromQuery) return;
    const run = ws.runs.value.find((r) => r.id === rid);
    if (!run) return;
    openedFromQuery = true;
    router.replace({ path: route.path });
    if (run.status === "ready") {
      tab.value = "results";
      openRun(rid);
    }
  },
  { immediate: true },
);
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

/**
 * The colour scale of the map drawn on the model for detector `di` (0 up to `vmax`). The textures and the scale legend both take it
 * from here, so the legend always says what the colours mean. A footprint from the analysis replaces the exit map of its detector.
 */
function mapScaleOf(d: DetectorResult, di: number, part: ModelMap | null): MapScale {
  if (part && part.detector === di) return { label: part.label, vmax: part.vmax, unit: part.unit };
  const rcv = d.kind === "receiver";
  const what = d.quantity === "luminance" ? (rcv ? "Apparent luminance" : "Luminance") : rcv ? "Illuminance" : "Exitance";
  return { label: `${what} - ${d.name}`, vmax: (d.stats.max ?? 1) * 1.02 || 1, unit: d.unit };
}
/** The scales of the maps on the model, for the legend (the opened run only; the live preview has no analysis panel). */
const mapScales = computed<MapScale[]>(() => {
  const res = current.value?.result;
  const sc = runScene.value;
  if (!av.layers.showScale || !drape.value || !activeRun.value || resultsShown.value || !res || !sc || (live.enabled.value && !!live.result.value && !!live.scene.value)) return [];
  const part = av.modelMap.value;
  return res.detectors.flatMap((d, di) => (sc.detectors.some((x) => x.id === d.id) ? [mapScaleOf(d, di, part)] : []));
});

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
      const scale = mapScaleOf(d, di, part);
      if (part && part.detector === di) drawMap(canvas, part.values, null, 0, scale.vmax, { flipV: false });
      else drawMap(canvas, d.map ?? d[d.quantity === "luminance" ? "luminance" : "exitance"], d.valid, 0, scale.vmax, { flipV: false });
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
  // a receiver detector measures what a viewer sees: a blue arrow shows where the viewer is, only while the exits are being edited (Exit tab)
  if (tab.value === "detector") {
    const shown = drape.value && !liveOn && activeRun.value && runScene.value ? runScene.value.detectors : null;
    const src = shown ?? ws.check.value?.detectors ?? [];
    const arrows = src
      .filter((d) => d.kind === "receiver" && d.view && d.corners.length >= 4)
      .map((d) => {
        const c = d.corners.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
        const origin = d.origin ?? c.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / c.length).toArray();
        const size = Math.max(c[1].distanceTo(c[0]), c[3].distanceTo(c[0])) * 0.6;
        return { origin, view: d.view as number[], size: Math.max(size, diag * 0.05) };
      });
    if (arrows.length) overlay.setViewArrows(arrows);
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
