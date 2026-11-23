import time, json, tempfile
from pathlib import Path
import numpy as np
from refract.step_import import import_step
from refract.optical_mesh import build_optical_mesh, load_optical, face_info
from refract.engine.scene import build_scene
from refract.engine import sources, tracer, kernels

m = import_step(r'F:\Projects\LightSim\samples\lightbar_a.step')
d = Path(tempfile.mkdtemp())
doc = build_optical_mesh(m, d/'o.npz', d/'o.json')
opt = load_optical(d/'o.npz')
def top_face(inst, axis=2, sign=1):
    best=None
    for f in range(inst['faceCount']):
        fi = face_info(opt, inst, f)
        if fi and fi['planar'] and fi['normal'][axis]*sign>0.99:
            if best is None or fi['centroid'][axis]*sign > best[1]: best=(f, fi['centroid'][axis]*sign)
    return best[0]
pipe = next(i for i in doc['instances'] if i['name'].startswith('Light Pipe'))
led1 = next(i for i in doc['instances'] if i['name']=='LED:1')
pf = top_face(pipe); lf = top_face(led1)
setup = {
 'parts': [
   {'match':'*','material':None,'finish':'black'},
   {'match':'Reflector Tray*','finish':'white_diffuse'},
   {'match':'PCB*','finish':'led_package'},
   {'match':'LED:*','finish':'led_package'},
   {'match':'Light Pipe*','material':'pc_tio2_0p03','finish':'polished'},
 ],
 'leds': [{'id':f'led{k}','name':f'LED {k}','model':'white_generic','placement':{'type':'face','part':f'*/LED:{k}','face':lf}} for k in range(1,6)],
 'detectors': [{'id':'exit','name':'Exit','faces':[{'part':'*/Light Pipe*','face':pf}],'cone_deg':30,'resolution':120}],
}
t0=time.time()
sc = build_scene(opt, doc, setup)
print('scene', time.time()-t0, len(sc.T), sc.warnings, [ (d.nu,d.nv,round(d.area_total,2)) for d in sc.detectors])
from refract import library as lib
srcs = sources.build_channel_sources(setup, doc, sc, opt, lib.get_library())
print([s.key for s in srcs], srcs[0].flux_w, srcs[0].axis)
for n in (2000, 50000):
    t0=time.time()
    tl = tracer.trace_channel(sc, srcs[2], n, seed=1, record_paths=5)
    print(n, 'time', round(time.time()-t0,2), 'steps', tl.steps)
print('accounted', tl.total_accounted(), 'of', tl.n_rays)
print('special', tl.special, 'det_pow', tl.det_pow, 'abs_vol', tl.abs_vol, 'abs_surf', tl.abs_surf)
print('exit eff', tl.det_pow[0]/tl.n_rays)
