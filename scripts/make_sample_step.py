"""Generate a sample 5-LED light bar STEP assembly (end-to-end fixture for Refract).

    python scripts/make_sample_step.py [output.step] [--variant a|b]

Layout (mm, X along the bar):

    z = +3.2  ------------------------------  light pipe top face (exit surface, rounded long edges)
    z =  0.2  ------------------------------  light pipe bottom (light enters here)
              |   white reflector tray     |   open cavity, cavity depth = 3.2 (A) or 4.2 (B, deeper)
    z = -3.0  [LED]  [LED]  [LED]  [LED]  [LED]   LED packages (top face is the emitter), on the PCB
    z = -4.0  ============ PCB ============

Parts: PCB, Reflector Tray, Light Pipe, LED:1 .. LED:5 (one shared LED definition).
Revision B has a deeper cavity and a thicker pipe, so the two files make a natural revision-compare pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec

BAR_LEN = 50.0
BAR_W = 6.0
LED_PITCH = 10.0
LED_SIZE = 2.0
LED_H = 0.8


def loc(x=0.0, y=0.0, z=0.0) -> TopLoc_Location:
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(t)


def box(dx, dy, dz, x=0.0, y=0.0, z=0.0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def rounded_pipe(length, width, z0, z1, radius):
    """Bar with the two long top edges rounded."""
    shape = box(length, width, z1 - z0, 0, -width / 2, z0)
    fillet = BRepFilletAPI_MakeFillet(shape)
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge(ex.Current())
        ex.Next()
        # the long top edges: parallel to X at z == z1
        from OCP.BRep import BRep_Tool

        p0 = BRep_Tool.Pnt_s(__import__("OCP.TopExp", fromlist=["TopExp"]).TopExp.FirstVertex_s(edge))
        p1 = BRep_Tool.Pnt_s(__import__("OCP.TopExp", fromlist=["TopExp"]).TopExp.LastVertex_s(edge))
        if abs(p0.Z() - z1) < 1e-6 and abs(p1.Z() - z1) < 1e-6 and abs(p0.Y() - p1.Y()) < 1e-6:
            fillet.Add(radius, edge)
    return fillet.Shape()


def main(out: Path, variant: str = "a") -> None:
    cavity_floor = -3.0
    pipe_z0 = 0.2 if variant == "a" else 1.2
    pipe_z1 = pipe_z0 + (3.0 if variant == "a" else 3.5)

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def name(label, text):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))

    def color(label, r, g, b):
        ct.SetColor(label, Quantity_Color(r, g, b, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    margin = 1.0
    pcb = st.AddShape(box(BAR_LEN + 2 * margin, BAR_W + 2 * margin, 1.0, -margin, -BAR_W / 2 - margin, cavity_floor - 1.0), False)
    name(pcb, "PCB")
    color(pcb, 0.1, 0.45, 0.2)

    tray_h = pipe_z0 - cavity_floor
    tray = box(BAR_LEN + 2 * margin, BAR_W + 2 * margin, tray_h, -margin, -BAR_W / 2 - margin, cavity_floor)
    pocket = box(BAR_LEN, BAR_W, tray_h, 0, -BAR_W / 2, cavity_floor)
    tray = BRepAlgoAPI_Cut(tray, pocket).Shape()
    tray_l = st.AddShape(tray, False)
    name(tray_l, "Reflector Tray")
    color(tray_l, 0.95, 0.95, 0.95)

    pipe = st.AddShape(rounded_pipe(BAR_LEN, BAR_W, pipe_z0, pipe_z1, 0.8), False)
    name(pipe, "Light Pipe")
    color(pipe, 0.85, 0.9, 0.95)

    led = st.AddShape(box(LED_SIZE, LED_SIZE, LED_H, -LED_SIZE / 2, -LED_SIZE / 2, 0), False)
    name(led, "LED")
    color(led, 0.95, 0.85, 0.3)

    top = st.NewShape()
    name(top, f"Light Bar {variant.upper()}")
    for label_text, def_l in (("PCB:1", pcb), ("Reflector Tray:1", tray_l), ("Light Pipe:1", pipe)):
        c = st.AddComponent(top, def_l, TopLoc_Location())
        name(c, label_text)
    for k in range(5):
        x = LED_PITCH / 2 + k * LED_PITCH
        c = st.AddComponent(top, led, loc(x, 0, cavity_floor))
        name(c, f"LED:{k + 1}")
    st.UpdateAssemblies()

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    if not writer.Transfer(doc):
        raise SystemExit("STEP transfer failed")
    out.parent.mkdir(parents=True, exist_ok=True)
    status = writer.Write(str(out))
    if status != IFSelect_RetDone:
        raise SystemExit(f"STEP write failed: {status}")
    print(f"wrote {out}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    variant = "b" if "--variant" in sys.argv and sys.argv[sys.argv.index("--variant") + 1].lower() == "b" else "a"
    default = Path(__file__).resolve().parents[1] / "samples" / f"lightbar_{variant}.step"
    target = Path(args[0]) if args and args[0] not in ("a", "b") else default
    main(target, variant)
