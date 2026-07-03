## Sensitivity Grid (parallel per-process variant runs)

Generated 2026-07-03 15:34:50 UTC from 6 variant file(s).

Base combined net USD — gated: **+131.42**, ungated: **+109.58**, delta (G-U): **+21.84**.

| Variant | Label | Trades | Net USD | Delta vs Base Gated | Variant Delta (V-U) | Max DD $ | WR% | PF |
|---------|-------|-------:|--------:|--------------------:|--------------------:|---------:|----:|---:|
| er_loose | er(0.30/0.15) | 107 | +136.78 | +5.36 | +27.20 | 523.90 | 49.5 | 1.10 |
| er_tight | er(0.40/0.25) | 103 | +192.53 | +61.11 | +82.95 | 409.77 | 50.5 | 1.15 |
| vol_loose | vol(20/70/90) | 102 | +66.34 | -65.08 | -43.24 | 460.92 | 49.0 | 1.05 |
| vol_tight | vol(30/80/97) | 101 | +172.73 | +41.31 | +63.15 | 400.45 | 50.5 | 1.14 |
| win_minus30 | windows -30min | 115 | +23.03 | -108.39 | -86.55 | 494.97 | 47.8 | 1.02 |
| win_plus30 | windows +30min | 105 | +71.57 | -59.85 | -38.01 | 497.96 | 49.5 | 1.05 |

Rubric condition 4 — no sensitivity variant flips the sign of the combined gated-vs-ungated delta: **FAIL**

