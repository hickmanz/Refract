"""Compare the GPU solver with the CPU solver on a stored project, and time both.

    cd server; ..\.venv\Scripts\python.exe scripts\gpu_bench.py <project id> <revision id> <setup id> [rays] [--all]

Uses the first channel of the setup (or every channel with --all).
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from refract import library as lib  # noqa: E402
from refract import store  # noqa: E402
from refract.engine import gpu_tracer, sources, tracer  # noqa: E402
from refract.engine.scene import build_scene  # noqa: E402
from refract.optical_mesh import load_optical  # noqa: E402


def main() -> None:
    pid, rid, setup_id = sys.argv[1], sys.argv[2], sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 1_000_000
    setup = store.read_json(store.project_dir(pid) / "setups" / f"{setup_id}.json")
    mdir = store.rev_dir(rid)
    opt, doc = load_optical(mdir / "optical.npz"), store.read_json(mdir / "optical.json")
    library = lib.get_library()
    scene = build_scene(opt, doc, setup, library)
    srcs = sources.build_channel_sources(setup, doc, scene, opt, library)
    chans = srcs if "--all" in sys.argv else srcs[:1]
    ok, name = gpu_tracer.available()
    print("GPU:", ok, name, "| triangles:", len(scene.T), "| channels:", len(chans), "| rays each:", n)
    for src in chans:
        t0 = time.time()
        gpu_tracer.trace_channel(scene, src, 20_000, seed=3)  # compile and warm up
        print(f"{src.key}: GPU warm-up {time.time() - t0:.1f} s")
        t0 = time.time()
        g = gpu_tracer.trace_channel(scene, src, n, seed=7)
        tg = time.time() - t0
        t0 = time.time()
        c = tracer.trace_channel(scene, src, n, seed=7)
        tc = time.time() - t0
        print(f"  GPU {n / tg:,.0f} rays/s ({tg:.2f} s)   CPU {n / tc:,.0f} rays/s ({tc:.2f} s)   speedup {tc / tg:.1f}x")
        for det in scene.detectors:
            sl = slice(det.offset, det.offset + det.nu * det.nv)
            gy, cy = g.cone[sl, 1], c.cone[sl, 1]
            gt, ct = g.ex[sl, 1].sum(), c.ex[sl, 1].sum()
            print(f"  {det.name}: exit power GPU {g.det_pow[0] / n:.5f} CPU {c.det_pow[0] / n:.5f}; luminous total GPU {gt / n:.5f} CPU {ct / n:.5f}; cone total GPU {gy.sum() / n:.5f} CPU {cy.sum() / n:.5f}")
            # per-pixel difference against the combined noise of the two maps
            var = g.cone2[sl] + c.cone2[sl]
            m = (var > 0) & (cy > 0)
            z = (gy[m] - cy[m]) / np.sqrt(var[m])
            print(f"  per-pixel difference in sigmas: mean {z.mean():+.3f}, rms {np.sqrt((z * z).mean()):.3f} (1.0 expected) over {m.sum()} pixels")
        print("  accounted GPU", g.total_accounted() / n, "CPU", c.total_accounted() / n)
        print("  specials GPU", np.round(g.special / n, 5), "\n  specials CPU", np.round(c.special / n, 5))


if __name__ == "__main__":
    main()

