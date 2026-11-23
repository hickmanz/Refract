"""STEP import through OpenCASCADE XDE.

Flattens the XDE label tree into a list of *instances* (one per assembly
occurrence) that reference shared *definitions* (one per unique part).
Leaf parts that contain more than one solid are expanded into one "Body N"
child per solid so that every solid can be checked/hidden/coloured on its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.collections import Sequence_TDF_Label

Color = tuple[float, float, float]
DEFAULT_COLOR: Color = (0.72, 0.74, 0.78)


@dataclass
class Definition:
    key: str
    name: str
    shape: TopoDS_Shape
    solids: list[TopoDS_Shape]
    color: Color | None


@dataclass
class Instance:
    index: int
    id: str
    name: str
    path: str
    parent: int | None
    def_key: str
    local_loc: TopLoc_Location
    global_loc: TopLoc_Location
    color: Color | None
    children: list[int] = field(default_factory=list)
    is_leaf: bool = True


@dataclass
class ImportedModel:
    instances: list[Instance]
    definitions: dict[str, Definition]
    roots: list[int]
    warnings: list[str]


def _entry(label: TDF_Label) -> str:
    s = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, s)
    return s.ToCString()


def _name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        try:
            return str(attr.Get().ToExtString()).strip()
        except Exception:
            return ""
    return ""


def _make_id(path: str) -> str:
    return "i_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


class _Importer:
    def __init__(self, doc: TDocStd_Document, progress: Callable[[str], None] | None) -> None:
        main = doc.Main()
        self.shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(main)
        self.color_tool = XCAFDoc_DocumentTool.ColorTool_s(main)
        self.instances: list[Instance] = []
        self.definitions: dict[str, Definition] = {}
        self.warnings: list[str] = []
        self.progress = progress

    # -- colours -----------------------------------------------------------
    def _label_color(self, label: TDF_Label) -> Color | None:
        c = Quantity_Color()
        for t in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            try:
                if self.color_tool.GetColor(label, t, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception:
                pass
        return None

    def _shape_color(self, shape: TopoDS_Shape) -> Color | None:
        if shape.IsNull():
            return None
        c = Quantity_Color()
        for t in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            try:
                if self.color_tool.GetColor(shape, t, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception:
                pass
        # first coloured face
        ex = TopExp_Explorer(shape, TopAbs_FACE)
        checked = 0
        while ex.More() and checked < 64:
            f = ex.Current()
            try:
                if self.color_tool.GetColor(f, XCAFDoc_ColorType.XCAFDoc_ColorSurf, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception:
                pass
            ex.Next()
            checked += 1
        return None

    # -- definitions -------------------------------------------------------
    def _definition(self, def_label: TDF_Label, fallback_name: str, leaf: bool) -> Definition:
        key = _entry(def_label)
        d = self.definitions.get(key)
        if d is not None:
            return d
        solids: list[TopoDS_Shape] = []
        if leaf:
            shape = XCAFDoc_ShapeTool.GetShape_s(def_label)
            if not shape.IsNull():
                ex = TopExp_Explorer(shape, TopAbs_SOLID)
                while ex.More():
                    solids.append(TopoDS.Solid(ex.Current()))
                    ex.Next()
            color = self._label_color(def_label) or self._shape_color(shape)
        else:
            shape = TopoDS_Shape()
            color = self._label_color(def_label)
        d = Definition(key, _name(def_label) or fallback_name or key, shape, solids, color)
        self.definitions[key] = d
        return d

    # -- traversal ---------------------------------------------------------
    def walk(
        self,
        label: TDF_Label,
        parent: Instance | None,
        sibling_names: dict[str, int],
        inherited: Color | None,
    ) -> Instance | None:
        is_ref = XCAFDoc_ShapeTool.IsReference_s(label)
        def_label = label
        if is_ref:
            referred = TDF_Label()
            if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred):
                def_label = referred

        local_loc = XCAFDoc_ShapeTool.GetLocation_s(label) if is_ref else TopLoc_Location()
        global_loc = local_loc if parent is None else parent.global_loc * local_loc

        definition_name = _name(def_label)
        name = _name(label) or definition_name or f"Part {_entry(def_label)}"

        n = sibling_names.get(name, 0)
        sibling_names[name] = n + 1
        unique = name if n == 0 else f"{name}#{n + 1}"
        path = unique if parent is None else f"{parent.path}/{unique}"

        comps = Sequence_TDF_Label()
        is_asm = XCAFDoc_ShapeTool.GetComponents_s(def_label, comps)
        n_comps = comps.Length() if is_asm else 0

        label_color = self._label_color(label) if is_ref else None
        color = label_color or self._label_color(def_label) or inherited

        definition = self._definition(def_label, name, n_comps == 0)
        if n_comps == 0:
            color = color or definition.color

        inst = Instance(
            index=len(self.instances),
            id=_make_id(path),
            name=name,
            path=path,
            parent=parent.index if parent else None,
            def_key=definition.key,
            local_loc=local_loc,
            global_loc=global_loc,
            color=color,
            is_leaf=n_comps == 0,
        )
        self.instances.append(inst)
        if parent is not None:
            parent.children.append(inst.index)

        if n_comps > 0:
            child_names: dict[str, int] = {}
            for i in range(1, n_comps + 1):
                self.walk(comps.Value(i), inst, child_names, color)
            return inst

        # Leaf: expand multi-solid parts into bodies.
        if len(definition.solids) > 1:
            inst.is_leaf = False
            body_names: dict[str, int] = {}
            for k, solid in enumerate(definition.solids, start=1):
                bkey = f"{definition.key}#b{k}"
                if bkey not in self.definitions:
                    self.definitions[bkey] = Definition(
                        bkey,
                        f"{definition.name} body {k}",
                        solid,
                        [solid],
                        self._shape_color(solid) or definition.color,
                    )
                bname = f"Body {k}"
                body_names[bname] = 1
                bpath = f"{path}/{bname}"  # path/id stay "Body N" so saved setups keep resolving
                child = Instance(
                    index=len(self.instances),
                    id=_make_id(bpath),
                    name=f"{name} - {bname}",
                    path=bpath,
                    parent=inst.index,
                    def_key=bkey,
                    local_loc=TopLoc_Location(),
                    global_loc=global_loc,
                    color=color,
                    is_leaf=True,
                )
                child.color = color or self.definitions[bkey].color
                self.instances.append(child)
                inst.children.append(child.index)
        return inst


def import_step(path: str, progress: Callable[[str], None] | None = None) -> ImportedModel:
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)

    if progress:
        progress("Reading STEP file")
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Could not read STEP file (status {status})")
    if progress:
        progress("Transferring STEP data")
    if not reader.Transfer(doc):
        raise RuntimeError("STEP transfer to XDE document failed")

    importer = _Importer(doc, progress)
    free = Sequence_TDF_Label()
    importer.shape_tool.GetFreeShapes(free)
    roots: list[int] = []
    names: dict[str, int] = {}
    for i in range(1, free.Length() + 1):
        inst = importer.walk(free.Value(i), None, names, None)
        if inst is not None:
            roots.append(inst.index)

    if not importer.instances:
        raise RuntimeError("The STEP file does not contain any shapes")

    no_solid = [
        i for i in importer.instances if i.is_leaf and not importer.definitions[i.def_key].solids
    ]
    if no_solid:
        importer.warnings.append(
            f"{len(no_solid)} part(s) contain no solid geometry (surfaces/shells only) "
            "and can only be used as opaque shells in the optical simulation."
        )
    return ImportedModel(importer.instances, importer.definitions, roots, importer.warnings)
