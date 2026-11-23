// Paints the loaded CAD model by optical role (LED, light pipe, reflector...) and highlights picked faces.
import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from "vue";
import * as THREE from "three";
import type { FinishDoc, Library, PartDoc, Setup } from "../api";
import { getBrepFaceData, getBrepFaceTrianglePositions, getFaceIdAtTriangle } from "./viewer/useBrepTopology";
import { instanceForPattern, resolvePart } from "../utils/setup";

export interface FaceHighlight {
  key: string;
  instanceId: string;
  face: number;
  color: number;
  opacity?: number;
}

export interface PickedFace {
  instanceId: string;
  face: number;
  point: THREE.Vector3;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Viewer = any;

interface Leaf {
  id: string;
  meshes: THREE.Mesh[];
  lines: THREE.Object3D[];
}

export function useSetupScene(
  viewer: Viewer,
  input: {
    parts: Ref<PartDoc[]>;
    setup: Ref<Setup | null>;
    library: Ref<Library | null>;
    /** Instances (leaf ids) that are selected in the viewport, including everything inside a selected assembly. */
    selectedIds: Ref<Set<string>>;
    highlights: Ref<FaceHighlight[]>;
    dimOthers: Ref<boolean>;
    /** Draw every part as a see-through ghost (to look at what is inside). */
    allTransparent: Ref<boolean>;
  },
) {
  const leaves = shallowRef(new Map<string, Leaf>());
  /**
   * Viewport-only show / hide choices per instance (true = shown, false = hidden). An instance without an entry
   * follows the setup: parts that are ignored by the simulation are hidden, everything else is shown.
   */
  const visibility = shallowRef(new Map<string, boolean>());
  /** Bumped after every repaint, so the model tree can refresh its eye icons. */
  const paintVersion = ref(0);
  let lastHiddenKey = "";
  const created: THREE.Material[] = [];
  const saved = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  const overlay = new THREE.Group();
  overlay.name = "refract-face-overlay";
  let overlayAttached = false;
  const hover = ref<FaceHighlight | null>(null);

  function index() {
    const model = viewer.loadedModel.value as THREE.Object3D | null;
    const map = new Map<string, Leaf>();
    if (!model) {
      leaves.value = map;
      return;
    }
    const owner = (obj: THREE.Object3D): string | null => {
      for (let cur: THREE.Object3D | null = obj; cur && cur !== model; cur = cur.parent) {
        const id = cur.userData?.cadreview_instanceId;
        if (id) return id;
      }
      return null;
    };
    const entry = (id: string) => {
      let e = map.get(id);
      if (!e) map.set(id, (e = { id, meshes: [], lines: [] }));
      return e;
    };
    model.traverse((child) => {
      if (child.userData?.isFatEdgeLine) {
        const id = owner(child);
        if (id) entry(id).lines.push(child);
      } else if (child instanceof THREE.Mesh && !child.userData?._isOutlineHelper) {
        const id = owner(child);
        if (id) entry(id).meshes.push(child);
      }
    });
    leaves.value = map;
  }

  function roleMaterial(color: number, opts: { opacity?: number; metalness?: number; emissive?: number } = {}): THREE.MeshStandardMaterial {
    const transparent = (opts.opacity ?? 1) < 1;
    const m = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.55,
      metalness: opts.metalness ?? 0.05,
      side: THREE.DoubleSide,
      transparent,
      opacity: opts.opacity ?? 1,
      depthWrite: !transparent,
      emissive: opts.emissive ?? 0x000000,
      emissiveIntensity: opts.emissive ? 0.55 : 0,
    });
    created.push(m);
    return m;
  }

  function restore() {
    for (const [mesh, mat] of saved) mesh.material = mat;
    saved.clear();
    if (created.length) {
      viewer.unregisterClippableMaterials?.(created);
      created.forEach((m) => m.dispose());
      created.length = 0;
    }
    for (const leaf of leaves.value.values()) {
      leaf.meshes.forEach((m) => (m.visible = true));
      leaf.lines.forEach((l) => (l.visible = true));
    }
  }

  /** Preview colour of an opaque surface: a bundled finish, or an opaque surface material (same fields: colour, rdiff, rspec). */
  function finishColor(f: Pick<FinishDoc, "color" | "rdiff" | "rspec"> & { type?: string; mode?: string } | undefined): { color: number; metalness: number } {
    if (!f || (f.type !== "opaque" && f.mode !== "surface")) return { color: 0x7b8794, metalness: 0.05 };
    if (f.color && /^#[0-9a-f]{6}$/i.test(f.color)) return { color: parseInt(f.color.slice(1), 16), metalness: (f.rspec ?? 0) > 0.5 ? 0.85 : 0.05 };
    const refl = (f.rdiff ?? 0) + (f.rspec ?? 0);
    if ((f.rspec ?? 0) > 0.5) return { color: 0xcbd5e1, metalness: 0.85 };
    const g = Math.round(40 + refl * 200);
    return { color: (g << 16) | (g << 8) | g, metalness: 0.05 };
  }

  function paint() {
    restore();
    const setup = input.setup.value;
    const lib = input.library.value;
    if (!setup || !lib || !leaves.value.size) return;
    const parts = input.parts.value;
    const ledParts = new Set<string>();
    for (const led of setup.leds) {
      if (led.placement.type === "face" && led.placement.part) for (const i of instanceForPattern(led.placement.part, parts)) ledParts.add(i.id);
    }
    const hiddenIds: string[] = [];
    const sel = input.selectedIds.value;
    for (const inst of parts) {
      const leaf = leaves.value.get(inst.id);
      if (!leaf) continue;
      const eff = resolvePart(inst, setup.parts);
      if (!(visibility.value.get(inst.id) ?? eff.enabled)) {
        leaf.meshes.forEach((m) => (m.visible = false));
        leaf.lines.forEach((l) => (l.visible = false));
        hiddenIds.push(inst.id);
        continue;
      }
      const selected = sel.has(inst.id);
      const dim = input.dimOthers.value && sel.size > 0 && !selected;
      // "all transparent": every part becomes a ghost; the selected part stays a little more visible
      const ghost = input.allTransparent.value;
      const see = (opacity: number) => (ghost ? Math.min(opacity, selected ? 0.4 : 0.18) : opacity);
      let mat: THREE.MeshStandardMaterial;
      const emissive = selected ? 0x2563eb : 0;
      if (!eff.enabled) {
        // ignored by the simulation, but shown on request (model tree): a faint grey ghost
        mat = roleMaterial(0x94a3b8, { opacity: see(dim ? 0.08 : 0.2), emissive });
      } else if (ledParts.has(inst.id)) mat = roleMaterial(0xfacc15, { emissive: selected ? 0xfacc15 : 0xa16207, opacity: see(dim ? 0.25 : 1) });
      else if (eff.material && inst.closed && lib.materials[eff.material]?.mode !== "surface") {
        const mdoc = lib.materials[eff.material];
        const scatter = mdoc?.scattering && mdoc.scattering.type !== "none";
        mat = roleMaterial(scatter ? 0xe0ecff : 0x93c5fd, { opacity: see(dim ? 0.12 : scatter ? 0.62 : 0.4), emissive });
      } else {
        const smat = eff.material ? lib.materials[eff.material] : undefined;
        const fc = finishColor(smat?.mode === "surface" ? smat : lib.finishes[eff.finish]);
        mat = roleMaterial(fc.color, { metalness: fc.metalness, emissive, opacity: see(dim ? 0.2 : 1) });
      }
      for (const m of leaf.meshes) {
        saved.set(m, m.material);
        m.material = mat;
        m.renderOrder = mat.transparent ? 3 : 1;
      }
    }
    viewer.registerClippableMaterials?.(created);
    // the section caps are built from the visible geometry: rebuild them when parts appear or disappear
    const key = hiddenIds.join("|");
    if (key !== lastHiddenKey) {
      lastHiddenKey = key;
      if (viewer.activeCuttingPlanes?.value?.size > 0) viewer.refreshAllCaps?.();
    }
    paintVersion.value++;
    rebuildOverlay(); // face highlights follow their part when it is hidden or shown
  }

  // ── show / hide ─────────────────────────────────────────────────────────────────
  function enabledByDefault(id: string): boolean {
    const setup = input.setup.value;
    const inst = input.parts.value.find((p) => p.id === id);
    return setup && inst ? resolvePart(inst, setup.parts).enabled : true;
  }

  /** Leaf instances (those that own geometry) in the subtrees of the given viewer nodes. */
  function leafIdsUnder(nodeIds: number[]): string[] {
    const scene = viewer.scene.value as THREE.Scene | null;
    const out = new Set<string>();
    for (const nid of nodeIds) {
      scene?.getObjectById(nid)?.traverse((c) => {
        const id = c.userData?.cadreview_instanceId as string | undefined;
        if (id && leaves.value.has(id)) out.add(id);
      });
    }
    return [...out];
  }

  function setInstancesVisible(ids: Iterable<string>, visible: boolean) {
    const next = new Map(visibility.value);
    for (const id of ids) {
      if (visible === enabledByDefault(id)) next.delete(id);
      else next.set(id, visible);
    }
    visibility.value = next;
  }
  const setNodesVisible = (nodeIds: number[], visible: boolean) => setInstancesVisible(leafIdsUnder(nodeIds), visible);

  /** Everything visible, including parts that are ignored by the simulation (as ghosts). */
  function showAll() {
    setInstancesVisible(leaves.value.keys(), true);
  }

  /** Show only the given instances. */
  function isolate(keep: Set<string>) {
    const hide: string[] = [];
    for (const id of leaves.value.keys()) if (!keep.has(id)) hide.push(id);
    setInstancesVisible(hide, false);
    setInstancesVisible(keep, true);
  }

  /** True while any part of the model is not shown (hidden by hand, or ignored by the simulation). */
  const anyHidden = computed(() => {
    for (const id of leaves.value.keys()) if (!(visibility.value.get(id) ?? enabledByDefault(id))) return true;
    return false;
  });
  /** Instance ids of every part currently shown in the viewport. */
  const shownIds = computed(() => {
    const out: string[] = [];
    for (const id of leaves.value.keys()) if (visibility.value.get(id) ?? enabledByDefault(id)) out.push(id);
    return out;
  });

  // ── face overlays ─────────────────────────────────────────────────────────────
  const overlayMats = new Map<number, THREE.MeshBasicMaterial>();
  function overlayMaterial(color: number, opacity: number): THREE.MeshBasicMaterial {
    const key = color * 100 + Math.round(opacity * 100);
    let m = overlayMats.get(key);
    if (!m) {
      m = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        depthTest: true,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -4,
      });
      overlayMats.set(key, m);
      viewer.registerClippableMaterials?.([m]); // the section planes cut the face highlights too
    }
    return m;
  }

  function faceMesh(instanceId: string, face: number): { mesh: THREE.Mesh; positions: Float32Array } | null {
    const leaf = leaves.value.get(instanceId);
    if (!leaf) return null;
    for (const mesh of leaf.meshes) {
      if (!mesh.visible) continue;
      if (getBrepFaceData(mesh, face)) {
        const pos = getBrepFaceTrianglePositions(mesh, face);
        if (pos) return { mesh, positions: pos };
      }
    }
    return null;
  }

  function rebuildOverlay() {
    for (const child of [...overlay.children]) {
      overlay.remove(child);
      (child as THREE.Mesh).geometry?.dispose();
    }
    const scene = viewer.scene.value as THREE.Scene | null;
    if (!scene) return;
    if (!overlayAttached) {
      scene.add(overlay);
      overlayAttached = true;
    }
    const all = [...input.highlights.value, ...(hover.value ? [hover.value] : [])];
    for (const h of all) {
      const fm = faceMesh(h.instanceId, h.face);
      if (!fm) continue;
      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(fm.positions, 3));
      const mesh = new THREE.Mesh(geom, overlayMaterial(h.color, h.opacity ?? 0.7));
      mesh.matrixAutoUpdate = false;
      fm.mesh.updateWorldMatrix(true, false);
      mesh.matrix.copy(fm.mesh.matrixWorld);
      mesh.renderOrder = 10;
      mesh.raycast = () => {};
      overlay.add(mesh);
    }
  }

  // ── picking ─────────────────────────────────────────────────────────────────────
  function pickFace(event: MouseEvent | PointerEvent): PickedFace | null {
    const hits = viewer.raycastFromCursor(event) as THREE.Intersection[];
    for (const hit of hits) {
      const mesh = hit.object as THREE.Mesh;
      if (!(mesh instanceof THREE.Mesh) || hit.faceIndex == null) continue;
      if (mesh.parent === overlay) continue;
      const node = viewer.findParentInstanceNode(mesh) as THREE.Object3D | null;
      const instanceId = node?.userData?.cadreview_instanceId as string | undefined;
      const face = getFaceIdAtTriangle(mesh, hit.faceIndex);
      if (instanceId && face !== null) return { instanceId, face, point: hit.point.clone() };
    }
    return null;
  }

  function pickPart(event: MouseEvent | PointerEvent): string | null {
    return pickFace(event)?.instanceId ?? null;
  }

  function setHover(h: FaceHighlight | null) {
    const prev = hover.value;
    if (prev?.instanceId === h?.instanceId && prev?.face === h?.face && prev?.color === h?.color) return;
    hover.value = h;
    rebuildOverlay();
  }

  // ── wiring ──────────────────────────────────────────────────────────────────────
  watch(
    () => viewer.modelLoaded.value,
    (loaded: boolean) => {
      if (loaded) {
        index();
        paint();
        rebuildOverlay();
      }
    },
    { immediate: true },
  );
  watch(
    [() => input.setup.value, () => input.library.value, () => input.parts.value, () => input.selectedIds.value, () => input.dimOthers.value, () => input.allTransparent.value, visibility],
    () => paint(),
    { deep: true },
  );
  watch(() => input.highlights.value, () => rebuildOverlay(), { deep: true });

  onBeforeUnmount(() => {
    restore();
    for (const child of [...overlay.children]) (child as THREE.Mesh).geometry?.dispose();
    overlay.removeFromParent();
    viewer.unregisterClippableMaterials?.([...overlayMats.values()]);
    overlayMats.forEach((m) => m.dispose());
  });

  return { index, paint, pickFace, pickPart, setHover, hover, leaves, paintVersion, anyHidden, shownIds, leafIdsUnder, setNodesVisible, showAll, isolate };
}
