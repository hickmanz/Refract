// Draws simulation results inside the 3D view: the exit-surface map draped on the CAD and recorded ray paths.
import { onBeforeUnmount } from "vue";
import * as THREE from "three";
import type { AnalysisRayPath } from "../api";

export type RayColorMode = "led" | "flux" | "bounce" | "event" | "group" | "face";
/** A layer of space beside a cut plane: from the plane (`origin`) up to `thickness` mm in the direction `normal`. */
export interface Slab {
  origin: number[];
  normal: number[];
  thickness: number;
}

/** The part of the segment a→b inside the slab, or null when none of it is. */
function clipToSlab(a: number[], b: number[], s: Slab): [number[], number[]] | null {
  const d = (p: number[]) => (p[0] - s.origin[0]) * s.normal[0] + (p[1] - s.origin[1]) * s.normal[1] + (p[2] - s.origin[2]) * s.normal[2];
  const da = d(a);
  const db = d(b);
  const lo = 0;
  const hi = s.thickness;
  if ((da < lo && db < lo) || (da > hi && db > hi)) return null;
  const span = db - da;
  let t0 = 0;
  let t1 = 1;
  if (Math.abs(span) > 1e-12) {
    const ta = (lo - da) / span;
    const tb = (hi - da) / span;
    t0 = Math.max(0, Math.min(ta, tb));
    t1 = Math.min(1, Math.max(ta, tb));
    if (t0 >= t1) return null;
  }
  const at = (t: number) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  return [at(t0), at(t1)];
}

export interface RayStyle {
  /** Draw only the part of the rays that lies in this layer beside the section plane (the "ray fan"). */
  slab?: Slab | null;
  color: RayColorMode;
  density: boolean;
  /** Draw only this many segments of every ray (0 = all). */
  maxBounce: number;
  /** Cut every ray at the first time it reaches the exit surface (what happens to it afterwards does not matter for the pattern). */
  stopAtExit: boolean;
  /** The detectors (indices) that end a ray when `stopAtExit` is on; null = the first exit event of any; an empty list = none (all the detectors let the light go on). */
  stopDets?: number[] | null;
  opacity: number;
  densityGain: number;
  focusFace: number | null;
  /** In the focus-surface colouring: also draw the other segments of the rays (faint grey). */
  showOthers: boolean;
  ledColor: (key: string, led: string) => number;
  groupColor: (group: number) => number;
  eventColor: (event: number) => number;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Viewer = any;

export function useResultsOverlay(viewer: Viewer) {
  const group = new THREE.Group();
  group.name = "refract-results";
  let attached = false;
  const disposables: { dispose: () => void }[] = [];

  function attach() {
    const scene = viewer.scene.value as THREE.Scene | null;
    if (scene && !attached) {
      scene.add(group);
      attached = true;
    }
  }

  /** The section planes cut these overlays like the model itself. */
  function clippable(register: boolean) {
    const mats = group.children.map((c) => (c as THREE.Mesh).material as THREE.Material).filter(Boolean);
    if (register) viewer.registerClippableMaterials?.(mats);
    else viewer.unregisterClippableMaterials?.(mats);
  }

  function clear() {
    clearRays();
    clippable(false);
    for (const child of [...group.children]) group.remove(child);
    disposables.splice(0).forEach((d) => d.dispose());
  }

  /** Quads textured with canvases (row 0 of the canvas = detector v-min). */
  function setMaps(maps: { corners: number[][]; normal: number[]; canvas: HTMLCanvasElement; mesh?: { positions: number[]; normals: number[]; uv: number[] } }[], offset: number) {
    attach();
    for (const m of maps) {
      const c = m.corners.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      const n = new THREE.Vector3(m.normal[0], m.normal[1], m.normal[2]).normalize().multiplyScalar(offset);
      const geom = new THREE.BufferGeometry();
      if (m.mesh) {
        // unwrapped (curved) exit surface: the CAD triangles themselves, pushed out along their normals
        const pos = m.mesh.positions.map((x, i) => x + m.mesh!.normals[i] * offset);
        geom.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
        geom.setAttribute("uv", new THREE.Float32BufferAttribute(m.mesh.uv, 2));
      } else {
        const pts = [c[0], c[1], c[2], c[3]].map((p) => p.clone().add(n));
        geom.setAttribute("position", new THREE.Float32BufferAttribute(pts.flatMap((p) => [p.x, p.y, p.z]), 3));
        geom.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
        geom.setIndex([0, 1, 2, 0, 2, 3]);
      }
      const tex = new THREE.CanvasTexture(m.canvas);
      tex.flipY = false;
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.magFilter = THREE.NearestFilter;
      // The map is a surface like any other: depth-tested and depth-writing, drawn in the opaque pass. It only needs to win against
      // the CAD face it lies on: the small push out along the normal (`offset`) does most of that, so the depth bias is kept tiny
      // (a large one scales with the slope of the surface and pulled the map in front of nearby geometry).
      // It must NOT be a transparent material: the rays, the see-through parts and the markers are transparent and do not write depth,
      // so a transparent map is drawn after them and paints over them even where they are in front of it. In the opaque pass the map
      // writes its depth first, and whatever lies behind it is hidden while whatever lies in front is blended on top. `alphaTest`
      // drops texels without data so that they neither show nor hide anything.
      const mat = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: false, alphaTest: 0.5, depthTest: true, depthWrite: true, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1, toneMapped: false });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.renderOrder = 12;
      mesh.raycast = () => {};
      group.add(mesh);
      disposables.push(geom, mat, tex);
    }
    clippable(true);
  }

  /**
   * Two arrows at the origin corner of a map (red: u, the map's left-to-right; green: v, the map's bottom-to-top), so the
   * orientation of the 2D maps can be matched with what the 3D view shows. Drawn on top of the model.
   */
  function setMapAxes(maps: { corners: number[][]; normal: number[] }[]) {
    attach();
    for (const m of maps) {
      const [c0, c1, , c3] = m.corners.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      const du = c1.clone().sub(c0);
      const dv = c3.clone().sub(c0);
      const len = Math.max(Math.min(du.length(), dv.length()), Math.max(du.length(), dv.length()) * 0.1);
      const n = new THREE.Vector3(m.normal[0], m.normal[1], m.normal[2]).normalize();
      const origin = c0.clone().addScaledVector(du.clone().normalize(), len * 0.15).addScaledVector(dv.clone().normalize(), len * 0.15).addScaledVector(n, len * 0.05);
      for (const [dir, color] of [
        [du, 0xef4444],
        [dv, 0x22c55e],
      ] as [THREE.Vector3, number][]) {
        const arrow = new THREE.ArrowHelper(dir.clone().normalize(), origin, len, color, len * 0.4, len * 0.28);
        for (const part of [arrow.line, arrow.cone] as THREE.Mesh[]) {
          const mat = part.material as THREE.Material;
          mat.depthTest = false;
          mat.depthWrite = false;
          mat.transparent = true; // drawn in the same pass as the map, after it (renderOrder), so the map cannot cover them
          part.renderOrder = 14;
          part.raycast = () => {};
          disposables.push(part.geometry, mat);
        }
        group.add(arrow);
      }
    }
  }

  /**
   * One blue arrow per receiver detector: from the middle of its map towards the viewer (the axis of the viewing cone), so the
   * direction in which the reflected light is measured can be seen on the model. `size` is the length of the arrow (model units).
   */
  function setViewArrows(items: { origin: number[]; view: number[]; size: number }[]) {
    attach();
    for (const it of items) {
      const dir = new THREE.Vector3(it.view[0], it.view[1], it.view[2]).normalize();
      const origin = new THREE.Vector3(it.origin[0], it.origin[1], it.origin[2]);
      const arrow = new THREE.ArrowHelper(dir, origin, it.size, 0x3b82f6, it.size * 0.22, it.size * 0.14);
      for (const part of [arrow.line, arrow.cone] as THREE.Mesh[]) {
        const mat = part.material as THREE.Material;
        mat.depthTest = false;
        mat.depthWrite = false;
        mat.transparent = true;
        part.renderOrder = 14;
        part.raycast = () => {};
        disposables.push(part.geometry, mat);
      }
      group.add(arrow);
    }
  }

  /**
   * Replayed rays with a colour per segment. A segment runs from vertex k to vertex k+1 of a path; the colour comes from the
   * weight, the bounce number, the event at its end, the surface it left or the LED. `focusFace` colours the segments that
   * arrive at / leave that CAD face and greys out the rest. `maxBounce` draws only the first segments of every ray (0 = all).
   * In density mode the segments add up (additive blending, no depth test) so crowded channels glow.
   */
  let rayObject: THREE.LineSegments | null = null;
  /** Remove the replayed rays only (the map and the LED markers stay). */
  function clearRays() {
    if (!rayObject) return;
    viewer.unregisterClippableMaterials?.([rayObject.material as THREE.Material]);
    group.remove(rayObject);
    rayObject.geometry.dispose();
    (rayObject.material as THREE.Material).dispose();
    rayObject = null;
  }

  function setRays(paths: AnalysisRayPath[], style: RayStyle) {
    attach();
    clearRays();
    const pos: number[] = [];
    const col: number[] = [];
    const tmp = new THREE.Color();
    const maxLen = Math.max(2, ...paths.map((p) => p.points.length));
    const dim = style.density ? style.densityGain : 1;
    for (const p of paths) {
      const m = p.points.length;
      let last = style.maxBounce > 0 ? Math.min(m - 1, style.maxBounce) : m - 1;
      if (style.stopAtExit) {
        // EV_EXIT = 8. A ray that passes one detector can arrive at another later: cut it at the detectors that count here.
        // (a list of detectors that end the ray: a ray that arrives only on detectors that let the light go on is drawn to its end)
        const dets = style.stopDets;
        const at = dets && p.det ? p.event.findIndex((e, i) => e === 8 && dets.includes(p.det![i])) : p.event.indexOf(8);
        if (at > 0) last = Math.min(last, at);
      }
      for (let k = 0; k < last; k++) {
        const w = p.w[k] ?? 1;
        let faded = 1;
        switch (style.color) {
          case "led":
            tmp.setHex(style.ledColor(p.key, p.led));
            break;
          case "flux": {
            const t = Math.min(1, Math.max(0, 1 + Math.log10(Math.max(w, 1e-3)) / 3));
            tmp.setHSL((1 - t) * 0.66, 1, 0.5);
            break;
          }
          case "bounce":
            tmp.setHSL((1 - k / Math.max(maxLen - 2, 1)) * 0.72, 1, 0.5);
            break;
          case "event":
            tmp.setHex(style.eventColor(p.event[k + 1] ?? 9));
            break;
          case "group":
            tmp.setHex(k === 0 ? 0xe5e7eb : style.groupColor(p.group[k] ?? -1));
            break;
          case "face": {
            const f = style.focusFace;
            if (f !== null && p.gface[k + 1] === f) tmp.setHex(0x06b6d4);
            else if (f !== null && p.gface[k] === f) tmp.setHex(0xf97316);
            else if (style.showOthers) {
              tmp.setHex(0x9ca3af);
              faded = 0.25;
            } else continue; // the rest of the ray is not drawn: only what arrives at / leaves the surface
            break;
          }
        }
        const gain = (style.density ? 0.25 + 0.75 * Math.min(1, Math.sqrt(Math.max(w, 0))) : 1) * dim * faded;
        let a = p.points[k];
        let b = p.points[k + 1];
        if (style.slab) {
          const c = clipToSlab(a, b, style.slab);
          if (!c) continue;
          [a, b] = c;
        }
        pos.push(...a, ...b);
        col.push(tmp.r * gain, tmp.g * gain, tmp.b * gain, tmp.r * gain, tmp.g * gain, tmp.b * gain);
      }
    }
    if (!pos.length) return;
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geom.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    const mat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: style.density ? 1 : style.opacity,
      blending: style.density ? THREE.AdditiveBlending : THREE.NormalBlending,
      depthTest: !style.density,
      depthWrite: false,
    });
    const lines = new THREE.LineSegments(geom, mat);
    lines.renderOrder = 11;
    lines.raycast = () => {};
    group.add(lines);
    rayObject = lines;
    viewer.registerClippableMaterials?.([mat]);
  }

  /**
   * One marker per LED: an outline of the emitting area on the emitter face and a slim arrow from its centre along the
   * emission axis. The arrow is sized from the emitter (about 1.5 x its larger side), not from the model.
   * `diag` (model diagonal) only bounds the size and is the fallback when the emitter size is unknown.
   */
  function setLedMarkers(items: { origin: number[]; axis: number[]; color: number; ex?: number[]; ey?: number[]; size?: number[] }[], diag: number) {
    attach();
    const V = (a: number[]) => new THREE.Vector3(a[0], a[1], a[2]);
    for (const it of items) {
      const axis = V(it.axis).normalize();
      const origin = V(it.origin);
      const emitter = it.size && it.size.length >= 2 && it.size[0] > 0 && it.size[1] > 0 ? Math.max(it.size[0], it.size[1]) : 0;
      const len = THREE.MathUtils.clamp(emitter > 0 ? emitter * 1.5 : diag * 0.01, diag * 0.004, diag * 0.05);
      const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);

      const headLen = len * 0.3;
      const shaftLen = len - headLen;
      const shaftGeom = new THREE.CylinderGeometry(len * 0.035, len * 0.035, shaftLen, 8);
      const shaftMat = new THREE.MeshBasicMaterial({ color: it.color });
      const shaft = new THREE.Mesh(shaftGeom, shaftMat);
      shaft.position.copy(origin).addScaledVector(axis, shaftLen / 2);
      shaft.quaternion.copy(quat);
      const headGeom = new THREE.ConeGeometry(len * 0.11, headLen, 12);
      const headMat = new THREE.MeshBasicMaterial({ color: it.color });
      const head = new THREE.Mesh(headGeom, headMat);
      head.position.copy(origin).addScaledVector(axis, shaftLen + headLen / 2);
      head.quaternion.copy(quat);
      for (const m of [shaft, head]) {
        m.raycast = () => {};
        m.renderOrder = 11;
        group.add(m);
      }
      disposables.push(shaftGeom, shaftMat, headGeom, headMat);

      if (emitter > 0 && it.ex && it.ey && it.size) {
        const ex = V(it.ex).multiplyScalar(it.size[0] / 2);
        const ey = V(it.ey).multiplyScalar(it.size[1] / 2);
        const lift = axis.clone().multiplyScalar(len * 0.01); // keep the outline off the face (no z-fighting)
        const corners = [
          origin.clone().sub(ex).sub(ey),
          origin.clone().add(ex).sub(ey),
          origin.clone().add(ex).add(ey),
          origin.clone().sub(ex).add(ey),
        ].map((p) => p.add(lift));
        const outlineGeom = new THREE.BufferGeometry().setFromPoints(corners);
        const outlineMat = new THREE.LineBasicMaterial({ color: it.color });
        const outline = new THREE.LineLoop(outlineGeom, outlineMat);
        outline.raycast = () => {};
        outline.renderOrder = 11;
        group.add(outline);
        disposables.push(outlineGeom, outlineMat);
      }
    }
    clippable(true);
  }

  /**
   * The flux through a section plane, as a quad on the plane (transparent where no light passes). It lives apart from the
   * rest of the overlay (it is not rebuilt with it) and is not cut by the section planes, so it stays on the cut.
   */
  let sliceMesh: THREE.Mesh | null = null;
  function clearSlice() {
    if (!sliceMesh) return;
    sliceMesh.removeFromParent();
    sliceMesh.geometry.dispose();
    const mat = sliceMesh.material as THREE.MeshBasicMaterial;
    mat.map?.dispose();
    mat.dispose();
    sliceMesh = null;
  }
  function setSlice(s: { corners: number[][]; normal: number[]; canvas: HTMLCanvasElement } | null, lift: number) {
    clearSlice();
    const scene = viewer.scene.value as THREE.Scene | null;
    if (!s || !scene) return;
    const n = new THREE.Vector3(s.normal[0], s.normal[1], s.normal[2]).normalize().multiplyScalar(lift);
    const pts = s.corners.map((p) => new THREE.Vector3(p[0], p[1], p[2]).add(n));
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(pts.flatMap((p) => [p.x, p.y, p.z]), 3));
    geom.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
    geom.setIndex([0, 1, 2, 0, 2, 3]);
    const tex = new THREE.CanvasTexture(s.canvas);
    tex.flipY = false;
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.magFilter = THREE.LinearFilter;
    const mat = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: true, polygonOffset: true, polygonOffsetFactor: -8, polygonOffsetUnits: -8, toneMapped: false, depthWrite: false });
    sliceMesh = new THREE.Mesh(geom, mat);
    sliceMesh.renderOrder = 13;
    sliceMesh.raycast = () => {};
    scene.add(sliceMesh);
  }

  onBeforeUnmount(() => {
    clear();
    clearSlice();
    group.removeFromParent();
  });

  return { clear, clearRays, setMaps, setMapAxes, setViewArrows, setRays, setLedMarkers, setSlice, clearSlice };
}
