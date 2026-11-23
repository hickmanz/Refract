import sys, time
sys.path.insert(0, r"F:\Projects\LightSim\server")
from refract.materials import fit

t = time.time()
print(fit.simulate_slab(2.0, 0.0, 0.0), "(clear: expect ~0.92 = 1-2*R/(1+R))")
print("compile+run", time.time() - t)
t = time.time()
true = [(1.0, 1.2, 0.6), (3.0, 1.2, 0.6)]
samples = []
for th, mus, g in true:
    tot, haze = fit.simulate_slab(th, mus, g, 1.58, 0.0, 200000, 5)
    samples.append({"thickness_mm": th, "total_transmission": tot, "haze": haze})
    print(th, tot, haze)
r = fit.fit_slab(samples, "pc", 40000)
print({k: r[k] for k in ("mus", "g", "cost", "notes")}, time.time() - t)
r = fit.fit_slab([{k: v for k, v in s.items() if k != "haze"} for s in samples], "pc", 40000)
print({k: r[k] for k in ("mus", "g", "cost", "notes")})
