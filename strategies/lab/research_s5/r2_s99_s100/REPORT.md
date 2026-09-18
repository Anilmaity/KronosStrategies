# S99 (MSS+FVG reversal) and S100 (M3 combo): fix or retire — report (2026-09-18)

Pre-registration: `PROTOCOL.md` (written 11:50 UTC, before any arm ran; one deviation appended —
a worker crash and an unchanged resume). Every number below is copied from `results/`
(`scores.csv`, `decision.txt`, `table_s99.md` / `table_s100.md`, `anatomy.txt`, `live_record.txt`,
`book.txt`, `sweep_*.log`); nothing is hand-typed. Harness: `lab.harness.replay` over the 24-month
cache (2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST), the real `get_signal` and real
`shared.gate_rules`; every arm a full re-run (never a filter of a saved list — S100's shared
`_pending` slot and S99's single pending slot are exercised on every arm). Costs: mid-M1 0.75 / 1.00,
quote-S5 0.45 / 0.70 (the tradeable claim, bid/ask exits, resolver validated at 100.000 % against
`results/s5exit/s14_ob_mit_bias_c0.80.parquet`). 43 replays (24 S99, 19 S100), ~55 min of compute.

## Headline

**Retire both.** Under the new cost convention neither strategy is within reach of the bars at its
shipped configuration, and none of the 33 pre-registered changes (10 dimensions for S99, 8 for S100,
each with its complement or identity control) produces a configuration that passes bars 1–7 under
live triggers when the value is chosen on TRAIN:

| | shipped, mid-M1 TEST PF @0.75 / 1.00 | shipped, **quote-S5 TEST PF @0.45 / 0.70** | TRAIN quote PF @0.70 | best TRAIN-justified pick (quote TEST PF @0.45 / 0.70) | bars failed by the pick |
|---|---|---|---|---|---|
| **S99** | 0.847 / 0.788 | **0.825 / 0.767** (−864 pts TEST @0.70) | 0.726 | `bias_with_ema21` 0.937 / 0.872 | 2 (TEST > 1 at both costs), 3 (TRAIN 0.847 < 0.9), 4 (30 % of TEST months positive) |
| **S100** | 0.973 / 0.904 | **0.958 / 0.893** (−903 pts TEST @0.70) | 0.696 | `minsl2.5` 0.997 / 0.935 | 2, 3 (TRAIN 0.756), 4 (20 %) |

The single most informative negative: S100's best TEST arm at stress cost, `minsl3.0` (quote TEST
1.026 / 0.967, TRAIN 0.736), would have been the "fix" if judged on TEST alone — it is exactly the
shape (TEST improves while TRAIN stays under 0.8) the protocol's TRAIN-only selection exists to refuse.
The live record does not rescue either: S99 is flat in points (PF 1.01 on 102 trades, bootstrap 95 %
CI 0.57–1.76) and S100's +$478 has a point-PF of 1.17 with a CI that includes 1 (0.85–1.55); both
have declined every month (S99 +151 → +69 → −29 USD; S100 +53 → +475 → −50).

## 1. Baselines re-established (identity controls first)

- `s99_ident` (variant module, all knobs neutral) and `s99_base_w400` (original, win_15m 400) reproduce
  `s99_base` exactly: n 1871, −940.2 pts, PF 0.834, TRAIN 0.812 / TEST 0.847 — identical to Stage 1's
  1871 trades. `s100_er_off_w1600` (the ER-gate control at the wider window) is 4547 trades vs 4579 at
  win_1m 700, PF 0.897 vs 0.900: the EMA warm-up difference is small, which is why it has its own control.
- Cost derivation proven: the replayed 1.00 arm equals the 0.75 arm minus 0.25 on every trade for both
  strategies (max |difference| 1.8e−15). All 1871 / 4579 baseline trades lie inside the S5 cache.

| baseline | n | mid-M1 @0.75 TRAIN / TEST PF | mid-M1 @1.00 TRAIN / TEST PF | quote-S5 @0.45 TRAIN / TEST PF | **quote-S5 @0.70 TRAIN / TEST PF** | quote @0.70 TEST pts | WR (q70) TRAIN / TEST |
|---|---|---|---|---|---|---|---|
| S99 shipped | 1871 | 0.812 / 0.847 | 0.722 / 0.788 | 0.817 / 0.825 | **0.726 / 0.767** | −864.3 | 37.0 / 39.2 % |
| S100 shipped | 4579 | 0.779 / 0.973 | 0.695 / 0.904 | 0.760 / 0.958 | **0.696 / 0.893** | −902.9 | 29.9 / 31.2 % |

Baseline anatomy under quote-S5 @0.70 (`results/anatomy.txt`):
- **S99 loses everywhere**: every stop bucket under 9 pt (PF 0.41 at 1.5–2 pt, 0.66 at 2–3, 0.60 at
  3–4, 0.75 at 4–6, 0.75 at 6–9; the 9+ bucket is 1.01 on 173 trades), both sides in both halves
  (0.7–0.8), every hour 6–14 (0.5–0.9; hour 15 is 1.0). Mid→quote haircut 0.333 pts/trade (79 of
  782 mid-model TPs become stops) — a little above the 0.275 generic geometry, as expected for a
  1.5R target strategy. The reversal has a 37–39 % win rate at 1.5R; break-even needs 47 %.
- **S100 loses in its tight-stop mass**: 1.5–3 pt stops are 54 % of trades and −1,718 pts (PF 0.6–0.7);
  4–6 pt is 1.0, 9+ is 1.0 on 213. Hours 1, 3, 4, 7, 15 are the losers (PF 0.6–0.7); hour 6 is the
  only hour above 1. By entry model in TRAIN all three lose (FVG 0.7, OB 0.7, RSI 0.7); in TEST RSI
  is 1.0, FVG 0.9, OB 0.8 — the FVG/RSI hand-off `REPORT_s100.md` described, now with all three
  under 1 in the half that was never seen by the spec. Haircut 0.298 pts/trade.

## 2. S99 grid — every arm (`results/table_s99.md`; bars 1–5 on quote-S5 @0.45 with the 0.70 twin for bar 2)

| arm | n | mid TRAIN PF @0.75 | mid TEST PF @0.75 | mid TEST PF @1.00 | mid TEST pts @1.00 | **quote TRAIN PF @0.70** | quote TRAIN pts @0.70 | quote TEST PF @0.45 | **quote TEST PF @0.70** | quote TEST pts @0.70 | TEST +months | gold corr | bars 1-5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s99_base | 1871 | 0.812 | 0.847 | 0.788 | -784.2 | **0.726** | -604.7 | 0.825 | **0.767** | -864.3 | 0.4 | 0.27 | 10001 |
| s99_ident | 1871 | 0.812 | 0.847 | 0.788 | -784.2 | **0.726** | -604.7 | 0.825 | **0.767** | -864.3 | 0.4 | 0.27 | 10001 |
| s99_base_w400 | 1871 | 0.812 | 0.847 | 0.788 | -784.2 | **0.726** | -604.7 | 0.825 | **0.767** | -864.3 | 0.4 | 0.27 | 10001 |
| s99_base_c1.00 | 1871 | 0.812 | 0.847 | 0.788 | -784.2 | **0.726** | -604.7 | 0.825 | **0.767** | -864.3 | 0.4 | 0.27 | 10001 |
| s99_mingap0.25 | 1756 | 0.834 | 0.87 | 0.815 | -655.8 | **0.744** | -588.0 | 0.846 | **0.793** | -738.6 | 0.2 | 0.39 | 10001 |
| s99_mingap0.5 | 1267 | 0.863 | 0.777 | 0.737 | -775.8 | **0.779** | -430.4 | 0.759 | **0.72** | -826.1 | 0.2 | 0.33 | 10001 |
| s99_mingap1.0 | 405 | 1.046 | 0.864 | 0.835 | -226.4 | **0.973** | -21.3 | 0.824 | **0.795** | -283.5 | 0.4 | 0.31 | 10101 |
| s99_buf0.1 | 1584 | 0.875 | 0.955 | 0.886 | -341.8 | **0.778** | -382.6 | 0.918 | **0.852** | -449.5 | 0.4 | 0.43 | 10000 |
| s99_buf0.5 | 2599 | 0.77 | 0.817 | 0.771 | -1238.1 | **0.706** | -1121.9 | 0.835 | **0.787** | -1128.9 | 0.3 | 0.1 | 10001 |
| s99_buf1.0 | 2731 | 0.777 | 0.757 | 0.727 | -2029.6 | **0.737** | -1437.8 | 0.747 | **0.718** | -2088.3 | 0.0 | -0.22 | 10001 |
| s99_bias_with_ema21 | 1567 | 0.947 | 0.952 | 0.887 | -342.6 | **0.847** | -265.3 | 0.937 | **0.872** | -386.6 | 0.4 | 0.34 | 10101 |
| s99_bias_with_h1 | 1221 | 0.848 | 0.96 | 0.895 | -246.0 | **0.761** | -346.3 | 0.946 | **0.882** | -276.0 | 0.4 | 0.27 | 10001 |
| s99_bias_against_ema21 | 314 | 0.332 | 0.382 | 0.351 | -448.6 | **0.28** | -344.4 | 0.333 | **0.305** | -482.3 | 0.0 | -0.03 | 10001 |
| s99_bias_against_h1 | 664 | 0.74 | 0.651 | 0.6 | -550.0 | **0.651** | -268.4 | 0.613 | **0.565** | -602.1 | 0.2 | 0.21 | 10001 |
| s99_sweepn24 | 1662 | 0.792 | 0.845 | 0.787 | -705.5 | **0.722** | -548.4 | 0.811 | **0.755** | -817.9 | 0.2 | 0.25 | 10001 |
| s99_sweepn96 | 1998 | 0.81 | 0.843 | 0.783 | -851.5 | **0.723** | -649.1 | 0.822 | **0.764** | -927.9 | 0.3 | 0.31 | 10001 |
| s99_retrace12 | 1857 | 0.823 | 0.837 | 0.778 | -818.8 | **0.734** | -578.9 | 0.814 | **0.757** | -900.1 | 0.3 | 0.29 | 10001 |
| s99_retrace48 | 1873 | 0.812 | 0.844 | 0.785 | -796.4 | **0.726** | -604.7 | 0.822 | **0.764** | -875.8 | 0.4 | 0.27 | 10001 |
| s99_buy | 982 | 0.77 | 0.854 | 0.791 | -379.9 | **0.702** | -328.0 | 0.824 | **0.763** | -432.7 | 0.3 | 0.23 | 10001 |
| s99_sell | 900 | 0.854 | 0.838 | 0.781 | -416.2 | **0.748** | -279.1 | 0.822 | **0.767** | -445.3 | 0.6 | 0.2 | 10001 |
| s99_above | 1141 | 0.833 | 0.771 | 0.713 | -537.8 | **0.732** | -422.6 | 0.749 | **0.693** | -576.3 | 0.25 | -0.24 | 10001 |
| s99_below | 723 | 0.758 | 0.925 | 0.864 | -246.4 | **0.726** | -166.2 | 0.902 | **0.842** | -288.0 | 0.4 | 0.7 | 10000 |
| s99_nolondon | 1003 | 0.912 | 0.857 | 0.804 | -407.2 | **0.801** | -251.8 | 0.847 | **0.794** | -426.6 | 0.3 | 0.26 | 10001 |
| s99_nony | 1044 | 0.689 | 0.822 | 0.758 | -480.9 | **0.625** | -420.4 | 0.782 | **0.72** | -560.8 | 0.4 | 0.3 | 10001 |

**TRAIN-justified selection (PROTOCOL §3; incumbent TRAIN quote @0.70 PF 0.726 / −604.7 pts, TEST 0.767 / −864.3):**

| dim | qualifiers on TRAIN | pick (best TRAIN PF) | pick's TEST, read once (q45 / q70 PF, pts@70) | verdict |
|---|---|---|---|---|
| A min FVG size | 0.25, 0.5, 1.0 all beat the incumbent on TRAIN | `mingap1.0` (TRAIN 0.973, n 405) | 0.824 / 0.795, −283.5 | **dead** — fails bars 2, 4; TRAIN rises only by shrinking n 1871 → 405 (0.973 on 227 TRAIN trades) and TEST stays far under 1 (0.795). Not a plateau: 0.25 → 0.793, 0.5 → 0.720, 1.0 → 0.795 on TEST |
| B stop buffer | 0.1 only | `buf0.1` (TRAIN 0.778) | 0.918 / 0.852, −449.5 | **dead** — fails 2, 3, 4, 5. Monotone: tighter buffer is better (0.1 > 0.2 > 0.5 > 1.0 on both halves), i.e. the *widest* structural stop is the worst — the opposite of the "stops too tight" story; a wider buffer admits more fills (n 1584 → 1871 → 2599 → 2731, fewer phantom-guard cancels) and they are worse trades |
| C HTF bias gate | with_ema21, with_h1 | `bias_with_ema21` (TRAIN 0.847) | 0.937 / 0.872, −386.6 | **dead** — fails 2, 3, 4. The concept separates (see controls) but the aligned remainder still loses at any realistic cost; the second definition moves the same way (h1: TRAIN 0.761, TEST 0.882), a directional plateau of losing |
| C controls | — | `against_ema21` PF 0.28 TRAIN / 0.31 TEST on 314; `against_h1` 0.65 / 0.57 on 664 | — | counter-bias fills are catastrophic; the gate removes them, and *that is all it does* |
| D `_SWEEP_N` | none | — | — | **dead** — 24 / 48 / 96 give TEST 0.755 / 0.767 / 0.764: the docstring's "plateau" is a plateau of losing |
| E `_RETRACE_W` | 12 (by 0.008 PF) | `retrace12` | 0.814 / 0.757, −900.1 | **dead** — inert (12 / 24 / 48 = 0.757 / 0.767 / 0.764) |
| F sides | SELL | `sell` (0.748) | 0.822 / 0.767, −445.3 | **dead** — both sides lose in both halves |
| F regime | above_sma20 | `above` (0.732) | 0.749 / 0.693, −576.3 | **dead**; `below` looks better on TEST (0.842) but not on TRAIN (0.726 = incumbent) — a TEST-only mirage |
| F session | no-London | `nolondon` (0.801) | 0.847 / 0.794, −426.6 | **dead** — fails 2, 3, 4; NY-only reproduces `REPORT_s99`'s hour instability |

No dimension passes → no combination stage (pre-declared rule). **S99: RETIRE.**

What the grid establishes beyond "no": (i) the two recorded defects are not the cause — filtering
small FVGs (= raising the ATR-relative stop floor) does not lift TEST above 0.80 at any value, and the
absolute floor was already refuted (`REPORT_s99`); (ii) the July diagnosis ("counter-HTF-bias filter is
the promising fix") is half right — counter-bias fills are the worst trades in the book (PF 0.3) — and
half wrong: removing them leaves a strategy that still loses 0.13 R per trade under live triggers,
because the aligned reversal itself has a 40 % win rate at 1.5R; (iii) nothing in the module's
own knob set (`_SWEEP_N`, `_RETRACE_W`, `_BUF_ATR`) moves the result — the loss is in the
sweep→MSS→FVG concept at this geometry, which is what the S5 sweeps study found from the other side
(a gold sweep is followed by continuation, not reversal).

## 3. S100 grid — every arm (`results/table_s100.md`)

| arm | n | mid TRAIN PF @0.75 | mid TEST PF @0.75 | mid TEST PF @1.00 | mid TEST pts @1.00 | **quote TRAIN PF @0.70** | quote TRAIN pts @0.70 | quote TEST PF @0.45 | **quote TEST PF @0.70** | quote TEST pts @0.70 | TEST +months | gold corr | bars 1-5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s100_base | 4579 | 0.779 | 0.973 | 0.904 | -831.4 | **0.696** | -1570.8 | 0.963 | **0.893** | -902.9 | 0.4 | -0.28 | 10001 |
| s100_er_off_w1600 | 4547 | 0.769 | 0.976 | 0.907 | -802.3 | **0.688** | -1607.2 | 0.967 | **0.896** | -866.8 | 0.5 | -0.31 | 10001 |
| s100_base_c1.00 | 4579 | 0.779 | 0.973 | 0.904 | -831.4 | **0.696** | -1570.8 | 0.963 | **0.893** | -902.9 | 0.4 | -0.28 | 10001 |
| s100_minsl2.0 | 3580 | 0.815 | 0.984 | 0.919 | -655.6 | **0.742** | -1018.3 | 0.969 | **0.903** | -766.2 | 0.4 | -0.29 | 10001 |
| s100_minsl2.5 | 2798 | 0.822 | 1.014 | 0.952 | -352.1 | **0.756** | -746.6 | 0.997 | **0.935** | -469.7 | 0.5 | -0.32 | 10001 |
| s100_minsl3.0 | 2155 | 0.793 | 1.048 | 0.989 | -69.8 | **0.736** | -606.3 | 1.026 | **0.967** | -214.3 | 0.6 | -0.29 | 10001 |
| s100_hours_drop1 | 4149 | 0.785 | 0.99 | 0.919 | -624.8 | **0.702** | -1389.1 | 0.983 | **0.91** | -668.8 | 0.5 | -0.17 | 10001 |
| s100_hours_drop12 | 3762 | 0.779 | 0.985 | 0.914 | -594.9 | **0.694** | -1309.5 | 0.97 | **0.898** | -687.0 | 0.5 | -0.29 | 10001 |
| s100_hours_drop123 | 3444 | 0.775 | 1.024 | 0.952 | -302.4 | **0.693** | -1226.2 | 1.011 | **0.937** | -382.1 | 0.5 | -0.32 | 10001 |
| s100_hours_nyonly | 1611 | 0.733 | 1.066 | 1.003 | 8.5 | **0.645** | -827.2 | 1.044 | **0.981** | -56.3 | 0.6 | -0.2 | 10001 |
| s100_hours_asialondon | 2968 | 0.817 | 0.924 | 0.852 | -840.0 | **0.737** | -743.6 | 0.919 | **0.846** | -846.6 | 0.4 | -0.27 | 10001 |
| s100_er_ranging_w1600 | 3253 | 0.752 | 0.949 | 0.885 | -740.7 | **0.678** | -1215.5 | 0.944 | **0.878** | -760.3 | 0.4 | -0.36 | 10001 |
| s100_er_strict_w1600 | 398 | 0.526 | 1.165 | 1.093 | 80.8 | **0.478** | -229.7 | 1.09 | **1.021** | 17.9 | 0.4 | -0.64 | 11000 |
| s100_tpr2.0 | 4798 | 0.766 | 0.92 | 0.85 | -1304.8 | **0.673** | -1685.2 | 0.913 | **0.842** | -1344.5 | 0.3 | -0.31 | 10001 |
| s100_tpr3.0 | 4376 | 0.781 | 0.941 | 0.877 | -1064.5 | **0.702** | -1529.0 | 0.97 | **0.902** | -815.5 | 0.4 | -0.17 | 10001 |
| s100_buy | 2404 | 0.74 | 0.906 | 0.837 | -692.2 | **0.663** | -895.4 | 0.889 | **0.818** | -743.7 | 0.2 | -0.1 | 10001 |
| s100_sell | 2179 | 0.823 | 1.039 | 0.971 | -129.0 | **0.733** | -669.9 | 1.035 | **0.966** | -148.1 | 0.6 | -0.31 | 10001 |
| s100_above | 2746 | 0.764 | 1.046 | 0.971 | -123.8 | **0.68** | -1176.8 | 1.009 | **0.934** | -272.4 | 0.25 | -0.24 | 10001 |
| s100_below | 1817 | 0.81 | 0.904 | 0.841 | -707.6 | **0.731** | -390.4 | 0.919 | **0.853** | -630.5 | 0.4 | -0.18 | 10001 |

**TRAIN-justified selection (incumbent TRAIN quote @0.70 PF 0.696 / −1570.8 pts, TEST 0.893 / −902.9; for the ER dimension the incumbent is `er_off_w1600`: TRAIN 0.688, TEST 0.896):**

| dim | qualifiers on TRAIN | pick | pick's TEST, read once (q45 / q70 PF, pts@70) | verdict |
|---|---|---|---|---|
| A stop floor (reject) | 2.0, 2.5, 3.0 | `minsl2.5` (TRAIN 0.756) | 0.997 / 0.935, −469.7 | **dead** — fails 2, 3, 4. The floor is monotone on TEST (0.903 → 0.935 → 0.967 at 0.70; 3.0 reaches 1.026 at 0.45 only) but NOT on TRAIN (0.742 → 0.756 → 0.736): the TRAIN half never gets near 0.9 at any floor, so the TEST improvement is the 2026 tape, not the rule. Contrast s14, where TRAIN rose monotonically with the floor. Live agrees only in part: the live losses sit in the 2–3 pt bucket (−$541 on 92 trades, PF 0.68), which a 2.5 floor removes only half of and a 3.0 floor removes at the cost of 57 % of the trades |
| B hours | drop1, asia+london | `hours_asialondon` (0.737) | 0.919 / 0.846, −846.6 | **dead**; the live-motivated drop-1-2-3 does not qualify on TRAIN (0.693 ≤ 0.696) although it is 1.011 / 0.937 on TEST — the same one-sided shape as on 2026-09-02, now on a TRAIN half with four extra months. NY-only is TRAIN 0.645 / TEST 0.981: the *worst* TRAIN arm and the best TEST arm |
| C ER gate | none | — | — | **dead**; `ranging` is worse in both halves; `strict` (n 398) is TRAIN 0.478 / TEST 1.021 — a 2026-only artefact on a tiny sample (bars 3, 4, 5 fail). The opt15 do-not-arm verdict stands, now on 24 months |
| D `_TP_R` | 3.0 (by 0.006) | `tpr3.0` (0.702) | 0.970 / 0.902, −815.5 | **dead** — 2.0 / 2.5 / 3.0 = 0.842 / 0.893 / 0.902 on TEST: flat-to-monotone, the spec's plateau is real and it is a plateau under 1 |
| E sides | SELL | `sell` (0.733) | 1.035 / 0.966, −148.1 | **dead** — fails 2 (stress), 3, 4; long-only 0.663 / 0.818: S100's whole 2026 positive read is its shorts in a topping range |
| E regime | below_sma20 | `below` (0.731) | 0.919 / 0.853, −630.5 | **dead**; `above` is 1.009 / 0.934 on TEST but 0.680 on TRAIN |

No dimension passes → no combination stage. **S100: RETIRE.**

What the grid establishes: (i) the stop floor works in the direction the spec and the s14 result
predict, but S100 has no positive-expectancy core for the floor to expose — at 3.0 the remaining
2155 trades are still 0.736 on TRAIN; (ii) every arm that looks good is good on TEST only (the
2026 range), which is the regime dependence the spec itself recorded (2023H2 −273 pts) showing
up again in the half the spec never saw; (iii) the ER gate cannot buy regime independence at any
cost; (iv) the OB/FVG/RSI hand-off means no single entry model carries the strategy in both halves.

## 4. Live record since 2026-07-01 (prod DB, read-only; `results/live_record.txt`)

| | S99 | S100 |
|---|---|---|
| signals generated / placed | 277 / 102 (37 %) | 629 / 268 (43 %) |
| rejection reasons | sl_too_tight 47, entry_drift 47, soft_daily_brake 31, no_add_to_loser 17, metaapi 15, news 8, dup 6, cap 4 | entry_drift 117, sl_too_tight 83, open_position_cap 72, soft_daily_brake 56, metaapi 24, no_add 8, dup 1 |
| `sl_too_tight` stop distances | median 1.17 pt (p90 1.38) | median 1.10 (p90 1.40) |
| generated stops < 1.5 / < 3.0 pt | 18.8 % / 54.2 % | 15.1 % / 55.6 % |
| closed trades, USD, PF (USD), WR | 102, **+$191**, 1.107, 43.1 % | 268, **+$478**, 1.095, 27.2 % |
| **PF in points** (sizing-invariant), 95 % bootstrap CI | **1.010** [0.57, 1.76], +2.9 pts | **1.169** [0.85, 1.55], +103 pts |
| by month (USD) | Jul +151 · Aug +69 · **Sep −29** | Jul +53 · Aug +475 · **Sep −50** |
| last 30 days | n 46, +$30 | n 163, +$11 |
| by stop bucket (USD, PF) | <2 pt −190 (0.2–0.5) · 2–3 +356 (2.08) · 3–4 −77 · 4–6 −201 (0.61) · 6+ +303 (2.02) | 1.5–2 −49 (0.90) · **2–3 −541 (0.68)** · 3–4 +75 · 4–6 +842 (1.83) · 6+ +151 |
| by entry model (USD, PF) | — | FVG +335 (1.1) · **OB −208 (0.9)** · RSI +351 (1.4) |
| by side (USD, PF) | BUY −276 (0.70) · SELL +467 (1.54) | BUY +91 (1.0) · SELL +387 (1.2) |

Same window, the harness's ungated read (`results/book.txt`): S99 sim n 233, quote-S5 @0.70 PF 0.975
(−16 pts) vs live PF(pts) 1.010 — live selection neither helps nor hurts S99. S100 sim n 649, quote
@0.70 PF 0.807 (−365 pts) vs live 1.169 (+103 pts) on the 41 % of signals that were placed — the live
gates (entry_drift, sl_too_tight, cap, brake) *did* select a better subset over these 2.5 months,
consistent with the 2026-08 fidelity audit ("gates are net protective"). But: the CI includes 1, the
edge is concentrated in August (+$475 of +$478), September is negative, S100's shorts carry it
(+$387 of +$478), and the sim's own by-month PF in the window (0.74 / 0.89 / 0.78) says the underlying
signal population was losing throughout. A 268-trade live sample cannot overturn a 4,579-trade
24-month failure; it can only say the gates make S100 lose slower.

**Live selection does not change the verdict for either strategy.** It also does not point at a
gate we could model as a fix: the July fidelity audit already replayed the rejected set and found it
net negative at realistic cost, so "more gating" is the strategy manager's job, not a strategy fix.

## 5. What the book loses / gains (`results/book.txt`; equal-risk 1 R per trade)

- Trade flow: sim S99 ≈ 3.5 trades/weekday, S100 ≈ 8.6; live since July 102 and 268 placed over
  ~57 weekdays ≈ 1.8 and 4.7/day. Retiring both removes ~6.5 placed trades/day from a book that just added c03 (~4/day) and s14
  (~3.3/day at the 3.0 floor) — it eases the `open_position_cap` (72 S100 rejects since July) and
  the shared $150/day kill-switch, both of which the two losers were consuming.
- Expectancy: S99 −0.28 R/trade, S100 −0.23 R/trade under quote-S5 @0.70 over 24 months (TEST
  −206 R and −394 R respectively).
- Diversification: daily-R correlation with the survivors is small (S99/c03 0.15, S99/s14 0.00,
  S100/c03 0.14, S100/s14 −0.02) — but a negative-expectancy leg is not diversification. The 24-month
  equal-risk book c03+s14 is +675 R, daily PF 1.74, max DD −94 R, 18/25 months positive; adding S99
  drops it to +151 R / DD −231 R; adding S100 to −355 R / DD −540 R; adding both to −879 R, 6/25
  months positive. (Survivors on mid-M1 @1.00, the two on quote-S5 @0.70 — the survivors' live-trigger
  haircut is −8 % / −15 % of PF per `REPORT_s5exit`, which does not change the ordering.)
- What is lost by retiring: on the live record, +$669 of demo P&L over 2.5 months at ~0.09 lots —
  with point-PFs whose CIs include 1 and a September that is negative for both. On the 24-month
  sim, nothing: both legs subtract.

## 6. Recommendation

**S99 — retire.** Flip `arm=OFF` for 'S99 MSS FVG Reversal' in the manager (no code change; the
container can be stopped with `docker compose -p kronos stop <s99 service>` afterwards). No
configuration in the module's own knob set, in the two recorded defects, in the HTF-bias idea, or in
the Stage-2 gate set passes; the best TRAIN-justified pick (`bias_with_ema21`) is quote-S5 TEST
0.937 / 0.872 with TRAIN 0.847 — 0.13 R/trade short of break-even. The concept (sweep → MSS →
FVG retrace at 1.5R) has a 37–39 % win rate where it needs 47 %.

**S100 — retire.** Flip `arm=OFF` for 'S100 M3 Combo Scalper'. The best TRAIN-justified pick
(`MIN_SL_DIST_PTS=2.5` on its container) is quote-S5 TEST 0.997 / 0.935 with TRAIN 0.756; the best
TEST arm (`3.0`: 1.026 / 0.967) fails the stress cost and has TRAIN 0.736. Everything that looks
good in S100 is 2026-only (TEST) or short-only; TRAIN never exceeds 0.83 at any floor, hour set,
gate, side, regime or TP. If the operator wants to keep it running on the demo for the live-gate
parity question (the one place it still earns: the placed subset's point-PF 1.17 with CI 0.85–1.55),
the least-bad configuration is `MIN_SL_DIST_PTS=2.5` — but that is a demo-fidelity experiment, not a
strategy with a validated edge, and this report does not recommend it.

Neither retirement needs a rebuild; both are dashboard arm flips, reversible the same way.

## 7. What this does and does not establish

Establishes, on 24 months at realistic cost with live-trigger exits and every change re-run through
the real `get_signal`: no single pre-registered change to S99 or S100 produces a configuration that
is profitable on TRAIN, profitable on TEST at both costs, monthly-consistent and regime-independent.
The two recorded defects (no minimum FVG size; target floored but not the stop) are real but are not
what makes the strategies lose; fixing them in ATR units (S99) or by live-style rejection (S100)
moves PF by 0.03–0.10 and leaves both under 1 on TRAIN.

Does not establish: anything about a combined change (none qualified for the pre-declared stage B);
anything about the gates the harness does not model (`entry_drift`, `no_add_to_loser`, cap, brake) as a
*strategy* fix — the live S100 placed subset is better than the ungated sim in the same 2.5 months,
which is the manager doing its job on a losing population, not evidence of an edge; anything about a
gold bear market (none in the window).

Comparisons made: 20 S99 arms + 15 S100 arms against 5 + 2 bars each, with 4 identity/complement
controls; ≈ 1–2 spurious single-bar passes were expected and the ones seen (S100 `er_strict`,
`hours_nyonly`, `above`, `sell`; S99 `below`) are all TEST-only passes that failed TRAIN, exactly the
pattern the TRAIN-only selection was written to refuse.

## 8. Next hypotheses (each needs its own pre-registration; none is recommended before retiring)

1. **Model the live entry-drift gate in the harness** (S5 ltp at fill vs the level, budget
   `min(0.5, 0.25 × stop)`, `shared.gate_rules.entry_drift_exceeded`) and re-run S100's baseline: the
   only live evidence in either strategy's favour is that the gated subset beat the ungated sim by
   ~0.35 PF over 2.5 months. If the modelled gate reproduces that on 24 months, it is a *manager*
   result worth having for every strategy; if not, the live August was noise.
2. **S99's aligned-bias remainder at a wider target.** `_TP_R` was refuted on the ungated population;
   it has not been tested on the with-bias subset (PF 0.85–0.95), whose losers are no longer the
   counter-trend fills. Low prior (the 1.5R → 2R+ trade-off was monotone negative before).
3. **S100 short-only with the 2.5 floor** as a combined arm — not run here because neither parent
   qualified; it is the only S100 configuration whose TEST is above 1 at both quote costs
   (`sell` 1.035 / 0.966 is not), and it should be expected to fail TRAIN like both parents.

## Files
- `PROTOCOL.md` (pre-registration + one deviation), `arms.py` (the 43 arms), `s99v.py` (S99 variant
  mechanics: min-gap, buffer, bias with two definitions; identity-checked), `run_sweep.py`,
  `score.py` (quote-S5 resolver + bars), `decide.py` (TRAIN-justified selection), `anatomy.py`,
  `book.py`, `live_record.py` (read-only prod pull).
- `results/sweep_s99/`, `results/sweep_s100/` (per-arm json + trades), `results/quote/` (per-arm
  quote-S5 resolution), `results/scores.csv`, `results/table_s99.{csv,md}`, `results/table_s100.{csv,md}`,
  `results/decision.txt`, `results/anatomy.txt`, `results/book.txt`, `results/live_record.txt`,
  `results/live_*.csv`, `results/sweep_s99.log`, `results/sweep_s100.log`.
