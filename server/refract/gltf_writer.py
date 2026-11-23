"""Minimal GLB writer (no external dependency).

Produces files compatible with the Synapse viewer: custom per-vertex
``_FACE_ID`` / ``_EDGE_ID`` attributes, node ``extras``, column-major node
matrices and PBR materials.
"""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

_FLOAT = 5126
_UINT = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963


class GltfBuilder:
    def __init__(self, generator: str = "refract") -> None:
        self._bin = bytearray()
        self.buffer_views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []
        self.meshes: list[dict[str, Any]] = []
        self.materials: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.root_nodes: list[int] = []
        self._material_cache: dict[tuple, int] = {}
        self._generator = generator

    # -- buffers -----------------------------------------------------------
    def _append(self, data: bytes, target: int | None) -> int:
        # 4-byte alignment
        pad = (-len(self._bin)) % 4
        if pad:
            self._bin.extend(b"\x00" * pad)
        offset = len(self._bin)
        self._bin.extend(data)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def add_float_accessor(self, arr: np.ndarray, type_: str, bounds: bool = False) -> int:
        arr = np.ascontiguousarray(arr, dtype="<f4")
        comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[type_]
        arr = arr.reshape(-1, comps)
        view = self._append(arr.tobytes(), _ARRAY_BUFFER)
        acc: dict[str, Any] = {
            "bufferView": view,
            "componentType": _FLOAT,
            "count": int(arr.shape[0]),
            "type": type_,
        }
        if bounds and arr.shape[0]:
            acc["min"] = [float(v) for v in arr.min(axis=0)]
            acc["max"] = [float(v) for v in arr.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def add_index_accessor(self, arr: np.ndarray) -> int:
        arr = np.ascontiguousarray(arr, dtype="<u4").reshape(-1)
        view = self._append(arr.tobytes(), _ELEMENT_ARRAY_BUFFER)
        self.accessors.append(
            {
                "bufferView": view,
                "componentType": _UINT,
                "count": int(arr.shape[0]),
                "type": "SCALAR",
            }
        )
        return len(self.accessors) - 1

    # -- materials / meshes / nodes ---------------------------------------
    def material(
        self,
        rgb: tuple[float, float, float],
        alpha: float = 1.0,
        emissive: tuple[float, float, float] | None = None,
        name: str | None = None,
    ) -> int:
        key = (round(rgb[0], 3), round(rgb[1], 3), round(rgb[2], 3), round(alpha, 3), emissive)
        if key in self._material_cache:
            return self._material_cache[key]
        mat: dict[str, Any] = {
            "name": name or f"mat_{key[0]}_{key[1]}_{key[2]}",
            "pbrMetallicRoughness": {
                "baseColorFactor": [rgb[0], rgb[1], rgb[2], alpha],
                "metallicFactor": 0.1,
                "roughnessFactor": 0.5,
            },
            "doubleSided": True,
        }
        if emissive is not None:
            mat["emissiveFactor"] = list(emissive)
        if alpha < 1.0:
            mat["alphaMode"] = "BLEND"
        self.materials.append(mat)
        self._material_cache[key] = len(self.materials) - 1
        return self._material_cache[key]

    def add_triangle_mesh(
        self,
        name: str,
        positions: np.ndarray,
        normals: np.ndarray,
        indices: np.ndarray,
        material: int,
        face_ids: np.ndarray | None = None,
    ) -> int:
        attrs = {
            "POSITION": self.add_float_accessor(positions, "VEC3", bounds=True),
            "NORMAL": self.add_float_accessor(normals, "VEC3"),
        }
        if face_ids is not None:
            attrs["_FACE_ID"] = self.add_float_accessor(face_ids, "SCALAR")
        prim = {
            "attributes": attrs,
            "indices": self.add_index_accessor(indices),
            "mode": 4,
            "material": material,
        }
        self.meshes.append({"name": name, "primitives": [prim]})
        return len(self.meshes) - 1

    def add_line_mesh(
        self,
        name: str,
        positions: np.ndarray,
        indices: np.ndarray,
        edge_ids: np.ndarray | None = None,
    ) -> int:
        attrs = {"POSITION": self.add_float_accessor(positions, "VEC3", bounds=True)}
        if edge_ids is not None:
            attrs["_EDGE_ID"] = self.add_float_accessor(edge_ids, "SCALAR")
        prim = {"attributes": attrs, "indices": self.add_index_accessor(indices), "mode": 1}
        self.meshes.append({"name": name, "primitives": [prim]})
        return len(self.meshes) - 1

    def add_node(
        self,
        name: str,
        parent: int | None = None,
        mesh: int | None = None,
        matrix: list[float] | None = None,
        extras: dict[str, Any] | None = None,
    ) -> int:
        node: dict[str, Any] = {"name": name}
        if mesh is not None:
            node["mesh"] = mesh
        if matrix is not None:
            node["matrix"] = matrix
        if extras:
            node["extras"] = extras
        self.nodes.append(node)
        idx = len(self.nodes) - 1
        if parent is None:
            self.root_nodes.append(idx)
        else:
            self.nodes[parent].setdefault("children", []).append(idx)
        return idx

    # -- output ------------------------------------------------------------
    def to_glb(self) -> bytes:
        doc: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": self._generator},
            "scene": 0,
            "scenes": [{"nodes": self.root_nodes}],
            "nodes": self.nodes,
        }
        if self.meshes:
            doc["meshes"] = self.meshes
        if self.materials:
            doc["materials"] = self.materials
        if self.accessors:
            doc["accessors"] = self.accessors
        if self.buffer_views:
            doc["bufferViews"] = self.buffer_views
            doc["buffers"] = [{"byteLength": len(self._bin)}]

        json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * ((-len(json_bytes)) % 4)
        bin_bytes = bytes(self._bin) + b"\x00" * ((-len(self._bin)) % 4)

        total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
        out = bytearray()
        out += struct.pack("<4sII", b"glTF", 2, total)
        out += struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
        if bin_bytes:
            out += struct.pack("<I4s", len(bin_bytes), b"BIN\x00") + bin_bytes
        return bytes(out)


def compute_vertex_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Area-weighted smooth vertex normals for an indexed triangle mesh."""
    tri = indices.reshape(-1, 3)
    p0, p1, p2 = positions[tri[:, 0]], positions[tri[:, 1]], positions[tri[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)  # magnitude == 2 * area
    normals = np.zeros_like(positions, dtype=np.float64)
    for k in range(3):
        np.add.at(normals, tri[:, k], fn)
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    lens[lens == 0] = 1.0
    return (normals / lens).astype(np.float32)
