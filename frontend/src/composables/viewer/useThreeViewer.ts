import {
  ref,
  onMounted,
  onBeforeUnmount,
  type Ref,
  watch,
  shallowRef,
  reactive,
} from "vue";
import * as THREE from "three";
import { useTransformGizmo } from "./useTransformGizmo";
import { CadOrbitControls } from "../../lib/CadOrbitControls";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "meshoptimizer";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { MeshBVH, acceleratedRaycast, SAH } from "three-mesh-bvh";
import { disposeCapWorkerManager } from "../../workers/capWorkerManager";
import {
  useSectionPlanes,
  type PlaneAxis,
  type SectionPlanesContext,
} from "./useSectionPlanes";
import { useViewportGizmo } from "./useViewportGizmo";
import {
  useThreeMeasure,
  type ThreeMeasureContext,
  type MeasurementData,
} from "./useThreeMeasure";
import {
  extractEdgeTopology,
  getFaceIdAtTriangle,
  getBrepFaceData,
  getBrepFaceTrianglePositions,
  findNearestBrepEdge,
  getBrepEdgeWorldPositions,
  getBrepEdgeData,
  computeMinEdgeToEdgeDistance,
  computeMinEdgeToFaceDistance,
} from "./useBrepTopology";
import {
  useSequentialHide,
  type SequentialHideContext,
} from "./useSequentialHide";
import { useExplodeView, type ExplodeViewContext } from "./useExplodeView";
import { useInteractionManager } from "./useInteractionManager";
import { useHotkeys } from "./useHotkeys";
export type ControlType = "custom" | "customTrackball";
import { useModelAppearance } from "./useModelAppearance";
import { usePartSelection } from "./usePartSelection";
import { performDebugRaycast } from "./performDebugRaycast";

let _colorProbe: HTMLElement | null = null;
/** Resolve any CSS color (oklch, lab, etc.) to a `#rrggbb` hex string via computed style. */
function cssColorToHex(css: string): string {
  if (!_colorProbe) {
    _colorProbe = document.createElement("div");
    _colorProbe.style.display = "none";
    document.body.appendChild(_colorProbe);
  }
  _colorProbe.style.color = css;
  const rgb = getComputedStyle(_colorProbe).color;
  const m = rgb.match(/\d+/g);
  if (!m || m.length < 3) return "#333333";
  return (
    "#" +
    ((1 << 24) | (Number(m[0]) << 16) | (Number(m[1]) << 8) | Number(m[2]))
      .toString(16)
      .slice(1)
  );
}

/**
 * Serializable camera view state.
 * Resolution-independent: frustum is recomputed from the viewer's current
 * container size on restore, so the same state looks correct on any screen.
 */
export interface CameraViewState {
  position: { x: number; y: number; z: number };
  target: { x: number; y: number; z: number };
  up: { x: number; y: number; z: number };
  /** Camera orientation quaternion — preferred over up+lookAt for lossless restore. */
  quaternion?: { x: number; y: number; z: number; w: number };
  projection: "perspective" | "orthographic";
  /** Perspective FOV in degrees (stored even in ortho for round-trip) */
  fov: number;
  /** Orthographic zoom factor (null when perspective) */
  orthoZoom: number | null;
  /** Raw orthographic frustum half-height (null when perspective or legacy data) */
  orthoHalfH?: number | null;
  /** Viewport aspect ratio (w/h) at save time for contain-fit on narrower screens. */
  aspect?: number;
}

// Re-export TreeNode type for compatibility with existing code
export interface TreeNode {
  nodeId: number;
  name: string;
  type: "root" | "assembly" | "part";
  children: TreeNode[];
  isExpanded: boolean;
  visibilityState: "visible" | "hidden" | "partial";
}

// Performance stats exposed for external overlay component
export interface PerformanceStats {
  // Frame stats
  fps: number;
  frameTimeMs: number;
  // Render stats
  calls: number;
  triangles: number;
  lines: number;
  // Model stats
  fileSize: number | null;
  vertices: number;
  meshes: number;
  // Model loading timings (ms)
  downloadMs: number | null;
  downloadPercent: number | null;
  parseDecodeMs: number | null;
  sceneBuildMs: number | null;
  bvhMs: number | null;
  bboxCameraMs: number | null;
  capsMs: number | null;
  totalModelLoadMs: number | null;
  // Loading phase tracking (ms timestamps)
  modelLoadStartedAt: number | null;
  loadingPhase: string | null;
  loadingPhaseStartedAt: number | null;
  // Loading step start timestamps
  downloadStartedAt: number | null;
  parseDecodeStartedAt: number | null;
  sceneBuildStartedAt: number | null;
  bvhStartedAt: number | null;
  bboxCameraStartedAt: number | null;
  capsStartedAt: number | null;
  // Memory stats
  geometries: number;
  textures: number;
  jsHeapMB: number | null;
}

export interface ThreeViewerOptions {
  /** Presigned URL for the GLB mesh */
  meshUrl: Ref<string>;
  /** Presigned URL for the metadata JSON (optional for now) */
  metadataUrl?: Ref<string>;
}

// Install BVH-accelerated raycast once so Mesh.raycast uses boundsTree (and firstHitOnly)
let acceleratedRaycastInstalled = false;
const paddedGeometries = new WeakSet<THREE.BufferGeometry>();

/**
 * Expand every BVH node's AABB by a scale-relative epsilon to compensate for
 * the zero-tolerance slab method used in three-mesh-bvh's ray-AABB test.
 * Without this, rays nearly parallel to a thin AABB face produce false
 * negatives — the node's subtree is pruned and all its triangles are missed.
 *
 * Node layout (three-mesh-bvh 0.9.x, confirmed for v0.9.8):
 *   8 × uint32 per node = 32 bytes.
 *   float32 [0..2] = min(x,y,z)   float32 [3..5] = max(x,y,z)
 *   uint32  [6] = offset/right-child   uint32 [7] = split-axis + leaf flag
 */
function padBVHBounds(bvh: MeshBVH, epsilon = 5e-3): void {
  const UINT32_PER_NODE = 8;
  for (const root of (bvh as any)._roots as ArrayBuffer[]) {
    const f32 = new Float32Array(root);
    const nodeCount = root.byteLength / (UINT32_PER_NODE * 4);
    for (let i = 0; i < nodeCount; i++) {
      const base = i * UINT32_PER_NODE;
      for (let axis = 0; axis < 3; axis++) {
        const lo = f32[base + axis];
        const hi = f32[base + 3 + axis];
        const absMax = Math.max(Math.abs(lo), Math.abs(hi), 1.0);
        const pad = absMax * epsilon;
        f32[base + axis] = lo - pad;
        f32[base + 3 + axis] = hi + pad;
      }
    }
  }
}

export function useThreeViewer(
  canvasRef: Ref<HTMLCanvasElement | null>,
  options: ThreeViewerOptions,
) {
  // State
  const status = ref<string>("Initializing...");
  const viewerReady = ref(false);
  const modelLoaded = ref(false);
  const error = ref<string | null>(null);

  // Three.js objects (use shallowRef to avoid deep reactivity)
  const scene = shallowRef<THREE.Scene | null>(null);
  const camera = shallowRef<
    THREE.PerspectiveCamera | THREE.OrthographicCamera | null
  >(null);
  const renderer = shallowRef<THREE.WebGLRenderer | null>(null);
  const controls = shallowRef<CadOrbitControls | null>(null);
  const loadedModel = shallowRef<THREE.Group | null>(null);

  // Current control type
  const currentControlType = ref<ControlType>("customTrackball");

  // Current camera projection type and FOV
  const currentProjection = ref<"perspective" | "orthographic">("perspective");
  const currentFov = ref<number>(50);
  const zoomInverted = ref<boolean>(false);

  // Animation frame ID for cleanup
  let animationFrameId: number | null = null;
  let resizeObserver: ResizeObserver | null = null;

  // Performance stats (reactive, for external overlay component)
  const performanceStats = reactive<PerformanceStats>({
    fps: 0,
    frameTimeMs: 0,
    calls: 0,
    triangles: 0,
    lines: 0,
    fileSize: null,
    vertices: 0,
    meshes: 0,
    downloadMs: null,
    downloadPercent: null,
    parseDecodeMs: null,
    sceneBuildMs: null,
    bvhMs: null,
    bboxCameraMs: null,
    capsMs: null,
    totalModelLoadMs: null,
    modelLoadStartedAt: null,
    loadingPhase: null,
    loadingPhaseStartedAt: null,
    downloadStartedAt: null,
    parseDecodeStartedAt: null,
    sceneBuildStartedAt: null,
    bvhStartedAt: null,
    bboxCameraStartedAt: null,
    capsStartedAt: null,
    geometries: 0,
    textures: 0,
    jsHeapMB: null,
  });

  // FPS calculation state
  let lastFrameTime = performance.now();
  let frameCount = 0;
  let fpsAccumulator = 0;
  let lastFpsUpdate = performance.now();

  // Debug mode toggle (exposed for dev overlay)
  const raycastDebugEnabled = ref(false);

  // When true, hover/leave handlers won't change the canvas cursor
  // (used by markup mode to keep the pin cursor from being overwritten)
  const cursorLocked = ref(false);

  // Orbit target marker
  let orbitMarker: THREE.Mesh | null = null;

  const DEBUG_MARKER_COLOR = 0xff0000;
  const DEBUG_MARKER_SIZE = 0.005;

  // Raycasting - configure to only hit mesh faces, not lines/points
  const raycaster = new THREE.Raycaster();
  // Disable line raycasting (default threshold is 1 unit which causes vertex snapping)
  raycaster.params.Line = { threshold: 0 };
  // Disable point raycasting
  raycaster.params.Points = { threshold: 0 };

  // Pivot raycast: reuse to avoid allocations in pointerdown
  const pivotCoords = new THREE.Vector2();
  const pivotIntersects: THREE.Intersection[] = [];

  // Zoom state - cached cursor NDC for depth-independent zoom-to-cursor
  let cachedZoomNDC: THREE.Vector2 | null = null;

  // Hover raycast throttle
  let lastHoverRaycastTime = 0;
  const HOVER_RAYCAST_THROTTLE_MS = 50;

  // Zoom inertia
  let zoomVelocity = 0;
  const ZOOM_DAMPING = 0.2; // Decay factor per frame (lower = more friction)
  const ZOOM_VELOCITY_THRESHOLD = 0.0001; // Stop when velocity is negligible

  // ---------------------------------------------------------------------------
  // Camera helpers (projection-agnostic)
  // ---------------------------------------------------------------------------

  /**
   * Compute world-space units per CSS pixel at the given distance from the camera.
   * Works for both PerspectiveCamera and OrthographicCamera.
   */
  const getWorldPerPixel = (distance: number): number => {
    const cam = camera.value;
    const viewportHeight = renderer.value?.domElement.clientHeight || 1;
    if (!cam) return 1;
    if (cam instanceof THREE.PerspectiveCamera) {
      const fovRad = THREE.MathUtils.degToRad(cam.fov);
      return (2 * distance * Math.tan(fovRad / 2)) / viewportHeight;
    } else {
      const ortho = cam as THREE.OrthographicCamera;
      return (ortho.top - ortho.bottom) / (ortho.zoom * viewportHeight);
    }
  };

  /**
   * Create a camera matching the current projection setting.
   */
  const createCamera = (
    width: number,
    height: number,
  ): THREE.PerspectiveCamera | THREE.OrthographicCamera => {
    const aspect = width / height;
    if (currentProjection.value === "orthographic") {
      // Use a sensible default frustum; fitCameraToModel will resize it
      const halfH = 5;
      const halfW = halfH * aspect;
      const cam = new THREE.OrthographicCamera(
        -halfW,
        halfW,
        halfH,
        -halfH,
        -10000,
        10000,
      );
      cam.position.set(5, 5, 5);
      cam.up.set(0, 1, 0);
      return cam;
    } else {
      const cam = new THREE.PerspectiveCamera(
        currentFov.value,
        aspect,
        0.1,
        10000,
      );
      cam.position.set(5, 5, 5);
      cam.up.set(0, 1, 0);
      return cam;
    }
  };

  // BVH and model data (used by section planes composable)
  const bvhMeshes = shallowRef<THREE.Mesh[]>([]);
  let modelBoundingBox: THREE.Box3 | null = null; // Cached for performance
  let modelEdgeLineMaterials: LineMaterial[] = []; // Fat line materials for resolution updates on resize

  // O(1) lookup: cadreview_instanceId → Object3D.
  // Rebuilt on model load, cleared on unload.
  const instanceIdMap = new Map<string, THREE.Object3D>();
  const rebuildInstanceIdMap = () => {
    instanceIdMap.clear();
    if (!loadedModel.value) return;
    loadedModel.value.traverse((child) => {
      const iid = child.userData?.cadreview_instanceId;
      if (iid) instanceIdMap.set(iid, child);
    });
  };

  // Pending resize dimensions — set by ResizeObserver, consumed by the render
  // loop so that buffer reallocation + render happen in the same frame (no
  // white flash between clearing the buffer and drawing the next frame).
  let pendingResize: { width: number; height: number } | null = null;

  /**
   * Convert a THREE.Line or THREE.LineSegments into a fat LineSegments2.
   * Returns the new fat line object, or null if conversion fails.
   */
  const convertToFatLine = (
    original: THREE.Line | THREE.LineSegments,
  ): LineSegments2 | null => {
    const geometry = original.geometry as THREE.BufferGeometry;
    const posAttr = geometry.getAttribute("position") as THREE.BufferAttribute;
    if (!posAttr || posAttr.count < 2) return null;

    let positions: Float32Array;

    if (original instanceof THREE.LineSegments) {
      // LineSegments: each consecutive pair of vertices is one segment
      if (geometry.index) {
        const index = geometry.index;
        positions = new Float32Array(index.count * 3);
        for (let i = 0; i < index.count; i++) {
          const vi = index.getX(i);
          positions[i * 3] = posAttr.getX(vi);
          positions[i * 3 + 1] = posAttr.getY(vi);
          positions[i * 3 + 2] = posAttr.getZ(vi);
        }
      } else {
        positions = new Float32Array(posAttr.array);
      }
    } else {
      // THREE.Line: continuous polyline → convert to segment pairs
      const vertexCount = geometry.index ? geometry.index.count : posAttr.count;
      if (vertexCount < 2) return null;
      const segmentCount = vertexCount - 1;
      positions = new Float32Array(segmentCount * 2 * 3);
      for (let i = 0; i < segmentCount; i++) {
        const vi0 = geometry.index ? geometry.index.getX(i) : i;
        const vi1 = geometry.index ? geometry.index.getX(i + 1) : i + 1;
        positions[i * 6] = posAttr.getX(vi0);
        positions[i * 6 + 1] = posAttr.getY(vi0);
        positions[i * 6 + 2] = posAttr.getZ(vi0);
        positions[i * 6 + 3] = posAttr.getX(vi1);
        positions[i * 6 + 4] = posAttr.getY(vi1);
        positions[i * 6 + 5] = posAttr.getZ(vi1);
      }
    }

    const lineGeometry = new LineSegmentsGeometry();
    lineGeometry.setPositions(positions);

    const lineMaterial = new LineMaterial({
      color: 0x1a1a1a,
      linewidth: 1,
      depthTest: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });
    lineMaterial.resolution.set(window.innerWidth, window.innerHeight);

    const fatLine = new LineSegments2(lineGeometry, lineMaterial);
    fatLine.renderOrder = 1;
    fatLine.frustumCulled = false;

    // Copy transforms from original
    fatLine.name = original.name;
    fatLine.position.copy(original.position);
    fatLine.rotation.copy(original.rotation);
    fatLine.scale.copy(original.scale);
    fatLine.matrix.copy(original.matrix);
    fatLine.matrixAutoUpdate = original.matrixAutoUpdate;
    if (!original.matrixAutoUpdate) {
      fatLine.matrixWorldNeedsUpdate = true;
    }

    // Preserve userData (nodeId, etc.)
    fatLine.userData = { ...original.userData, isFatEdgeLine: true };
    fatLine.visible = original.visible;
    fatLine.layers.mask = original.layers.mask;

    // Disable raycasting on edge lines — they shouldn't be selectable
    fatLine.raycast = () => {};

    // Track material for resolution updates
    modelEdgeLineMaterials.push(lineMaterial);

    return fatLine;
  };

  // Centralized interaction mode manager
  const interaction = useInteractionManager();

  // Centralized hotkey registry
  const hotkeys = useHotkeys({ interaction });

  // Forward-declare appearance — initialized after sectionPlanes since it
  // needs updateMaterialClipping / refreshAllCaps, but sectionPlanes needs
  // getCachedMaterials which is lazily resolved via closure.
  // eslint-disable-next-line prefer-const
  let appearance!: ReturnType<typeof useModelAppearance>;

  // Section planes composable context and instance
  const sectionPlanesContext: SectionPlanesContext = {
    scene,
    camera,
    renderer,
    controls,
    interaction,
    bvhMeshes,
    getModelBoundingBox: () => modelBoundingBox,
    // the selection outline / overlay are cut by the section planes like the model
    getCachedMaterials: () => [...appearance.getCachedMaterials(), ...partSelection.clippableMaterials],
    getCanvas: () => canvasRef.value,
  };

  const sectionPlanes = useSectionPlanes(sectionPlanesContext);

  // Model appearance composable (draw modes, transparency, highlight dimming)
  appearance = useModelAppearance({
    loadedModel,
    updateMaterialClipping: () => sectionPlanes.updateMaterialClipping(),
    refreshAllCaps: () => sectionPlanes.refreshAllCaps(),
    getHiddenNodeIds: () => getHiddenNodeIds(),
    findNodeByInstanceId: (id) => findNodeByInstanceId(id),
    collectInstanceMeshes: (node) => collectInstanceMeshes(node),
    setMeshVisibility: (child, visible) => setMeshVisibility(child, visible),
    getSelectedInstanceNodes: () => partSelection.getSelectedNodes(),
    findParentInstanceNode: (obj) => findParentInstanceNode(obj),
  });

  // Part selection composable (outlines, overlays, selection map)
  const partSelection = usePartSelection({
    loadedModel,
    scene,
    detachTransformGizmo: () => transformGizmo.detachTransformGizmo(),
    refreshAllCaps: () => sectionPlanes.refreshAllCaps(),
    collectInstanceMeshes: (node) => collectInstanceMeshes(node),
    setMeshVisibility: (child, visible) => setMeshVisibility(child, visible),
  });
  // Measurement tools composable
  const measureContext: ThreeMeasureContext = {
    scene,
    camera,
    renderer,
    canvasEl: canvasRef,
    getHitInfo: (event: MouseEvent | PointerEvent) => {
      const intersects = raycastFromCursor(event);
      if (intersects.length === 0) return null;
      const hit = intersects[0];
      return {
        point: hit.point.clone(),
        normal: hit.face?.normal?.clone() ?? null,
        object: hit.object,
        faceIndex: hit.faceIndex ?? null,
      };
    },
    findParentInstanceNode: (obj) => findParentInstanceNode(obj),
    getFaceIdAtTriangle,
    getBrepFaceData,
    getBrepFaceTrianglePositions,
    findNearestBrepEdge,
    getBrepEdgeWorldPositions,
    getBrepEdgeData,
    computeMinEdgeToEdgeDistance,
    computeMinEdgeToFaceDistance,
  };
  const measureTools = useThreeMeasure(measureContext);

  // Callback the host can set to be notified when a measurement is created
  let externalMeasurementCreatedCb: (() => void) | null = null;

  // After a measurement is created, keep the tool active so the user can make
  // additional measurements without reactivating.
  measureTools.setOnMeasurementCreated(() => {
    externalMeasurementCreatedCb?.();
  });

  // Sequential hide mode composable
  // Wrap late-declared helpers in closures so they're evaluated lazily
  // (same pattern used by measureContext.getHitInfo → raycastFromCursor)
  const sequentialHideContext: SequentialHideContext = {
    canvasEl: canvasRef,
    camera,
    loadedModel,
    cursorLocked,
    interaction,
    raycastFromCursor: (event) => raycastFromCursor(event),
    findParentInstanceNode: (obj) => findParentInstanceNode(obj),
    setNodeVisibility: (nodeId, visible) => setNodeVisibility(nodeId, visible),
    refreshCaps: () => sectionPlanes.refreshAllCaps(),
    // onNodeHidden is set by the host (ProjectViewerView) after init
  };
  const sequentialHide = useSequentialHide(sequentialHideContext);

  // Viewport gizmo (orientation triad in corner)
  let viewportGizmo: ReturnType<typeof useViewportGizmo> | null = null;

  // Middle-click orbit pivot state
  let middlePointerDown = false;
  let middlePointerStart: THREE.Vector2 | null = null;
  let middlePivotSet = false;
  let pendingOrbitMarkerPoint: THREE.Vector3 | null = null;
  const MIDDLE_DRAG_THRESHOLD_PX = 3;

  // Left-click orbit drag tracking (suppresses click-to-select after orbit)
  let orbitDragged = false;
  let orbitStartX = 0;
  let orbitStartY = 0;
  const ORBIT_CLICK_SLOP_PX = 4;

  // Right-click drag tracking (suppresses context menu after pan)
  let rightPointerDown = false;
  let rightPointerStartX = 0;
  let rightPointerStartY = 0;
  const rightClickWasDrag = ref(false);
  const RIGHT_DRAG_THRESHOLD_PX = 4;

  const getOrbitMarkerPosition = () => {
    if (pendingOrbitMarkerPoint) return pendingOrbitMarkerPoint;
    if (controls.value) return controls.value.target;
    return null;
  };

  const updateOrbitMarkerScale = () => {
    if (!orbitMarker || !camera.value || !renderer.value) return;
    const distance = camera.value.position.distanceTo(orbitMarker.position);
    const worldPerPixel = getWorldPerPixel(distance);
    const targetPixels = 6;
    orbitMarker.scale.setScalar(worldPerPixel * targetPixels);
  };

  const showOrbitMarker = () => {
    if (!scene.value) return;
    if (!orbitMarker) {
      const geometry = new THREE.SphereGeometry(1, 16, 16);
      const accent = getComputedStyle(document.documentElement)
        .getPropertyValue("--accent-9")
        .trim();
      const material = new THREE.MeshBasicMaterial({
        color: accent
          ? new THREE.Color(cssColorToHex(accent))
          : new THREE.Color(0x333333),
      });
      orbitMarker = new THREE.Mesh(geometry, material);
      orbitMarker.renderOrder = 10;
      scene.value.add(orbitMarker);
    } else {
      const accent = getComputedStyle(document.documentElement)
        .getPropertyValue("--accent-9")
        .trim();
      if (accent) {
        (orbitMarker.material as THREE.MeshBasicMaterial).color.set(
          cssColorToHex(accent),
        );
      }
    }
    const markerPosition = getOrbitMarkerPosition();
    if (markerPosition) {
      orbitMarker.position.copy(markerPosition);
    }
    updateOrbitMarkerScale();
    orbitMarker.visible = true;
  };

  const hideOrbitMarker = () => {
    if (orbitMarker) {
      orbitMarker.visible = false;
    }
  };

  // ── Debug raycast visualisation helpers ──
  const debugObjects: THREE.Object3D[] = [];

  const placeDebugMarker = (
    point: THREE.Vector3,
    info: string = "",
  ): THREE.Mesh | null => {
    if (!scene.value || !camera.value) return null;
    const distance = camera.value.position.distanceTo(point);
    const markerSize = distance * DEBUG_MARKER_SIZE;
    const geometry = new THREE.SphereGeometry(markerSize, 16, 16);
    const material = new THREE.MeshBasicMaterial({
      color: DEBUG_MARKER_COLOR,
      depthTest: false,
    });
    const marker = new THREE.Mesh(geometry, material);
    marker.position.copy(point);
    marker.renderOrder = 999;
    marker.userData.debugInfo = info;
    marker.userData.isDebugMarker = true;
    scene.value.add(marker);
    debugObjects.push(marker);
    return marker;
  };

  const addDebugSphere = (
    point: THREE.Vector3,
    color: number,
    size: number,
  ) => {
    if (!scene.value) return;
    const geo = new THREE.SphereGeometry(size, 12, 12);
    const mat = new THREE.MeshBasicMaterial({
      color,
      depthTest: false,
      transparent: true,
      opacity: 0.85,
    });
    const m = new THREE.Mesh(geo, mat);
    m.position.copy(point);
    m.renderOrder = 999;
    m.raycast = () => {};
    scene.value.add(m);
    debugObjects.push(m);
  };

  const addDebugLine = (
    from: THREE.Vector3,
    to: THREE.Vector3,
    color: number,
  ) => {
    if (!scene.value) return;
    const geo = new THREE.BufferGeometry().setFromPoints([from, to]);
    const mat = new THREE.LineBasicMaterial({
      color,
      depthTest: false,
      linewidth: 2,
    });
    const line = new THREE.Line(geo, mat);
    line.renderOrder = 999;
    line.raycast = () => {};
    scene.value.add(line);
    debugObjects.push(line);
  };

  const clearDebugMarkers = () => {
    if (!scene.value) return;
    for (const obj of debugObjects) {
      scene.value.remove(obj);
      if ((obj as any).geometry) (obj as any).geometry.dispose();
      const mat = (obj as any).material;
      if (mat) {
        if (Array.isArray(mat)) mat.forEach((m: any) => m.dispose());
        else mat.dispose();
      }
    }
    debugObjects.length = 0;
  };

  const getPointFromCursorAtPlane = (
    event: MouseEvent | PointerEvent,
    planePoint: THREE.Vector3,
  ): THREE.Vector3 | null => {
    if (!camera.value) return null;
    const coords = getNormalizedCoords(event);
    raycaster.setFromCamera(coords, camera.value);

    const cameraDirection = new THREE.Vector3();
    camera.value.getWorldDirection(cameraDirection);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(
      cameraDirection,
      planePoint,
    );

    const point = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(plane, point)) return null;
    return point;
  };

  // Helper: normalize mouse/pointer coords to [-1, 1] range (optional out avoids alloc)
  const getNormalizedCoords = (
    event: MouseEvent | PointerEvent,
    out?: THREE.Vector2,
  ): THREE.Vector2 => {
    const domElement = renderer.value?.domElement ?? canvasRef.value;
    if (!domElement) return out ?? new THREE.Vector2();
    const rect = domElement.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const width = domElement.clientWidth || rect.width;
    const height = domElement.clientHeight || rect.height;
    const v = out ?? new THREE.Vector2();
    v.set((x / width) * 2 - 1, -(y / height) * 2 + 1);
    return v;
  };

  // Helper: check if a point is visible (not clipped by any cutting plane)
  // tolerance > 0 treats points within that distance of the plane as visible
  // (useful for pins placed on cap surfaces where floating-point puts them at distance ≈ 0)
  const isPointVisible = (
    point: THREE.Vector3,
    tolerance: number = 0,
  ): boolean => {
    const planeRefs = sectionPlanes.getPlaneRefs();
    // A point is clipped if plane.distanceToPoint(point) < -tolerance for any active plane
    if (
      planeRefs.x.value &&
      planeRefs.x.value.distanceToPoint(point) < -tolerance
    )
      return false;
    if (
      planeRefs.y.value &&
      planeRefs.y.value.distanceToPoint(point) < -tolerance
    )
      return false;
    if (
      planeRefs.z.value &&
      planeRefs.z.value.distanceToPoint(point) < -tolerance
    )
      return false;
    return true;
  };

  // Core: get raw intersection results (filtered by clipping planes, mesh-only)
  const raycastFromCursor = (
    event: MouseEvent | PointerEvent,
  ): THREE.Intersection[] => {
    if (!camera.value || !loadedModel.value || !scene.value) return [];

    // Force-refresh ALL world matrices (camera is NOT in the scene graph)
    scene.value.updateMatrixWorld(true);
    camera.value.updateMatrixWorld(true);
    camera.value.updateProjectionMatrix();

    const coords = getNormalizedCoords(event);
    raycaster.setFromCamera(coords, camera.value);
    raycaster.firstHitOnly = false;

    // Build array of raycast targets: model + all cap meshes
    const targets: THREE.Object3D[] = [loadedModel.value];
    const capResources = sectionPlanes.getCapResources();
    for (const resources of capResources.values()) {
      targets.push(resources.capMesh);
    }

    // Raycast against model and cap meshes
    const intersects = raycaster.intersectObjects(targets, true);

    // Build map of cap meshes to their axis for visibility checking
    const capMeshToAxis = new Map<THREE.Object3D, PlaneAxis>();
    for (const [axis, resources] of capResources.entries()) {
      capMeshToAxis.set(resources.capMesh, axis);
    }

    // Helper: check if point is visible, optionally excluding one plane
    const planeRefs = sectionPlanes.getPlaneRefs();
    const isPointVisibleExcept = (
      point: THREE.Vector3,
      excludeAxis?: PlaneAxis,
    ): boolean => {
      if (
        excludeAxis !== "x" &&
        planeRefs.x.value &&
        planeRefs.x.value.distanceToPoint(point) < 0
      )
        return false;
      if (
        excludeAxis !== "y" &&
        planeRefs.y.value &&
        planeRefs.y.value.distanceToPoint(point) < 0
      )
        return false;
      if (
        excludeAxis !== "z" &&
        planeRefs.z.value &&
        planeRefs.z.value.distanceToPoint(point) < 0
      )
        return false;
      return true;
    };

    // Filter results:
    // 1. Distance must be a finite number (NaN/Infinity from degenerate hits corrupt sort order)
    // 2. Only include Mesh objects (not Lines, Points, etc.)
    // 3. Must have a valid face (triangle hit, not vertex/edge)
    // 4. Must actually be visible (not hidden via Hide/Isolate)
    // 5. Must be visible (not clipped by cutting planes)
    return intersects.filter((hit) => {
      if (!Number.isFinite(hit.distance)) return false;

      // Only accept Mesh intersections with valid face data
      if (!(hit.object instanceof THREE.Mesh)) return false;
      if (!hit.face) return false; // No face = not a proper triangle hit

      // Skip hidden meshes (from Hide / Isolate actions)
      if (!hit.object.visible) return false;

      // Cap meshes: check visibility against OTHER planes (not their own)
      const capAxis = capMeshToAxis.get(hit.object);
      if (capAxis) {
        return isPointVisibleExcept(hit.point, capAxis);
      }

      // For model geometry, check all clipping planes
      return isPointVisible(hit.point);
    });
  };

  // Convenience: just get the 3D point (mesh hit only)
  const getPointUnderCursor = (
    event: MouseEvent | PointerEvent,
  ): THREE.Vector3 | null => {
    const intersects = raycastFromCursor(event);
    return intersects.length > 0 ? intersects[0].point.clone() : null;
  };

  /** Fast pivot point for middle-click orbit: first visible hit on model + caps (skips clipped geometry). */
  const getPointUnderCursorForPivot = (
    event: MouseEvent | PointerEvent,
  ): THREE.Vector3 | null => {
    if (!camera.value || !loadedModel.value) return null;
    scene.value?.updateMatrixWorld(true);
    camera.value.updateMatrixWorld(true);
    camera.value.updateProjectionMatrix();
    getNormalizedCoords(event, pivotCoords);
    raycaster.setFromCamera(pivotCoords, camera.value);
    raycaster.firstHitOnly = false;
    pivotIntersects.length = 0;
    const targets: THREE.Object3D[] = [loadedModel.value];
    const capResources = sectionPlanes.getCapResources();
    for (const resources of capResources.values()) {
      targets.push(resources.capMesh);
    }
    const capMeshToAxis = new Map<THREE.Object3D, PlaneAxis>();
    for (const [axis, resources] of capResources.entries()) {
      capMeshToAxis.set(resources.capMesh, axis);
    }
    raycaster.intersectObjects(targets, true, pivotIntersects);
    const planeRefs = sectionPlanes.getPlaneRefs();
    for (const hit of pivotIntersects) {
      if (!Number.isFinite(hit.distance)) continue;
      if (!(hit.object instanceof THREE.Mesh) || !hit.face) continue;
      if (!hit.object.visible) continue; // skip hidden meshes
      const capAxis = capMeshToAxis.get(hit.object);
      if (capAxis) {
        if (
          planeRefs.x.value &&
          capAxis !== "x" &&
          planeRefs.x.value.distanceToPoint(hit.point) < 0
        )
          continue;
        if (
          planeRefs.y.value &&
          capAxis !== "y" &&
          planeRefs.y.value.distanceToPoint(hit.point) < 0
        )
          continue;
        if (
          planeRefs.z.value &&
          capAxis !== "z" &&
          planeRefs.z.value.distanceToPoint(hit.point) < 0
        )
          continue;
      } else {
        if (!isPointVisible(hit.point)) continue;
      }
      return hit.point.clone();
    }
    return null;
  };

  // Convenience: get hit object
  const getObjectUnderCursor = (
    event: MouseEvent | PointerEvent,
  ): THREE.Object3D | null => {
    const intersects = raycastFromCursor(event);
    return intersects.length > 0 ? intersects[0].object : null;
  };

  // Convenience: get full hit info
  const getHitInfo = (event: MouseEvent | PointerEvent) => {
    const intersects = raycastFromCursor(event);
    if (intersects.length === 0) return null;
    const hit = intersects[0];
    return {
      point: hit.point.clone(),
      normal: hit.face?.normal?.clone() ?? null,
      object: hit.object,
      faceIndex: hit.faceIndex ?? null,
    };
  };

  const setupModelBVH = (model: THREE.Object3D) => {
    const meshes: THREE.Mesh[] = [];
    const materials = new Set<THREE.Material>();
    let vertexCount = 0;

    model.traverse((child) => {
      if (child.userData.isFatEdgeLine) {
        // Fat line edge — cache its material but don't add to BVH meshes
        child.renderOrder = 1;
        if ((child as any).material) {
          const mats = Array.isArray((child as any).material)
            ? (child as any).material
            : [(child as any).material];
          mats.forEach((m: THREE.Material) => materials.add(m));
        }
      } else if (child instanceof THREE.Mesh) {
        const geometry = child.geometry as THREE.BufferGeometry & {
          boundsTree?: unknown;
        };

        // Expand the geometry bounding box so Three.js's Ray.intersectsBox
        // (slab method, zero epsilon) doesn't produce false negatives for
        // rays nearly parallel to a thin box face — common with planar CAD
        // geometry. Only affects the non-BVH fallback path; the BVH path
        // is covered by padBVHBounds above.
        // Geometry is shared by every instance of a part, so pad it only once
        // (padding per mesh compounds into an enormous box on big assemblies).
        if (!geometry.boundingBox) geometry.computeBoundingBox();
        if (geometry.boundingBox && !paddedGeometries.has(geometry)) {
          paddedGeometries.add(geometry);
          const size = geometry.boundingBox.getSize(new THREE.Vector3());
          const diag = size.length();
          const pad = Math.max(diag * 1e-2, 0.1);
          geometry.boundingBox.expandByScalar(pad);
        }

        if (!geometry.boundsTree) {
          const bvh = new MeshBVH(geometry, { maxLeafSize: 10, strategy: SAH });
          padBVHBounds(bvh);
          (geometry as any).boundsTree = bvh;
        }
        meshes.push(child);
        child.renderOrder = 1;
        // Count vertices for performance overlay
        if (geometry.attributes.position) {
          vertexCount += geometry.attributes.position.count;
        }
        // Cache materials for fast clipping updates
        if (child.material) {
          const mats = Array.isArray(child.material)
            ? child.material
            : [child.material];
          mats.forEach((m) => materials.add(m));
        }
      }
    });

    bvhMeshes.value = meshes;
    appearance.initCachedMaterials(Array.from(materials));
    performanceStats.vertices = vertexCount;
    performanceStats.meshes = meshes.length;
  };

  /**
   * Warm up BVH traversal to trigger JIT compilation and fill CPU caches.
   * This runs asynchronously after model load to avoid blocking.
   * Note: The actual BVH traversal warmup is minimal now that computation is in a worker.
   */
  const warmUpBVH = async () => {
    if (!bvhMeshes.value.length || !modelBoundingBox) return;

    console.log(
      "[BVH] Warm-up: BVH ready with",
      bvhMeshes.value.length,
      "meshes",
    );
  };

  // Pointer handlers - delegate cutting plane dragging to sectionPlanes composable
  const handlePointerDown = (event: PointerEvent) => {
    // Viewport gizmo consumes left clicks in its area.
    if (event.button === 0 && viewportGizmo?.handlePointerDown(event)) {
      event.stopImmediatePropagation();
      return;
    }
    // First try cutting plane drag (left click)
    if (event.button === 0) {
      if (sectionPlanes.handlePointerDown(event)) {
        return;
      }
    }
    // Left/middle click → orbit rotation.
    // When the transform gizmo owns left-click, suppress orbit for button 0.
    const gizmoActive = transformGizmo.isGizmoActive();
    if (
      (event.button === 0 || event.button === 1) &&
      controls.value &&
      interaction.isAllowed("orbit")
    ) {
      if (event.button === 0 && gizmoActive) {
        // Left-click reserved for gizmo
      } else {
        controls.value.startRotate(event.clientX, event.clientY);
        orbitStartX = event.clientX;
        orbitStartY = event.clientY;
      }
    }
    // Right click → pan + drag detection for context menu suppression
    if (event.button === 2) {
      rightPointerDown = true;
      rightClickWasDrag.value = false;
      rightPointerStartX = event.clientX;
      rightPointerStartY = event.clientY;
      if (controls.value && interaction.isAllowed("pan")) {
        controls.value.startPan(event.clientX, event.clientY);
      }
    }
    // Middle click → set orbit pivot (deferred until drag threshold)
    if (event.button === 1) {
      middlePointerDown = true;
      middlePivotSet = false;
      middlePointerStart = new THREE.Vector2(event.clientX, event.clientY);
      const hitPoint = getPointUnderCursorForPivot(event);
      if (hitPoint) {
        pendingOrbitMarkerPoint =
          getPointFromCursorAtPlane(event, hitPoint) ?? hitPoint;
      } else {
        pendingOrbitMarkerPoint = controls.value
          ? controls.value.target.clone()
          : null;
      }
      showOrbitMarker();
    }
  };

  const handlePointerMove = (event: PointerEvent) => {
    // Viewport gizmo hover (visual feedback + cursor)
    if (viewportGizmo) viewportGizmo.handlePointerMove(event);

    // Orbit rotation — delay during middle-click until the pivot is committed
    if (controls.value && (!middlePointerDown || middlePivotSet)) {
      if (controls.value.rotateByPointer(event.clientX, event.clientY)) {
        // a hand wobble of a pixel or two while clicking is still a click (it must not swallow a face pick)
        const dx = event.clientX - orbitStartX;
        const dy = event.clientY - orbitStartY;
        if (dx * dx + dy * dy >= ORBIT_CLICK_SLOP_PX * ORBIT_CLICK_SLOP_PX) orbitDragged = true;
      }
    }

    // Right-click pan
    if (controls.value) {
      controls.value.panByPointer(event.clientX, event.clientY);
    }

    // Right-click drag detection for context menu suppression
    if (rightPointerDown && !rightClickWasDrag.value) {
      const dx = event.clientX - rightPointerStartX;
      const dy = event.clientY - rightPointerStartY;
      if (
        dx * dx + dy * dy >=
        RIGHT_DRAG_THRESHOLD_PX * RIGHT_DRAG_THRESHOLD_PX
      ) {
        rightClickWasDrag.value = true;
      }
    }

    // Middle-click pivot commit after drag threshold
    if (middlePointerDown && !middlePivotSet && middlePointerStart) {
      const deltaX = event.clientX - middlePointerStart.x;
      const deltaY = event.clientY - middlePointerStart.y;
      const distanceSq = deltaX * deltaX + deltaY * deltaY;
      if (distanceSq >= MIDDLE_DRAG_THRESHOLD_PX * MIDDLE_DRAG_THRESHOLD_PX) {
        const hitPoint = getPointUnderCursorForPivot(event);
        if (hitPoint) {
          const alignedPoint =
            getPointFromCursorAtPlane(event, hitPoint) ?? hitPoint;
          if (controls.value) {
            controls.value.setOrbitCenter(alignedPoint);
          }
        }
        pendingOrbitMarkerPoint = null;
        middlePivotSet = true;
        if (controls.value) {
          controls.value.resetRotateOrigin(event.clientX, event.clientY);
        }
      }
    }
    // Delegate to section planes composable
    sectionPlanes.handlePointerMove(event);

    // ── Hover outline (throttled) ──
    // Skip during any drag operation (any button held); clear stale outline
    if (event.buttons !== 0) {
      if (partSelection.getHoveredNode()) {
        partSelection.clearHover();
        const hoverCanvas = canvasRef.value;
        if (hoverCanvas && !cursorLocked.value)
          hoverCanvas.style.cursor = "default";
      }
      return;
    }

    if (!interaction.isAllowed("partHover")) return;

    // Throttle hover raycast to avoid per-pixel cost
    const hoverNow = performance.now();
    if (hoverNow - lastHoverRaycastTime < HOVER_RAYCAST_THROTTLE_MS) return;
    lastHoverRaycastTime = hoverNow;

    if (!loadedModel.value || !camera.value) return;

    raycaster.firstHitOnly = false;
    camera.value.updateMatrixWorld(true);
    camera.value.updateProjectionMatrix();
    const hoverCoords = getNormalizedCoords(event);
    raycaster.setFromCamera(hoverCoords, camera.value);
    const hoverHits = raycaster.intersectObjects([loadedModel.value], true);

    const hoverHit = hoverHits.find(
      (h) =>
        Number.isFinite(h.distance) &&
        h.object instanceof THREE.Mesh &&
        h.object.visible &&
        h.face &&
        isPointVisible(h.point),
    );
    const hoverCanvas = canvasRef.value;

    if (hoverHit) {
      const instanceNode = findParentInstanceNode(hoverHit.object);
      if (instanceNode && instanceNode !== partSelection.getHoveredNode()) {
        partSelection.setHover(instanceNode);
      }
      if (hoverCanvas && !cursorLocked.value)
        hoverCanvas.style.cursor = "pointer";
    } else {
      partSelection.clearHover();
      if (hoverCanvas && !cursorLocked.value)
        hoverCanvas.style.cursor = "default";
    }
  };

  const handlePointerUp = (_event: PointerEvent) => {
    sectionPlanes.handlePointerUp();
    if (orbitDragged) {
      interaction.consumeNextClick();
    }
    orbitDragged = false;
    if (controls.value) {
      controls.value.endRotate();
      controls.value.endPan();
    }
    rightPointerDown = false;
    middlePointerDown = false;
    middlePointerStart = null;
    middlePivotSet = false;
    pendingOrbitMarkerPoint = null;
    hideOrbitMarker();
  };

  const handlePointerLeave = () => {
    partSelection.clearHover();
    const canvas = canvasRef.value;
    if (canvas && !cursorLocked.value) canvas.style.cursor = "default";
  };

  // Click handler — selection + optional debug markers
  const handleClick = (event: MouseEvent) => {
    if (interaction.isClickConsumed()) return;

    if (!interaction.isAllowed("partSelect")) return;

    // ── Left-click selection (when not in debug mode) ──
    if (!raycastDebugEnabled.value) {
      if (!loadedModel.value || !camera.value) return;

      const isCtrl = event.ctrlKey || event.metaKey;
      const selectIntersects = raycastFromCursor(event);

      if (selectIntersects.length > 0) {
        const instanceNode = findParentInstanceNode(selectIntersects[0].object);
        if (instanceNode) {
          if (isCtrl) {
            if (partSelection.isSelected(instanceNode.id)) {
              partSelection.removeFromSelection(instanceNode.id);
            } else {
              partSelection.addToSelection(instanceNode);
              partSelection.setSelectedInstanceNode(instanceNode);
            }
          } else if (
            partSelection.getSelectedNodeIds().length !== 1 ||
            partSelection.getSelectedNodeIds()[0] !== instanceNode.id
          ) {
            // A plain click selects exactly the part under the cursor. (The original viewer climbed to the
            // parent assembly when the selected part was clicked again; assemblies are picked in the model tree.)
            partSelection.clearSelectionHighlight();
            partSelection.addToSelection(instanceNode);
            partSelection.setSelectedInstanceNode(instanceNode);
            if (partSelection.getHoveredNode() === instanceNode) {
              partSelection.clearHover();
            }
          }
        }
      } else if (!isCtrl) {
        partSelection.clearSelectionHighlight();
      }
      // Ctrl+Click on empty space → do nothing
      return;
    }

    // ── Debug raycast mode ──
    if (!camera.value || !loadedModel.value || !scene.value) return;
    performDebugRaycast(event, {
      camera: camera.value,
      scene: scene.value,
      loadedModel: loadedModel.value,
      raycaster,
      bvhMeshes: bvhMeshes.value,
      modelBoundingBox,
      getNormalizedCoords,
      raycastFromCursor,
      isPointVisible,
      getCapResources: () => sectionPlanes.getCapResources(),
      addDebugLine,
      addDebugSphere,
      clearDebugMarkers,
      debugObjects,
    });
  };

  // Zoom-to-cursor handler - accumulates velocity, actual zoom applied in render loop
  const handleWheel = (event: WheelEvent) => {
    event.preventDefault();

    if (!camera.value || !controls.value) return;

    // Block zoom while gizmo animation is running
    if (viewportGizmo?.isAnimating) return;

    // Zoom factor - negative deltaY = zoom in, positive = zoom out
    const zoomSpeed = 0.001;
    const direction = zoomInverted.value ? 1 : -1;
    const delta = direction * event.deltaY * zoomSpeed;

    cachedZoomNDC = getNormalizedCoords(event);

    // Accumulate velocity (adds to existing momentum)
    zoomVelocity += delta;
  };

  const removeInputHandlers = () => {
    const canvas = canvasRef.value;
    if (!canvas) return;
    canvas.removeEventListener("pointerdown", handlePointerDown);
    canvas.removeEventListener("pointermove", handlePointerMove);
    canvas.removeEventListener("pointerup", handlePointerUp);
    canvas.removeEventListener("pointerleave", handlePointerLeave);
    canvas.removeEventListener("wheel", handleWheel);
    canvas.removeEventListener("click", handleClick);
  };

  const addInputHandlers = () => {
    const canvas = canvasRef.value;
    if (!canvas) return;
    canvas.addEventListener("pointerdown", handlePointerDown);
    canvas.addEventListener("pointermove", handlePointerMove);
    canvas.addEventListener("pointerup", handlePointerUp);
    canvas.addEventListener("pointerleave", handlePointerLeave);
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    canvas.addEventListener("click", handleClick);
  };

  /**
   * Initialize the Three.js scene, camera, renderer, and controls
   */
  const initScene = () => {
    console.log("[useThreeViewer] initScene called");
    const canvas = canvasRef.value;
    if (!canvas) {
      console.error("[useThreeViewer] Canvas element not found");
      error.value = "Canvas element not found";
      return false;
    }
    if (!acceleratedRaycastInstalled) {
      (THREE as any).Mesh.prototype.raycast = acceleratedRaycast;
      acceleratedRaycastInstalled = true;
    }
    console.log("[useThreeViewer] Canvas found:", canvas);

    // Create scene
    const newScene = new THREE.Scene();
    newScene.background = new THREE.Color(0x1a1a2e);
    scene.value = newScene;
    setSceneBackground("#1a1a2e");
    console.log("[useThreeViewer] Scene created");

    // Get container dimensions
    const container = canvas.parentElement;
    const width = container?.clientWidth ?? canvas.clientWidth;
    const height = container?.clientHeight ?? canvas.clientHeight;

    // Create camera (type depends on currentProjection setting)
    const newCamera = createCamera(width, height);
    camera.value = newCamera;

    // Create renderer
    const newRenderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      stencil: true,
    });
    newRenderer.setSize(width, height);
    newRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    newRenderer.outputColorSpace = THREE.SRGBColorSpace;
    newRenderer.toneMapping = THREE.ACESFilmicToneMapping;
    newRenderer.toneMappingExposure = 1.0;
    newRenderer.localClippingEnabled = true; // Enable clipping planes
    renderer.value = newRenderer;

    // Attach custom input handlers BEFORE controls so our listeners fire first
    // (allows gizmo clicks to stopImmediatePropagation before controls see them)
    addInputHandlers();

    // Create controls based on current control type setting
    const newControls = createControls(
      currentControlType.value,
      newCamera,
      canvas,
    );
    controls.value = newControls;

    // Add lighting
    setupLighting(newScene);

    // Create viewport gizmo (orientation triad in corner)
    viewportGizmo = useViewportGizmo({
      renderer,
      camera,
      controls,
      canvasRef,
      cursorLocked,
      onRequestControlsRecreate: () => {
        const canvas = canvasRef.value;
        const cam = camera.value;
        if (!canvas || !cam) return;

        const newControls = createControls(
          currentControlType.value,
          cam,
          canvas,
        );
        controls.value = newControls;
      },
      onAnimationComplete: () => {
        cachedZoomNDC = null;
        zoomVelocity = 0;
        if (controls.value) {
          controls.value.syncFromCamera();
        }
      },
    });

    // Setup resize observer
    setupResizeObserver(container ?? canvas);

    // Start render loop
    startRenderLoop();

    status.value = "Viewer ready";
    viewerReady.value = true;
    console.log(
      "[useThreeViewer] Scene initialized successfully, viewerReady =",
      viewerReady.value,
    );
    return true;
  };

  /**
   * Create controls for the given camera and canvas.
   */
  const createControls = (
    _type: ControlType,
    cam: THREE.PerspectiveCamera | THREE.OrthographicCamera,
    canvas: HTMLCanvasElement,
  ): CadOrbitControls => {
    const ctrl = new CadOrbitControls(cam, canvas);
    ctrl.turntable = _type === "custom";
    return ctrl;
  };

  /**
   * Switch between trackball and orbit controls.
   * Rebuilds the viewer stack and re-fits the camera to the model.
   * @param type - The control type to switch to ('custom' or 'customTrackball')
   */
  const setControlType = (type: ControlType): boolean => {
    if (type === currentControlType.value) {
      console.log(`[useThreeViewer] Controls already set to ${type}`);
      return true;
    }

    return resetViewerStack(type);
  };

  /**
   * Switch between perspective and orthographic projection while preserving
   * the current view pose (position, target, up). Objects at the orbit target
   * appear the same size in both projections.
   */
  const switchProjection = (to: "perspective" | "orthographic"): void => {
    const cam = camera.value;
    const ctrl = controls.value;
    if (!cam || !ctrl || !renderer.value) return;

    const canvas = canvasRef.value;
    if (!canvas) return;

    const distance = cam.position.distanceTo(ctrl.target);
    const position = cam.position.clone();
    const target = ctrl.target.clone();
    const quat = cam.quaternion.clone();
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(quat);
    const w = renderer.value.domElement.clientWidth || 1;
    const h = renderer.value.domElement.clientHeight || 1;
    const aspect = w / h;

    let newCam: THREE.PerspectiveCamera | THREE.OrthographicCamera;

    if (to === "orthographic") {
      // Compute frustum that matches the current perspective framing at the target distance
      const perspFov =
        cam instanceof THREE.PerspectiveCamera ? cam.fov : currentFov.value;
      const halfH = distance * Math.tan(THREE.MathUtils.degToRad(perspFov) / 2);
      // Ortho needs very generous near/far (negative near) so nothing clips during orbit
      const orthoNear = -cam.far;
      const orthoFar = cam.far;
      newCam = new THREE.OrthographicCamera(
        -halfH * aspect,
        halfH * aspect,
        halfH,
        -halfH,
        orthoNear,
        orthoFar,
      );
    } else {
      // Restore sensible perspective near/far (ortho may have had negative near)
      const perspNear = cam.far / 100000;
      newCam = new THREE.PerspectiveCamera(
        currentFov.value,
        aspect,
        perspNear,
        cam.far,
      );
    }

    newCam.position.copy(position);
    newCam.quaternion.copy(quat);
    newCam.up.copy(up);
    camera.value = newCam;

    // Recreate controls with the new camera
    if (ctrl) ctrl.dispose();
    controls.value = null;

    pendingOrbitMarkerPoint = null;
    hideOrbitMarker();
    middlePointerDown = false;
    middlePointerStart = null;
    middlePivotSet = false;
    cachedZoomNDC = null;
    zoomVelocity = 0;

    const newControls = createControls(
      currentControlType.value,
      newCam,
      canvas,
    );
    newControls.target.copy(target);
    newControls.syncFromCamera();
    controls.value = newControls;

    console.log(`[useThreeViewer] Switched projection to ${to}`);
  };

  // ---- Camera state capture / restore --------------------------------------

  /**
   * Capture the current camera view as a serializable object.
   * Returns `null` if the camera or controls are not yet initialised.
   */
  const getCameraState = (): CameraViewState | null => {
    const cam = camera.value;
    const ctrl = controls.value;
    if (!cam || !ctrl) return null;

    const pos = cam.position;
    const tgt = ctrl.target;
    const q = cam.quaternion;
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);

    const isOrtho = cam instanceof THREE.OrthographicCamera;
    const vw = renderer.value?.domElement.clientWidth || 1;
    const vh = renderer.value?.domElement.clientHeight || 1;
    return {
      position: { x: pos.x, y: pos.y, z: pos.z },
      target: { x: tgt.x, y: tgt.y, z: tgt.z },
      up: { x: up.x, y: up.y, z: up.z },
      quaternion: { x: q.x, y: q.y, z: q.z, w: q.w },
      projection: currentProjection.value,
      fov: currentFov.value,
      orthoZoom: isOrtho ? cam.zoom : null,
      orthoHalfH: isOrtho ? (cam.top - cam.bottom) / 2 : null,
      aspect: vw / vh,
    };
  };

  /**
   * Restore a previously captured camera view state.
   * Handles projection switching, frustum recomputation, and control sync.
   * @param animateDuration  If > 0, smoothly animate to the new state over this many seconds.
   */
  let _cameraAnimId: number | null = null; // cancel any in-flight animation
  let _cameraAnimReject: (() => void) | null = null;

  const restoreCameraState = (
    state: CameraViewState,
    animateDuration = 0,
  ): Promise<void> => {
    if (!camera.value || !controls.value || !renderer.value)
      return Promise.resolve();

    // Cancel any running camera animation
    if (_cameraAnimId !== null) {
      cancelAnimationFrame(_cameraAnimId);
      _cameraAnimId = null;
    }
    // Reject any pending animation promise
    if (_cameraAnimReject) {
      _cameraAnimReject();
      _cameraAnimReject = null;
    }

    // Switch projection if needed (this recreates camera + controls)
    if (state.projection !== currentProjection.value) {
      currentProjection.value = state.projection;
      switchProjection(state.projection);
    }

    const cam = camera.value!;
    const ctrl = controls.value!;

    // Target values
    const endPos = new THREE.Vector3(
      state.position.x,
      state.position.y,
      state.position.z,
    );
    const endTgt = new THREE.Vector3(
      state.target.x,
      state.target.y,
      state.target.z,
    );
    const endUp = new THREE.Vector3(state.up.x, state.up.y, state.up.z);
    const endZoom = state.orthoZoom ?? 1;
    const endOrthoHalfH = state.orthoHalfH ?? null;

    // Prefer stored quaternion for lossless orientation; fall back to lookAt
    const hasQuat = state.quaternion != null;
    const endQuat = hasQuat
      ? new THREE.Quaternion(
          state.quaternion!.x,
          state.quaternion!.y,
          state.quaternion!.z,
          state.quaternion!.w,
        )
      : null;

    /** Compute ortho frustum half-height. Uses stored value when available,
     *  falls back to distance * tan(fov/2) for legacy data. */
    const orthoHalfHFrom = (
      pos: THREE.Vector3,
      tgt: THREE.Vector3,
      fov: number,
      storedHalfH: number | null,
    ): number => {
      if (storedHalfH != null && storedHalfH > 0) return storedHalfH;
      const distance = pos.distanceTo(tgt);
      return distance * Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    };

    // ── Contain-fit: if the viewer's screen is narrower than the author's,
    // expand the vertical extent so nothing the author saw is cropped. ──
    const viewerW = renderer.value!.domElement.clientWidth || 1;
    const viewerH = renderer.value!.domElement.clientHeight || 1;
    const viewerAspect = viewerW / viewerH;
    const storedAspect = state.aspect ?? viewerAspect; // legacy data: no adjustment

    let endFov = state.fov;
    if (viewerAspect < storedAspect) {
      // Perspective: widen vertical FOV to preserve horizontal FOV
      const hFovRad =
        2 *
        Math.atan(
          Math.tan(THREE.MathUtils.degToRad(state.fov) / 2) * storedAspect,
        );
      endFov = THREE.MathUtils.radToDeg(
        2 * Math.atan(Math.tan(hFovRad / 2) / viewerAspect),
      );
    }

    /** Set camera orientation from quaternion (lossless) or via lookAt (legacy fallback). */
    const applyOrientation = (
      quat: THREE.Quaternion | null,
      up: THREE.Vector3,
      target: THREE.Vector3,
    ) => {
      if (quat) {
        cam.quaternion.copy(quat);
        cam.up.set(0, 1, 0).applyQuaternion(quat);
      } else {
        cam.up.copy(up);
        cam.lookAt(target);
      }
    };

    /** Apply the final pose (shared by snap & last frame of animation). */
    const applyFinalPose = () => {
      cam.position.copy(endPos);
      ctrl.target.copy(endTgt);
      if (cam instanceof THREE.PerspectiveCamera) {
        cam.fov = endFov;
        currentFov.value = endFov;
      } else if (cam instanceof THREE.OrthographicCamera) {
        let halfH = orthoHalfHFrom(endPos, endTgt, endFov, endOrthoHalfH);
        if (viewerAspect < storedAspect) {
          halfH *= storedAspect / viewerAspect;
        }
        const aspect = viewerAspect;
        cam.left = -halfH * aspect;
        cam.right = halfH * aspect;
        cam.top = halfH;
        cam.bottom = -halfH;
        cam.zoom = endZoom;
        currentFov.value = endFov;
      }
      cam.updateProjectionMatrix();
      applyOrientation(endQuat, endUp, ctrl.target);
      ctrl.syncFromCamera();
    };

    // ── Snap (no animation) ──
    if (animateDuration <= 0) {
      applyFinalPose();
      return Promise.resolve();
    }

    // ── Animated transition ──
    return new Promise<void>((resolve, reject) => {
      _cameraAnimReject = reject; // so a subsequent call can cancel us

      const startPos = cam.position.clone();
      const startTgt = ctrl.target.clone();
      const startCamQuat = cam.quaternion.clone();
      const startFov =
        cam instanceof THREE.PerspectiveCamera ? cam.fov : endFov;
      const startZoom =
        cam instanceof THREE.OrthographicCamera ? cam.zoom : endZoom;
      const startHalfH =
        cam instanceof THREE.OrthographicCamera
          ? (cam.top - cam.bottom) / 2
          : 0;
      let endHalfH =
        cam instanceof THREE.OrthographicCamera
          ? orthoHalfHFrom(endPos, endTgt, endFov, endOrthoHalfH)
          : 0;
      if (
        cam instanceof THREE.OrthographicCamera &&
        viewerAspect < storedAspect
      ) {
        endHalfH *= storedAspect / viewerAspect;
      }
      const durationMs = animateDuration * 1000;
      const t0 = performance.now();

      const startDist = startPos.distanceTo(startTgt);
      const endDist = endPos.distanceTo(endTgt);

      // Resolve a quaternion for slerp even when one wasn't stored (legacy).
      const endQuatResolved =
        endQuat ??
        (() => {
          const m = new THREE.Matrix4().lookAt(endPos, endTgt, endUp);
          return new THREE.Quaternion().setFromRotationMatrix(m);
        })();

      const _tmpInterpTgt = new THREE.Vector3();
      const _tmpFwd = new THREE.Vector3();

      const ease = (t: number) => t * t * (3 - 2 * t);

      const tick = () => {
        const elapsed = performance.now() - t0;
        const raw = Math.min(elapsed / durationMs, 1);
        const t = ease(raw);

        // Orientation first — everything else derives from it
        cam.quaternion.slerpQuaternions(startCamQuat, endQuatResolved, t);
        cam.up.set(0, 1, 0).applyQuaternion(cam.quaternion);

        // Target: straight lerp
        _tmpInterpTgt.lerpVectors(startTgt, endTgt, t);
        ctrl.target.copy(_tmpInterpTgt);

        // Position: derived from the slerped orientation so the camera
        // always sits behind its own forward vector at the correct distance.
        const interpDist = THREE.MathUtils.lerp(startDist, endDist, t);
        _tmpFwd.set(0, 0, -1).applyQuaternion(cam.quaternion);
        cam.position.copy(_tmpInterpTgt).addScaledVector(_tmpFwd, -interpDist);

        if (cam instanceof THREE.PerspectiveCamera) {
          cam.fov = THREE.MathUtils.lerp(startFov, endFov, t);
          currentFov.value = cam.fov;
        } else if (cam instanceof THREE.OrthographicCamera) {
          const fov = THREE.MathUtils.lerp(startFov, endFov, t);
          const zoom = THREE.MathUtils.lerp(startZoom, endZoom, t);
          const halfH = THREE.MathUtils.lerp(startHalfH, endHalfH, t);
          const w = renderer.value!.domElement.clientWidth || 1;
          const h = renderer.value!.domElement.clientHeight || 1;
          const aspect = w / h;
          cam.left = -halfH * aspect;
          cam.right = halfH * aspect;
          cam.top = halfH;
          cam.bottom = -halfH;
          cam.zoom = zoom;
          currentFov.value = fov;
        }

        cam.updateProjectionMatrix();
        ctrl.syncFromCamera();

        if (raw < 1) {
          _cameraAnimId = requestAnimationFrame(tick);
        } else {
          _cameraAnimId = null;
          _cameraAnimReject = null;
          resolve();
        }
      };

      _cameraAnimId = requestAnimationFrame(tick);
    });
  };

  /**
   * Set the projection type. If it changed, switches the camera live.
   */
  const setProjectionType = (type: "perspective" | "orthographic"): void => {
    if (type === currentProjection.value) return;
    currentProjection.value = type;
    if (camera.value && controls.value) {
      switchProjection(type);
    }
  };

  /**
   * Set the perspective camera FOV (live update if currently in perspective mode).
   */
  const setViewerFov = (fov: number): void => {
    currentFov.value = Math.max(10, Math.min(120, Math.round(fov)));
    if (camera.value instanceof THREE.PerspectiveCamera) {
      camera.value.fov = currentFov.value;
      camera.value.updateProjectionMatrix();
    }
  };

  /**
   * Set the zoom direction inversion preference (takes effect immediately).
   */
  const setZoomInverted = (inverted: boolean): void => {
    zoomInverted.value = inverted;
  };

  /**
   * Relative luminance (WCAG definition) from a hex colour string.
   * Parses hex directly to avoid THREE.Color linear-space ambiguity.
   */
  const relativeLuminance = (hex: string): number => {
    const n = parseInt(hex.replace("#", ""), 16);
    const r = ((n >> 16) & 0xff) / 255;
    const g = ((n >> 8) & 0xff) / 255;
    const b = (n & 0xff) / 255;
    const toLinear = (v: number) =>
      v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
  };

  /**
   * Update the scene background colour (takes effect immediately).
   * Also sets viewport contrast variables (`--vp-con-high/med/low`) on the
   * viewer wrapper so CSS overlays can maintain contrast against the background.
   */
  const setSceneBackground = (hex: string): void => {
    if (scene.value) {
      scene.value.background = new THREE.Color(hex);
    }
    const wrapper = canvasRef.value?.parentElement;
    if (wrapper) {
      wrapper.style.setProperty("--viewport-bg", hex);
      const light = relativeLuminance(hex) > 0.18;
      wrapper.style.setProperty(
        "--vp-con-high",
        `var(--neutral-${light ? 5 : 11})`,
      );
      wrapper.style.setProperty(
        "--vp-con-med",
        `var(--neutral-${light ? 6 : 9})`,
      );
      wrapper.style.setProperty(
        "--vp-con-low",
        `var(--neutral-${light ? 9 : 6})`,
      );
    }
  };

  /**
   * Rebuild camera/renderer/controls without reloading model.
   * This ensures a deterministic control setup on switch.
   */
  const resetViewerStack = (type: ControlType): boolean => {
    const canvas = canvasRef.value;
    const existingScene = scene.value;
    if (!canvas || !existingScene) {
      console.error(
        "[useThreeViewer] Cannot reset viewer stack: canvas or scene not initialized",
      );
      return false;
    }

    const container = canvas.parentElement;
    const width = container?.clientWidth ?? canvas.clientWidth;
    const height = container?.clientHeight ?? canvas.clientHeight;

    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }

    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }

    removeInputHandlers();

    if (controls.value) {
      controls.value.dispose();
      controls.value = null;
    }

    if (renderer.value) {
      renderer.value.dispose();
      renderer.value = null;
    }

    const newCamera = createCamera(width, height);
    camera.value = newCamera;

    const newRenderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      stencil: true,
    });
    newRenderer.setSize(width, height);
    newRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    newRenderer.outputColorSpace = THREE.SRGBColorSpace;
    newRenderer.toneMapping = THREE.ACESFilmicToneMapping;
    newRenderer.toneMappingExposure = 1.0;
    newRenderer.localClippingEnabled = true;
    renderer.value = newRenderer;

    pendingOrbitMarkerPoint = null;
    hideOrbitMarker();
    middlePointerDown = false;
    middlePointerStart = null;
    middlePivotSet = false;
    cachedZoomNDC = null;
    zoomVelocity = 0;

    // Attach input handlers BEFORE controls so our listeners fire first
    addInputHandlers();

    const newControls = createControls(type, newCamera, canvas);
    controls.value = newControls;
    currentControlType.value = type;
    setupResizeObserver(container ?? canvas);

    if (loadedModel.value) {
      fitCameraToModel(loadedModel.value);
    }

    startRenderLoop();

    console.log(`[useThreeViewer] Reset viewer stack for ${type} controls`);
    return true;
  };

  /**
   * Setup scene lighting
   */
  const setupLighting = (targetScene: THREE.Scene) => {
    // Ambient light for overall illumination
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
    targetScene.add(ambientLight);

    // Main directional light (sun-like)
    const directionalLight = new THREE.DirectionalLight(0xffffff, 1);
    directionalLight.position.set(5, 10, 7.5);
    directionalLight.castShadow = false;
    targetScene.add(directionalLight);

    // Fill light from opposite side
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.3);
    fillLight.position.set(-5, 5, -5);
    targetScene.add(fillLight);

    // Hemisphere light for sky/ground ambient
    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.3);
    hemiLight.position.set(0, 20, 0);
    targetScene.add(hemiLight);
  };

  /**
   * Setup resize observer to handle container size changes
   */
  const setupResizeObserver = (container: HTMLElement) => {
    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        // Don't call handleResize directly — that would clear the GPU buffer
        // between render frames, causing a visible white flash.  Instead, stash
        // the new dimensions so the render loop can apply + render atomically.
        pendingResize = { width, height };
      }
    });
    resizeObserver.observe(container);
  };

  /**
   * Handle resize events
   */
  const handleResize = (width: number, height: number) => {
    if (!camera.value || !renderer.value) return;

    const cam = camera.value;

    if (cam instanceof THREE.PerspectiveCamera) {
      cam.aspect = width / height;
    } else {
      const ortho = cam as THREE.OrthographicCamera;
      const halfH = (ortho.top - ortho.bottom) / 2;
      const halfW = halfH * (width / height);
      ortho.left = -halfW;
      ortho.right = halfW;
    }
    cam.updateProjectionMatrix();
    renderer.value.setSize(width, height);

    // Update LineMaterial resolution for fat lines — section plane caps
    const capResources = sectionPlanes.getCapResources();
    for (const [, resources] of capResources) {
      const edgeObj = resources.edgeLines as THREE.Mesh;
      const lineMat = edgeObj.material as THREE.ShaderMaterial;
      if ("resolution" in lineMat && lineMat.resolution) {
        (lineMat.resolution as THREE.Vector2).set(width, height);
      }
    }

    // Update LineMaterial resolution for fat lines — model edges
    for (const mat of modelEdgeLineMaterials) {
      mat.resolution.set(width, height);
    }

    // Resize measurement CSS2D overlay
    measureTools.resize(width, height);
  };

  /**
   * Start the render loop
   */
  const startRenderLoop = () => {
    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);

      // Apply any pending resize before rendering so the buffer reallocation
      // and the draw call happen in the same frame — no white flash.
      if (pendingResize) {
        const { width, height } = pendingResize;
        pendingResize = null;
        if (width > 0 && height > 0) {
          handleResize(width, height);
        }
      }

      // Calculate FPS
      const now = performance.now();
      const frameTime = now - lastFrameTime;
      lastFrameTime = now;
      frameCount++;
      fpsAccumulator += frameTime;

      // Update FPS display every 500ms for smoother readout
      if (now - lastFpsUpdate >= 500) {
        const avgFrameTime = fpsAccumulator / frameCount;
        performanceStats.fps = Math.round(1000 / avgFrameTime);
        performanceStats.frameTimeMs = avgFrameTime;
        frameCount = 0;
        fpsAccumulator = 0;
        lastFpsUpdate = now;
      }

      // Apply zoom inertia (skip if gizmo is animating)
      if (
        !viewportGizmo?.isAnimating &&
        Math.abs(zoomVelocity) > ZOOM_VELOCITY_THRESHOLD &&
        camera.value &&
        controls.value
      ) {
        if (camera.value instanceof THREE.OrthographicCamera) {
          // Orthographic: adjust zoom factor (frustum size) instead of moving camera.
          const orthoCam = camera.value;
          const prevZoom = orthoCam.zoom;
          const zoomFactor = 1 + zoomVelocity;
          orthoCam.zoom = Math.max(
            0.01,
            Math.min(orthoCam.zoom * zoomFactor, 1000),
          );
          orthoCam.updateProjectionMatrix();

          // Pan correction: keep cursor pinned (depth-independent via NDC)
          if (cachedZoomNDC) {
            const scale = 1 - prevZoom / orthoCam.zoom;
            const halfW = (orthoCam.right - orthoCam.left) / (2 * prevZoom);
            const halfH = (orthoCam.top - orthoCam.bottom) / (2 * prevZoom);
            const right = new THREE.Vector3().setFromMatrixColumn(
              camera.value.matrixWorld,
              0,
            );
            const up = new THREE.Vector3().setFromMatrixColumn(
              camera.value.matrixWorld,
              1,
            );
            const panX = scale * cachedZoomNDC.x * halfW;
            const panY = scale * cachedZoomNDC.y * halfH;
            const pan = right
              .multiplyScalar(panX)
              .add(up.clone().multiplyScalar(panY));
            camera.value.position.add(pan);
            controls.value.target.add(pan);
          }
        } else {
          // Perspective: dolly toward orbit target + lateral pan from NDC
          const cam = camera.value as THREE.PerspectiveCamera;
          const lookDir = new THREE.Vector3();
          cam.getWorldDirection(lookDir);
          const distance = cam.position.distanceTo(controls.value.target);
          const moveAmount = distance * zoomVelocity;
          const newDistance = distance - moveAmount;

          if (
            newDistance > controls.value.minDistance &&
            newDistance < controls.value.maxDistance
          ) {
            cam.position.addScaledVector(lookDir, moveAmount);

            // Pan correction: keep cursor pinned (depth-independent via NDC)
            if (cachedZoomNDC) {
              const halfTan = Math.tan(THREE.MathUtils.degToRad(cam.fov / 2));
              const aspect = cam.aspect;
              const right = new THREE.Vector3().setFromMatrixColumn(
                cam.matrixWorld,
                0,
              );
              const up = new THREE.Vector3().setFromMatrixColumn(
                cam.matrixWorld,
                1,
              );
              const panX = moveAmount * cachedZoomNDC.x * halfTan * aspect;
              const panY = moveAmount * cachedZoomNDC.y * halfTan;
              const pan = right
                .multiplyScalar(panX)
                .add(up.clone().multiplyScalar(panY));
              cam.position.add(pan);
              controls.value.target.add(pan);
            }
          } else {
            zoomVelocity = 0;
          }
        }

        // Decay velocity
        zoomVelocity *= ZOOM_DAMPING;

        // Clear velocity if negligible
        if (Math.abs(zoomVelocity) <= ZOOM_VELOCITY_THRESHOLD) {
          zoomVelocity = 0;
        }

        controls.value.syncFromCamera();
        controls.value.dispatchEvent({ type: "change" });
      }

      const frameDelta = frameTime / 1000;
      if (viewportGizmo) viewportGizmo.update(frameDelta);

      if (orbitMarker?.visible) {
        updateOrbitMarkerScale();
      }

      // Update section plane gizmos (constant size + viewport clamping)
      sectionPlanes.updateGizmos();

      if (renderer.value && scene.value && camera.value) {
        const sz = renderer.value.getSize(new THREE.Vector2());
        partSelection.updateScreenSize(sz);

        // Direct render — outline meshes are part of the scene graph
        renderer.value.render(scene.value, camera.value);

        // Update reactive performance stats (must read before gizmo render,
        // because WebGLRenderer.info auto-resets on each .render() call)
        const info = renderer.value.info;
        performanceStats.calls = info.render.calls;
        performanceStats.triangles = info.render.triangles;
        performanceStats.lines = info.render.lines;
        performanceStats.geometries = info.memory.geometries;
        performanceStats.textures = info.memory.textures;

        // Render viewport gizmo overlay (after main scene, on top via scissor)
        if (viewportGizmo) viewportGizmo.render();

        // Render measurement CSS2D labels
        measureTools.render();

        // Get JS heap memory (Chrome/Edge only)
        const perfMemory = (performance as any).memory;
        performanceStats.jsHeapMB = perfMemory
          ? perfMemory.usedJSHeapSize / (1024 * 1024)
          : null;
      }
    };
    animate();
  };

  /**
   * Load a GLB model from a URL with meshopt decoder support
   */
  const loadModel = async (url: string) => {
    console.log(
      "[useThreeViewer] loadModel called with URL:",
      url ? url.substring(0, 100) + "..." : "empty",
    );
    if (!url) {
      console.error("[useThreeViewer] loadModel called with empty URL");
      return;
    }
    if (!scene.value) {
      console.error("[useThreeViewer] Scene not initialized");
      error.value = "Scene not initialized";
      return;
    }

    console.log("[useThreeViewer] Starting model load...");
    status.value = "Loading model...";
    modelLoaded.value = false;
    const loadStart = performance.now();
    let downloadCompleteAt: number | null = null;
    performanceStats.modelLoadStartedAt = loadStart;
    performanceStats.loadingPhase = "download";
    performanceStats.loadingPhaseStartedAt = loadStart;

    // Remove existing model if any
    if (loadedModel.value) {
      appearance.resetForModelLoad();
      sectionPlanes.resetCuttingPlanes();
      scene.value.remove(loadedModel.value);
      disposeObject(loadedModel.value);
      loadedModel.value = null;
      instanceIdMap.clear();
    }
    bvhMeshes.value = [];
    modelBoundingBox = null;
    modelEdgeLineMaterials = [];
    performanceStats.fileSize = null;
    performanceStats.vertices = 0;
    performanceStats.meshes = 0;
    performanceStats.downloadMs = null;
    performanceStats.downloadPercent = null;
    performanceStats.parseDecodeMs = null;
    performanceStats.sceneBuildMs = null;
    performanceStats.bvhMs = null;
    performanceStats.bboxCameraMs = null;
    performanceStats.capsMs = null;
    performanceStats.totalModelLoadMs = null;
    performanceStats.modelLoadStartedAt = loadStart;
    performanceStats.loadingPhase = "download";
    performanceStats.loadingPhaseStartedAt = loadStart;
    performanceStats.downloadStartedAt = loadStart;
    performanceStats.parseDecodeStartedAt = null;
    performanceStats.sceneBuildStartedAt = null;
    performanceStats.bvhStartedAt = null;
    performanceStats.bboxCameraStartedAt = null;
    performanceStats.capsStartedAt = null;

    const loader = new GLTFLoader();

    // Set up meshopt decoder for compressed GLBs
    loader.setMeshoptDecoder(MeshoptDecoder);

    try {
      const gltf = await new Promise<any>((resolve, reject) => {
        loader.load(
          url,
          (gltf) => resolve(gltf),
          (progress) => {
            if (progress.total > 0) {
              performanceStats.fileSize = progress.total; // Capture file size
              const percent = Math.round(
                (progress.loaded / progress.total) * 100,
              );
              performanceStats.downloadPercent = percent;
              status.value = `Loading model... ${percent}%`;
              if (
                progress.loaded >= progress.total &&
                downloadCompleteAt === null
              ) {
                downloadCompleteAt = performance.now();
                performanceStats.downloadMs = downloadCompleteAt - loadStart;
                performanceStats.loadingPhase = "parseDecode";
                performanceStats.loadingPhaseStartedAt = downloadCompleteAt;
              }
            }
          },
          (err) => reject(err),
        );
      });

      const parseDecodeEnd = performance.now();
      const parseDecodeStart = downloadCompleteAt ?? loadStart;
      if (performanceStats.downloadMs === null) {
        performanceStats.downloadMs = parseDecodeStart - loadStart;
      }
      performanceStats.loadingPhase = "parseDecode";
      performanceStats.loadingPhaseStartedAt = parseDecodeStart;
      performanceStats.parseDecodeStartedAt = parseDecodeStart;
      performanceStats.parseDecodeMs = parseDecodeEnd - parseDecodeStart;
      performanceStats.loadingPhase = "sceneBuild";
      performanceStats.loadingPhaseStartedAt = parseDecodeEnd;

      const model = gltf.scene;

      // Apply styling to edge lines with render order to prevent z-fighting
      performanceStats.loadingPhase = "sceneBuild";
      const sceneBuildStart = performance.now();
      performanceStats.loadingPhaseStartedAt = sceneBuildStart;
      performanceStats.sceneBuildStartedAt = sceneBuildStart;
      // Clear previous model edge materials
      modelEdgeLineMaterials = [];

      // Pass 1: Cache mesh material state and collect line objects for fat line conversion
      const linesToReplace: Array<{
        original: THREE.Line | THREE.LineSegments;
        parent: THREE.Object3D;
      }> = [];
      model.traverse((child: THREE.Object3D) => {
        if (child instanceof THREE.Mesh) {
          const mats = Array.isArray(child.material)
            ? child.material
            : [child.material];
          for (const mat of mats) {
            appearance.registerOriginalMaterialState(mat);
          }
        } else if (
          child instanceof THREE.Line ||
          child instanceof THREE.LineSegments
        ) {
          if (child.parent) {
            linesToReplace.push({
              original: child as THREE.Line | THREE.LineSegments,
              parent: child.parent,
            });
          }
        }
      });

      // Pass 1.5: Extract BREP edge topology before fat line conversion destroys _edge_id
      console.log(
        `[useThreeViewer] Pass 1.5: ${linesToReplace.length} line objects to process`,
      );
      for (const { original } of linesToReplace) {
        const geo = original.geometry as THREE.BufferGeometry;
        const attrNames = Object.keys(geo.attributes);
        const isSegs = original instanceof THREE.LineSegments;
        console.log(
          `[useThreeViewer] Line object "${original.name}" type=${isSegs ? "LineSegments" : "Line"}`,
          `vertCount=${geo.getAttribute("position")?.count ?? 0}`,
          `indexed=${!!geo.index}`,
          `attrs=[${attrNames.join(", ")}]`,
        );
        const instanceNode = findParentInstanceNode(
          original.parent ?? original,
        );
        if (instanceNode) {
          extractEdgeTopology(original, instanceNode);
        } else {
          console.warn(
            `[useThreeViewer] No instance node found for line "${original.name}"`,
          );
        }
      }

      // Pass 2: Replace thin Line/LineSegments with fat LineSegments2
      for (const { original, parent } of linesToReplace) {
        const fatLine = convertToFatLine(original);
        if (fatLine) {
          parent.add(fatLine);
          parent.remove(original);
          // Dispose original geometry and material
          original.geometry?.dispose();
          if (original.material) {
            if (Array.isArray(original.material)) {
              original.material.forEach((m) => m.dispose());
            } else {
              original.material.dispose();
            }
          }
        }
      }
      loadedModel.value = model;
      rebuildInstanceIdMap();
      if (!scene.value) {
        console.warn(
          "[useThreeViewer] Scene was disposed during model load, aborting.",
        );
        return;
      }
      scene.value.add(model);
      performanceStats.sceneBuildMs = performance.now() - sceneBuildStart;

      performanceStats.loadingPhase = "bboxCamera";
      const bboxCameraStart = performance.now();
      performanceStats.loadingPhaseStartedAt = bboxCameraStart;
      performanceStats.bboxCameraStartedAt = bboxCameraStart;

      // Cache model bounding box for fast access (used by cutting planes)
      modelBoundingBox = new THREE.Box3().setFromObject(model);
      fitCameraToModel(model);
      performanceStats.bboxCameraMs = performance.now() - bboxCameraStart;
      performanceStats.loadingPhase = "bvh";
      performanceStats.loadingPhaseStartedAt = performance.now();

      const bvhStart = performance.now();
      performanceStats.bvhStartedAt = bvhStart;
      setupModelBVH(model);
      performanceStats.bvhMs = performance.now() - bvhStart;
      performanceStats.loadingPhase = "caps";
      performanceStats.loadingPhaseStartedAt = performance.now();

      // Warm up BVH traversal in background (triggers JIT compilation, fills caches)
      // This makes first cutting plane activation much faster
      warmUpBVH();

      // Recreate caps + edges for active planes
      const capsStart = performance.now();
      performanceStats.capsStartedAt = capsStart;
      sectionPlanes.recreateCapsForActivePlanes();
      performanceStats.capsMs = performance.now() - capsStart;

      status.value = "Model loaded";
      performanceStats.totalModelLoadMs = performance.now() - loadStart;
      performanceStats.loadingPhase = null;
      performanceStats.loadingPhaseStartedAt = null;
      performanceStats.modelLoadStartedAt = null;
      modelLoaded.value = true;
      error.value = null;

      console.log("Three.js model loaded successfully");
    } catch (err) {
      console.error("Error loading model:", err);
      error.value = err instanceof Error ? err.message : "Failed to load model";
      status.value = "Load failed";
    }
  };

  /**
   * Fit the camera to show the entire model
   */
  const fitCameraToModel = (model: THREE.Object3D) => {
    if (!camera.value || !controls.value) return;

    // Compute bounding box
    const box = new THREE.Box3().setFromObject(model);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());

    // Get the max dimension
    const maxDim = Math.max(size.x, size.y, size.z);
    const direction = new THREE.Vector3(1, 0.5, 1).normalize();

    const cam = camera.value;

    if (cam instanceof THREE.PerspectiveCamera) {
      const fov = cam.fov * (Math.PI / 180);
      let cameraDistance = maxDim / (2 * Math.tan(fov / 2));
      cameraDistance *= 1.5;
      cam.position
        .copy(center)
        .add(direction.clone().multiplyScalar(cameraDistance));
      cam.lookAt(center);
    } else {
      // Orthographic: set frustum to contain the model.
      // Position the camera far back so no geometry can ever end up behind it
      // during orbit. Since ortho has no perspective distortion, distance is
      // purely a clipping concern.
      const cameraDistance = maxDim * 50;
      const halfH = maxDim * 0.75;
      const w = renderer.value?.domElement.clientWidth || 1;
      const h = renderer.value?.domElement.clientHeight || 1;
      const aspect = w / h;
      cam.left = -halfH * aspect;
      cam.right = halfH * aspect;
      cam.top = halfH;
      cam.bottom = -halfH;
      cam.zoom = 1;
      cam.position
        .copy(center)
        .add(direction.clone().multiplyScalar(cameraDistance));
      cam.lookAt(center);
    }

    controls.value.target.copy(center);
    controls.value.maxDistance = maxDim * 100;
    controls.value.minDistance = maxDim * 0.001;
    controls.value.syncFromCamera();

    // Update near/far planes based on model size.
    if (cam instanceof THREE.OrthographicCamera) {
      cam.near = 0.1;
      cam.far = maxDim * 150;
    } else {
      cam.near = maxDim / 1000;
      cam.far = maxDim * 100;
    }
    cam.updateProjectionMatrix();
  };

  /**
   * Recompute modelBoundingBox and update camera near/far planes to encompass
   * the current scene geometry.  Called after gizmo drags or transform restores
   * so that moved parts are never clipped by stale frustum bounds.
   */
  const updateClippingFromBounds = () => {
    if (!loadedModel.value || !camera.value) return;

    modelBoundingBox = new THREE.Box3().setFromObject(loadedModel.value);
    const size = modelBoundingBox.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    if (maxDim === 0) return;

    const cam = camera.value;
    if (cam instanceof THREE.OrthographicCamera) {
      cam.near = 0.1;
      cam.far = maxDim * 150;
    } else {
      cam.near = maxDim / 1000;
      cam.far = maxDim * 100;
    }
    cam.updateProjectionMatrix();
  };

  /**
   * Dispose of a Three.js object and its children
   */
  const disposeObject = (obj: THREE.Object3D) => {
    obj.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        if (child.geometry) {
          child.geometry.dispose();
        }
        if (child.material) {
          if (Array.isArray(child.material)) {
            child.material.forEach((mat) => mat.dispose());
          } else {
            child.material.dispose();
          }
        }
      }
    });
  };

  /**
   * Clean up all Three.js resources
   */
  const cleanup = () => {
    // Stop render loop
    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }

    // Dispose hotkey listener
    hotkeys.dispose();

    // Dispose measurement tools
    measureTools.dispose();

    // Dispose viewport gizmo
    if (viewportGizmo) {
      viewportGizmo.dispose();
      viewportGizmo = null;
    }

    // Disconnect resize observer
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }

    // Remove custom input handlers
    const canvas = canvasRef.value;
    if (canvas) {
      canvas.removeEventListener("pointerdown", handlePointerDown);
      canvas.removeEventListener("pointermove", handlePointerMove);
      canvas.removeEventListener("pointerup", handlePointerUp);
      canvas.removeEventListener("pointerleave", handlePointerLeave);
      canvas.removeEventListener("wheel", handleWheel);
      canvas.removeEventListener("click", handleClick);
    }

    partSelection.dispose();
    appearance.dispose();

    // Dispose transform gizmo
    transformGizmo.disposeTransformGizmo();

    // Dispose controls
    if (controls.value) {
      controls.value.dispose();
      controls.value = null;
    }

    // Dispose model
    if (loadedModel.value && scene.value) {
      sectionPlanes.cleanup();
      scene.value.remove(loadedModel.value);
      disposeObject(loadedModel.value);
      loadedModel.value = null;
      instanceIdMap.clear();
    }
    bvhMeshes.value = [];
    modelBoundingBox = null;
    modelEdgeLineMaterials = [];
    performanceStats.fileSize = null;
    performanceStats.vertices = 0;
    performanceStats.meshes = 0;
    performanceStats.downloadMs = null;
    performanceStats.downloadPercent = null;
    performanceStats.parseDecodeMs = null;
    performanceStats.sceneBuildMs = null;
    performanceStats.bvhMs = null;
    performanceStats.bboxCameraMs = null;
    performanceStats.capsMs = null;
    performanceStats.totalModelLoadMs = null;
    performanceStats.modelLoadStartedAt = null;
    performanceStats.loadingPhase = null;
    performanceStats.loadingPhaseStartedAt = null;
    performanceStats.downloadStartedAt = null;
    performanceStats.parseDecodeStartedAt = null;
    performanceStats.sceneBuildStartedAt = null;
    performanceStats.bvhStartedAt = null;
    performanceStats.bboxCameraStartedAt = null;
    performanceStats.capsStartedAt = null;

    // Dispose cap worker
    disposeCapWorkerManager();

    // Dispose scene objects
    if (scene.value) {
      scene.value.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          if (child.geometry) child.geometry.dispose();
          if (child.material) {
            if (Array.isArray(child.material)) {
              child.material.forEach((mat) => mat.dispose());
            } else {
              child.material.dispose();
            }
          }
        }
      });
      scene.value.clear();
      scene.value = null;
    }

    // Dispose renderer
    if (renderer.value) {
      renderer.value.dispose();
      renderer.value = null;
    }

    camera.value = null;
    viewerReady.value = false;
    modelLoaded.value = false;
  };

  /**
   * Manually trigger a resize (useful after layout changes)
   */
  const resizeViewerCanvas = () => {
    const canvas = canvasRef.value;
    if (!canvas) return;

    const container = canvas.parentElement;
    const width = container?.clientWidth ?? canvas.clientWidth;
    const height = container?.clientHeight ?? canvas.clientHeight;
    handleResize(width, height);
  };

  /**
   * Reset the camera to fit the model
   */
  const resetView = () => {
    if (loadedModel.value) {
      fitCameraToModel(loadedModel.value);
    }
  };

  /**
   * Turn the camera to look along `dir` (a world direction) with `up` pointing up the screen, at the same target and distance.
   */
  const lookAlong = (dir: number[], up: number[], frame?: { center: number[]; extent: number }) => {
    const cam = camera.value;
    const ctl = controls.value;
    if (!cam || !ctl) return;
    const target = frame ? new THREE.Vector3(frame.center[0], frame.center[1], frame.center[2]) : ctl.target.clone();
    if (frame) ctl.target.copy(target);
    let dist = Math.max(cam.position.distanceTo(target), 1e-3);
    if (frame && frame.extent > 0) {
      if (cam instanceof THREE.PerspectiveCamera) dist = (frame.extent * 1.3) / (2 * Math.tan((cam.fov * Math.PI) / 360));
      else cam.zoom = Math.max(1e-6, (cam.top - cam.bottom) / (frame.extent * 1.3));
    }
    const d = new THREE.Vector3(dir[0], dir[1], dir[2]).normalize();
    cam.up.set(up[0], up[1], up[2]);
    cam.position.copy(target).addScaledVector(d, -dist);
    cam.lookAt(target);
    ctl.syncFromCamera();
    cam.updateProjectionMatrix();
  };

  // Watch for canvas to become available (it's inside v-else, so may not exist on mount)
  watch(
    () => canvasRef.value,
    (canvas) => {
      console.log("[useThreeViewer] canvasRef watch triggered:", {
        hasCanvas: !!canvas,
        viewerReady: viewerReady.value,
      });
      if (canvas && !viewerReady.value) {
        console.log(
          "[useThreeViewer] Canvas now available, initializing scene...",
        );
        const initialized = initScene();
        if (initialized && options.meshUrl.value) {
          console.log(
            "[useThreeViewer] Loading model after canvas became available...",
          );
          loadModel(options.meshUrl.value);
        }
      }
    },
    { immediate: true },
  );

  // Watch for meshUrl changes and load the model
  watch(
    () => options.meshUrl.value,
    (newUrl) => {
      console.log("[useThreeViewer] meshUrl watch triggered:", {
        newUrl: newUrl ? newUrl.substring(0, 80) + "..." : "empty",
        viewerReady: viewerReady.value,
      });
      if (newUrl && viewerReady.value) {
        loadModel(newUrl);
      } else if (newUrl && !viewerReady.value) {
        console.log(
          "[useThreeViewer] URL available but viewer not ready yet - will load when canvas is ready",
        );
      } else {
        console.log("[useThreeViewer] Skipping load - URL empty");
      }
    },
  );

  // Initialize on mount if canvas is already available
  onMounted(() => {
    console.log("[useThreeViewer] onMounted called:", {
      hasCanvas: !!canvasRef.value,
      meshUrl: options.meshUrl.value
        ? options.meshUrl.value.substring(0, 80) + "..."
        : "empty",
    });
    // Canvas might not be available yet if inside v-if/v-else - the watch above will handle it
    if (canvasRef.value && !viewerReady.value) {
      const initialized = initScene();
      console.log("[useThreeViewer] initScene returned:", initialized);
      if (initialized && options.meshUrl.value) {
        console.log("[useThreeViewer] Loading model on mount...");
        loadModel(options.meshUrl.value);
      }
    }
  });

  // Cleanup on unmount
  onBeforeUnmount(() => {
    cleanup();
  });

  // Event handlers
  const onViewerReady = () => {
    // No-op: Three.js is ready immediately after initScene
  };

  const onSceneReady = () => {
    // No-op
  };

  const onFirstModelLoaded = async () => {
    // Called automatically when model loads
  };

  const onModelFail = () => {
    // Error handling is done in loadModel
  };

  const onModelStructureReady = () => {
    // No-op for now - structure parsing from metadata would go here
  };

  const onMeasurementCreated = () => {
    // Measurement auto-deactivation is handled by the measureTools callback
    // set in setOnMeasurementCreated above.
  };

  // ── Explode view composable ──
  const explodeContext: ExplodeViewContext = {
    scene,
    loadedModel,
    getModelBoundingBox: () => modelBoundingBox,
    getSelectedNodeIds: () => partSelection.getSelectedNodeIds(),
    isSignificantChild: (child) => isSignificantChild(child),
    updateClippingFromBounds: () => updateClippingFromBounds(),
    refreshSectionCaps: () => sectionPlanes.refreshAllCaps(),
  };
  const explodeView = useExplodeView(explodeContext);

  /** Helper: set a mesh child visible/hidden (Mesh + fat-edge lines). */
  const setMeshVisibility = (child: THREE.Object3D, visible: boolean) => {
    if (child instanceof THREE.Mesh || (child as any).userData?.isFatEdgeLine) {
      if (!child.userData?._isOutlineHelper) {
        child.visible = visible;
      }
    }
  };

  // ── Transform gizmo (delegated to useTransformGizmo composable) ──
  const transformGizmo = useTransformGizmo({
    scene,
    camera,
    canvasEl: canvasRef,
    controls,
    interaction,
    selectedInstanceNode: () => partSelection.getSelectedInstanceNode(),
    getSelectedNodes: () => partSelection.getSelectedNodes(),
    findNodeByInstanceId: (id: string) => findNodeByInstanceId(id),
    updateClippingFromBounds,
    refreshSectionCaps: sectionPlanes.refreshAllCaps,
  });

  const resetModel = () => {
    explodeView.resetExplode();

    if (loadedModel.value) {
      loadedModel.value.traverse((child) => setMeshVisibility(child, true));
    }

    appearance.resetMaterialOverrides();

    transformGizmo.restoreOriginalTransforms();
    transformGizmo.clearTransformState();
    measureTools.clearAll();
    interaction.resetMode();
    transformGizmo.detachTransformGizmo();
    partSelection.clearAllSelections();

    sectionPlanes.updateMaterialClipping();
    sectionPlanes.refreshAllCaps();
  };

  /**
   * Fast-path model state restore for feedback recall.
   * Orchestrates subsystem resets, then delegates material work to
   * the appearance composable's optimised single-traversal path.
   *
   * Does NOT call refreshAllCaps — the caller handles that.
   */
  const restoreFeedbackModelState = (opts: {
    drawModeId: string;
    hiddenIds: Set<string>;
    transparentNodes: Map<string, number>;
  }) => {
    if (!loadedModel.value) return;

    explodeView.resetExplode();
    transformGizmo.restoreOriginalTransforms();
    transformGizmo.clearTransformState();
    measureTools.clearAll();
    interaction.resetMode();
    transformGizmo.detachTransformGizmo();
    partSelection.clearAllSelections();

    appearance.applyFeedbackDrawMode(opts);
  };

  // ── Getters for markup data persistence ──
  // Uses cadreview_instanceId (stable deterministic hash from the OCCT converter)
  // instead of THREE.Object3D.id which changes every page load.

  /** Find a scene node by its stable cadreview_instanceId string (O(1) map lookup). */
  const findNodeByInstanceId = (instanceId: string): THREE.Object3D | null => {
    return instanceIdMap.get(instanceId) ?? null;
  };

  /**
   * Walk the loaded model and collect stable instance IDs where all
   * descendant meshes are hidden.
   */
  const getHiddenNodeIds = (): string[] => {
    if (!loadedModel.value) return [];
    const hiddenIds: string[] = [];
    loadedModel.value.traverse((child) => {
      const iid = child.userData?.cadreview_instanceId;
      if (iid) {
        const state = computeVisibilityState(child);
        if (state === "hidden") hiddenIds.push(iid);
      }
    });
    return hiddenIds;
  };

  /**
   * Restore hidden nodes from saved markup data (uses stable instance IDs).
   * First resets everything to visible, then hides the specified nodes.
   */
  const restoreHiddenNodes = (instanceIds: string[], skipReset = false) => {
    if (!loadedModel.value) return;
    // Reset all meshes to visible first (unless caller already set a correct baseline,
    // e.g. after reapplyDrawMode during feedback restore)
    if (!skipReset) {
      loadedModel.value.traverse((child) => setMeshVisibility(child, true));
    }
    // Then hide the specified nodes
    for (const iid of instanceIds) {
      const obj = findNodeByInstanceId(iid);
      if (obj) {
        obj.traverse((child) => setMeshVisibility(child, false));
      }
    }
    // Refresh section caps since visibility changed
    sectionPlanes.refreshAllCaps();
  };

  // ── Model tree helpers ──────────────────────────────────────────────

  /**
   * Walk up the scene graph from a mesh to find the nearest ancestor
   * that is an OCCT instance node (has cadreview_instanceId in userData).
   */
  const findParentInstanceNode = (
    obj: THREE.Object3D,
  ): THREE.Object3D | null => {
    let current: THREE.Object3D | null = obj;
    while (current && current !== loadedModel.value) {
      if (current.userData?.cadreview_instanceId) return current;
      current = current.parent;
    }
    return null;
  };

  /**
   * Check if `child` is a descendant of `ancestor` in the scene graph.
   */
  const isDescendantOf = (
    child: THREE.Object3D,
    ancestor: THREE.Object3D,
  ): boolean => {
    let current = child.parent;
    while (current) {
      if (current === ancestor) return true;
      current = current.parent;
    }
    return false;
  };

  /**
   * Collect all Mesh descendants of an instance node.
   */
  const collectInstanceMeshes = (
    instanceNode: THREE.Object3D,
  ): THREE.Mesh[] => {
    const meshes: THREE.Mesh[] = [];
    instanceNode.traverse((child) => {
      if (
        child instanceof THREE.Mesh &&
        !child.userData?.isFatEdgeLine &&
        !child.userData?._isOutlineHelper
      ) {
        meshes.push(child);
      }
    });
    return meshes;
  };

  /**
   * Returns true if an Object3D child is a "significant" assembly/part node.
   * The OCCT converter tags every instance node with cadreview_instanceId in
   * its glTF extras (copied to userData by GLTFLoader).  Geometry leaves
   * ("Faces", "Edges", fat-line replacements) never have this extra, so
   * checking for it is the most reliable way to distinguish instance nodes
   * from internal geometry.
   */
  const isSignificantChild = (child: THREE.Object3D): boolean => {
    return !!child.userData?.cadreview_instanceId;
  };

  /**
   * Compute the aggregate visibility state for an Object3D subtree.
   * Looks at all descendant Mesh / fat-line objects.
   */
  const computeVisibilityState = (
    obj: THREE.Object3D,
  ): "visible" | "hidden" | "partial" => {
    let hasVisible = false;
    let hasHidden = false;

    obj.traverse((child) => {
      if (child === obj) return;
      if (child.userData?._isOutlineHelper || child.userData?._isSelectionOverlay) return;
      if (
        child instanceof THREE.Mesh ||
        (child as any).userData?.isFatEdgeLine
      ) {
        if (child.visible) hasVisible = true;
        else hasHidden = true;
      }
    });

    if (hasVisible && hasHidden) return "partial";
    if (hasVisible) return "visible";
    return "hidden";
  };

  /**
   * Build a TreeNode from a Three.js Object3D, recursively.
   * `expandedSet` carries nodeIds that were expanded in a previous tree so we
   * can preserve the user's expand/collapse state across rebuilds.
   */
  const buildTreeNode = (
    obj: THREE.Object3D,
    expandedSet: Set<number>,
    isRoot: boolean = false,
  ): TreeNode => {
    const significantChildren = obj.children.filter(isSignificantChild);
    const type: TreeNode["type"] = isRoot
      ? "root"
      : significantChildren.length > 0
        ? "assembly"
        : "part";

    const children = significantChildren.map((c) =>
      buildTreeNode(c, expandedSet),
    );

    return {
      nodeId: obj.id,
      name: obj.name || `Node ${obj.id}`,
      type,
      children,
      isExpanded: expandedSet.has(obj.id) || isRoot,
      visibilityState: computeVisibilityState(obj),
    };
  };

  /**
   * Collect all nodeIds that are currently expanded in a TreeNode tree.
   */
  const collectExpandedIds = (node: TreeNode, out: Set<number>) => {
    if (node.isExpanded) out.add(node.nodeId);
    for (const child of node.children) collectExpandedIds(child, out);
  };

  /**
   * Build (or rebuild) the model tree from the loaded scene graph.
   * Pass the previous tree to preserve expanded state.
   */
  const getModelTree = (previousTree?: TreeNode | null): TreeNode | null => {
    if (!loadedModel.value) return null;

    // Preserve expanded state from the previous tree
    const expandedSet = new Set<number>();
    if (previousTree) {
      collectExpandedIds(previousTree, expandedSet);
    }

    const model = loadedModel.value;
    const topChildren = model.children.filter(isSignificantChild);

    if (topChildren.length === 0) return null;

    // Single top-level node — use it as the root directly
    if (topChildren.length === 1) {
      return buildTreeNode(topChildren[0], expandedSet, true);
    }

    // Multiple top-level nodes — wrap in a virtual root
    return {
      nodeId: model.id,
      name: model.name || "Model",
      type: "root",
      children: topChildren.map((c) => buildTreeNode(c, expandedSet)),
      isExpanded: true,
      visibilityState: computeVisibilityState(model),
    };
  };

  /**
   * Toggle visibility for a node and all of its descendants.
   */
  const toggleNodeVisibility = (nodeId: number) => {
    if (!scene.value) return;
    const obj = scene.value.getObjectById(nodeId);
    if (!obj) return;

    // Determine current aggregate state — if anything is visible, hide all; otherwise show all
    const currentState = computeVisibilityState(obj);
    const makeVisible = currentState !== "visible";

    obj.traverse((child) => {
      if (
        child instanceof THREE.Mesh ||
        (child as any).userData?.isFatEdgeLine
      ) {
        child.visible = makeVisible;
      }
    });
  };

  /**
   * Explicitly set visibility for a node and all of its descendants.
   * Used by the rollback bar to hide/show nodes without toggling.
   */
  const setNodeVisibility = (nodeId: number, visible: boolean) => {
    if (!scene.value) return;
    const obj = scene.value.getObjectById(nodeId);
    if (!obj) return;
    obj.traverse((child) => {
      if (
        child instanceof THREE.Mesh ||
        (child as any).userData?.isFatEdgeLine
      ) {
        child.visible = visible;
      }
    });
  };

  /**
   * Clear the current selection outline.
   */
  const getSelectedFaceInfo = () => null;

  // Cutting plane functions - delegate to sectionPlanes composable
  const toggleCuttingPlaneX = (
    flip: boolean = false,
    isActive: boolean = false,
  ): boolean => {
    if (!loadedModel.value || !scene.value) return false;
    return sectionPlanes.toggleCuttingPlaneX(flip, isActive);
  };

  const toggleCuttingPlaneY = (
    flip: boolean = false,
    isActive: boolean = false,
  ): boolean => {
    if (!loadedModel.value || !scene.value) return false;
    return sectionPlanes.toggleCuttingPlaneY(flip, isActive);
  };

  const toggleCuttingPlaneZ = (
    flip: boolean = false,
    isActive: boolean = false,
  ): boolean => {
    if (!loadedModel.value || !scene.value) return false;
    return sectionPlanes.toggleCuttingPlaneZ(flip, isActive);
  };

  const cuttingPlaneOnSelectedFace = (
    _flip: boolean = false,
    _isActive: boolean = false,
  ): boolean => {
    // TODO: Implement face-based cutting plane using getSelectedFaceInfo
    console.log(
      "cuttingPlaneOnSelectedFace not yet implemented for Three.js viewer",
    );
    return false;
  };

  const resetCuttingPlanes = () => {
    sectionPlanes.resetCuttingPlanes();
  };

  const hideCuttingPlanes = (hide: boolean) => {
    sectionPlanes.hideCuttingPlanes(hide);
  };

  // Toggle section edge lines visibility
  const setShowSectionEdges = (show: boolean) => {
    sectionPlanes.setShowSectionEdges(show);
  };

  // Measurement functions (delegated to useThreeMeasure composable)

  const activateMeasurementOperator = (operatorName: string): boolean => {
    const result = measureTools.activate(operatorName);
    if (result) {
      interaction.setMode("measure");

      const statusMessages: Record<string, string> = {
        SmartMeasure:
          "Smart Measure \u2014 Hover to highlight, click to select edges, faces, or endpoints",
      };
      status.value =
        statusMessages[operatorName] ||
        `Measure: ${operatorName} \u2014 Click to measure`;
    }
    return result;
  };

  const clearAllMeasurements = () => {
    measureTools.clearAll();
    interaction.resetMode();
  };

  const removeLastMeasurement = () => {
    measureTools.removeLast();
  };

  const getAllMeasurements = (): MeasurementData[] => {
    return measureTools.getAll();
  };

  const loadMeasurements = (data: MeasurementData[]) => {
    measureTools.loadAll(data);
  };

  const deactivateMeasurementOperator = () => {
    measureTools.deactivate();
    interaction.resetMode();
  };

  const setOnMeasurementCreatedExternal = (cb: (() => void) | null) => {
    externalMeasurementCreatedCb = cb;
  };

  // Note: Feedback/Note markup state and functions have been moved to useThreeMarkup.ts

  // Return public API
  return {
    // State
    status,
    viewerReady,
    modelLoaded,
    error,
    performanceStats,

    // Core functions
    loadModel,
    resizeViewerCanvas,
    resetView,
    lookAlong,
    cleanup,

    // Raycasting utilities
    raycastFromCursor,
    getPointUnderCursor,
    getObjectUnderCursor,
    getHitInfo,

    // Debug utilities
    raycastDebugEnabled,
    placeDebugMarker,
    clearDebugMarkers,
    showDebugBBoxes: explodeView.showDebugBBoxes,
    clearDebugBBoxes: explodeView.clearDebugBBoxes,

    // Event handlers (for template compatibility)
    onViewerReady,
    onSceneReady,
    onFirstModelLoaded,
    onModelFail,
    onModelStructureReady,
    onMeasurementCreated,

    // View mode functions
    applyDrawMode: appearance.applyDrawMode,
    reapplyDrawMode: appearance.reapplyDrawMode,
    onExplodeChange: explodeView.onExplodeChange,
    getExplodeMagnitude: explodeView.getExplodeMagnitude,

    // Selection functions
    getSelectedNodeIds: partSelection.getSelectedNodeIds,
    hideSelectedNodes: partSelection.hideSelectedNodes,
    isolateSelectedNodes: partSelection.isolateSelectedNodes,
    showHandles: transformGizmo.showHandles,
    toggleTransparency: appearance.toggleTransparency,
    updateTransparencyOpacity: appearance.updateTransparencyOpacity,
    highlightSelectedNodes: appearance.highlightSelectedNodes,
    resetModel,

    // Visibility / transparency getters (for markup data)
    getHiddenNodeIds,
    getTransparentNodes: appearance.getTransparentNodes,
    restoreHiddenNodes,
    restoreTransparentNodes: appearance.restoreTransparentNodes,
    restoreFeedbackModelState,
    getActiveDrawModeId: appearance.getActiveDrawModeId,

    // Transform gizmo
    getTransformedNodes: transformGizmo.getTransformedNodes,
    restoreTransformedNodes: transformGizmo.restoreTransformedNodes,
    transformGizmoActive: transformGizmo.transformGizmoActive,
    transformGizmoMode: transformGizmo.transformGizmoMode,

    // Model tree functions
    getModelTree,
    toggleNodeVisibility,
    setNodeVisibility,
    selectNode: partSelection.selectNode,
    getSelectedFaceInfo,

    // Cutting plane functions
    resetCuttingPlanes,
    toggleCuttingPlaneX,
    toggleCuttingPlaneY,
    toggleCuttingPlaneZ,
    cuttingPlaneOnSelectedFace,
    hideCuttingPlanes,
    showSectionEdges: sectionPlanes.showSectionEdges,
    cuttingPlanesVisible: sectionPlanes.cuttingPlanesVisible,
    activeCuttingPlanes: sectionPlanes.activeCuttingPlanes,
    setShowSectionEdges,
    showSectionCaps: sectionPlanes.showSectionCaps,
    setShowSectionCaps: sectionPlanes.setShowSectionCaps,
    // New section state API
    getActivePlanes: sectionPlanes.getActivePlanes,
    getSectionState: sectionPlanes.getSectionState,
    restoreSectionState: sectionPlanes.restoreSectionState,
    updateSectionFlip: sectionPlanes.updateSectionFlip,
    refreshAllCaps: sectionPlanes.refreshAllCaps,
    planeDistances: sectionPlanes.planeDistances,
    setPlaneDistance: sectionPlanes.setPlaneDistance,
    dragAnnotation: sectionPlanes.dragAnnotation,

    // Interaction mode manager
    interaction,

    // Hotkey registry
    hotkeys,

    // Measurement functions
    activateMeasurementOperator,
    clearAllMeasurements,
    removeLastMeasurement,
    getAllMeasurements,
    loadMeasurements,
    deactivateMeasurementOperator,
    setOnMeasurementCreatedExternal,
    currentMeasureInfo: measureTools.currentMeasureInfo,
    selectedMeasurementId: measureTools.selectedMeasurementId,
    togglePendingMode: measureTools.togglePendingMode,
    toggleMeasurementMode: measureTools.toggleMeasurementMode,
    toggleCenterEdgePending: measureTools.toggleCenterEdgePending,
    toggleCenterEdgeById: measureTools.toggleCenterEdgeById,
    selectMeasurement: measureTools.selectMeasurement,
    setOnGizmoDragEnd: transformGizmo.setOnGizmoDragEnd,

    // Note: Feedback/Note markup functions moved to useThreeMarkup.ts

    // Three.js objects (for advanced usage)
    scene,
    camera,
    renderer,
    controls,
    loadedModel,

    // ── Interference-checker additions ──
    findNodeByInstanceId,
    findParentInstanceNode,
    /** Reactive ids of the nodes selected in the viewport (kept in sync with the model tree). */
    selectedNodeIds: partSelection.selectedNodeIdsRef,
    clearSelection: partSelection.clearSelectionHighlight,
    collectInstanceMeshes,
    setMeshVisibility,
    updateClippingFromBounds,
    updateMaterialClipping: () => sectionPlanes.updateMaterialClipping(),
    getModelBoundingBox: () => modelBoundingBox,
    /**
     * PNG of the current view (no UI gizmos). Renders one frame and snapshots the
     * canvas in the same task, so it works without preserveDrawingBuffer.
     */
    captureViewport: (): Promise<Blob | null> =>
      new Promise((resolve) => {
        const r = renderer.value;
        const s = scene.value;
        const c = camera.value;
        if (!r || !s || !c) return resolve(null);
        r.render(s, c);
        r.domElement.toBlob((b) => resolve(b), "image/png");
      }),
    /** Make materials created outside the viewer obey the active section planes. */
    registerClippableMaterials: (mats: THREE.Material[]) => {
      const cache = appearance.getCachedMaterials();
      for (const m of mats) if (!cache.includes(m)) cache.push(m);
      sectionPlanes.updateMaterialClipping();
    },
    unregisterClippableMaterials: (mats: THREE.Material[]) => {
      const cache = appearance.getCachedMaterials();
      for (const m of mats) {
        const i = cache.indexOf(m);
        if (i >= 0) cache.splice(i, 1);
      }
    },

    // Control type management
    currentControlType,
    setControlType,

    // Projection / FOV management
    currentProjection,
    currentFov,
    setProjectionType,
    setViewerFov,
    setZoomInverted,
    setSceneBackground,

    // Camera state capture / restore
    getCameraState,
    restoreCameraState,

    // Right-click drag detection (for context menu suppression)
    rightClickWasDrag,

    // Clipping plane visibility check (for pin clipping)
    isPointVisible,

    // Cursor lock (prevents hover/gizmo from overwriting the cursor)
    cursorLocked,

    // Sequential hide mode
    sequentialHideActive: sequentialHide.sequentialHideActive,
    activateSequentialHide: sequentialHide.activate,
    deactivateSequentialHide: sequentialHide.deactivate,
    sequentialHideContext,
  };
}
