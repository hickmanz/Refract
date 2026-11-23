# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds dist/Refract/Refract.exe (one-folder, no console window).
#   pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/refract.spec
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
if not (FRONTEND_DIST / "index.html").exists():
    raise SystemExit("Build the UI first: cd frontend && npm ci && npm run build")

datas = [(str(FRONTEND_DIST), "frontend/dist")]
binaries = []
hiddenimports = []

# OpenCASCADE bindings, the ray-tracing stack (numba + llvmlite, Embree, scipy), Mie scattering, the app window
# (pywebview + WebView2 loader + .NET bridge).
for pkg in ("OCP", "cadquery_ocp", "cadquery_ocp_proxy", "numba", "llvmlite", "embreex", "miepython", "trimesh",
            "webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

# Optional: Mitsuba 3 for the rendered picture (large; set REFRACT_BUNDLE_MITSUBA=1, see build.ps1 -WithRender).
# The packages are copied as plain folders: collecting their submodules one by one takes a very long time.
if os.environ.get("REFRACT_BUNDLE_MITSUBA") == "1":
    import importlib.util

    for pkg in ("mitsuba", "drjit"):
        spec = importlib.util.find_spec(pkg)
        if spec and spec.submodule_search_locations:
            datas.append((list(spec.submodule_search_locations)[0], pkg))
    hiddenimports += ["mitsuba", "drjit"]

# things that are imported lazily or by name
hiddenimports += collect_submodules("refract")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("scipy.sparse") + collect_submodules("scipy.ndimage") + collect_submodules("scipy.optimize")
hiddenimports += ["multipart", "python_multipart", "tkinter", "tkinter.filedialog"]
hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr", "clr_loader", "cffi"]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "server")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "pytest", "pandas", "vtk"] + ([] if os.environ.get("REFRACT_BUNDLE_MITSUBA") == "1" else ["mitsuba", "drjit"]),
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Refract",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no black window: the app has its own window
    icon=str(ROOT / "packaging" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Refract",
)
