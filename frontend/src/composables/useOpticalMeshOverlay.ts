// Draws the optical mesh (the triangles the ray tracer uses, finer than the display mesh) of some parts as lines over the
// model, coloured by how far each facet departs from the smooth CAD surface.
import { onBeforeUnmount } from "vue";
import * as THREE from "three";
import type { OpticalMesh } from "../api";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Viewer = any;

/** Facet angle (degrees) drawn at the red end of the scale. */
export const FACET_DEG_MAX = 6;

export function useOpticalMeshOverlay(viewer: Viewer) {
  let lines: THREE.LineSegments | null = null;

  function clear() {
    if (!lines) return;
    viewer.unregisterClippableMaterials?.([lines.material as THREE.Material]);
    lines.removeFromParent();
    lines.geometry.dispose();
    (lines.material as THREE.Material).dispose();
    lines = null;
  }

  /**
   * Edges of every triangle, each shared edge once, in the colour of the steeper of its two triangles. Flat faces grey.
   * `lift` (mm) moves each triangle's edges off the shaded surface along its normal so they do not flicker into it.
   */
  function set(mesh: OpticalMesh | null, lift: number) {
    clear();
    const scene = viewer.scene.value as THREE.Scene | null;
    if (!mesh || !scene || mesh.indices.length === 0) return;
    const nt = mesh.indices.length / 3;
    const idx = mesh.indices;
    const p = mesh.positions;
    const nv = p.length / 3;

    // each edge once: the first triangle that has it supplies the position (lift direction), the steeper triangle the colour
    const slotOf = new Map<number, number>();
    const eFrom = new Uint32Array(nt * 3);
    const eTo = new Uint32Array(nt * 3);
    const eTri = new Uint32Array(nt * 3);
    const eDeg = new Float32Array(nt * 3);
    let ne = 0;
    for (let t = 0; t < nt; t++) {
      const deg = mesh.facetDeg[t];
      for (let k = 0; k < 3; k++) {
        const a = idx[3 * t + k];
        const b = idx[3 * t + ((k + 1) % 3)];
        const key = (a < b ? a : b) * nv + (a < b ? b : a);
        const s = slotOf.get(key);
        if (s === undefined) {
          slotOf.set(key, ne);
          eFrom[ne] = a;
          eTo[ne] = b;
          eTri[ne] = t;
          eDeg[ne] = deg;
          ne++;
        } else if (deg > eDeg[s]) eDeg[s] = deg;
      }
    }
    slotOf.clear();

    const pos = new Float32Array(ne * 6);
    const col = new Float32Array(ne * 6);
    const c = new THREE.Color();
    const va = new THREE.Vector3();
    const vb = new THREE.Vector3();
    const vc = new THREE.Vector3();
    const n = new THREE.Vector3();
    const e = new THREE.Vector3();
    for (let s = 0; s < ne; s++) {
      const deg = eDeg[s];
      if (deg < 0.05) c.setHex(0x9ca3af);
      else c.setHSL((1 - Math.min(1, deg / FACET_DEG_MAX)) * 0.33, 1, 0.5); // green (smooth) .. red (coarse)
      const t = eTri[s];
      va.fromArray(p, 3 * idx[3 * t]);
      vb.fromArray(p, 3 * idx[3 * t + 1]);
      vc.fromArray(p, 3 * idx[3 * t + 2]);
      n.subVectors(vb, va).cross(e.subVectors(vc, va));
      if (n.lengthSq() > 0) n.normalize().multiplyScalar(lift);
      for (let k = 0; k < 2; k++) {
        const v = k === 0 ? eFrom[s] : eTo[s];
        const o = s * 6 + k * 3;
        pos[o] = p[3 * v] + n.x;
        pos[o + 1] = p[3 * v + 1] + n.y;
        pos[o + 2] = p[3 * v + 2] + n.z;
        col[o] = c.r;
        col[o + 1] = c.g;
        col[o + 2] = c.b;
      }
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(col, 3));
    const mat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false });
    lines = new THREE.LineSegments(geom, mat);
    lines.name = "refract-optical-mesh";
    lines.renderOrder = 10;
    lines.raycast = () => {};
    scene.add(lines);
    viewer.registerClippableMaterials?.([mat]);
  }

  onBeforeUnmount(clear);
  return { set, clear };
}
