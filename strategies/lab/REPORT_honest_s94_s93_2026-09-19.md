# S94 and S93 on the honest harness (2026-09-19)

Pre-registration: `lab/campaigns/honest_s94_s93.py` (written before any arm ran). Closed-bar
harness (`1c9ae30`), mid-M1 0.75/1.00, every arm re-resolved on S5 bid/ask at 0.45/0.70
(`lab/results/s5exit/honest/`). Bars 1–5 on mid-M1 from `campaign_score` (`SCORE.csv`); the
quote table in `QUOTE_S5_SUMMARY.csv`. Selection on TRAIN; TEST read once. Nothing hand-typed.

## S94 sweep reversal — the live config (long-only, `_SD_MULT` 2.5) does not clear the bars

| arm | bars 1–5 (mid) | mid TEST base / stress | mid TRAIN | **quote 0.70 TRAIN PF / pts** | **quote 0.70 TEST PF / pts** | +months |
|---|---|---|---|---|---|---|
| both / 2.0 (shipped baseline) | FAIL | 0.812 / 0.771 | 0.746 | 0.718 / −552 | 0.812 / −656 | 20 % |
| both / 2.5 | FAIL | 0.931 / 0.887 | 0.814 | 0.781 / −446 | 0.947 / −189 | 50 % |
| both / 3.0 | FAIL | 0.965 / 0.921 | 0.844 | 0.819 / −381 | 0.929 / −266 | 40 % |
| long / 2.0 | FAIL | 0.845 / 0.808 | 0.856 | 0.828 / −209 | 0.851 / −315 | 50 % |
| **long / 2.5 (live)** | FAIL | 1.013 / 0.972 | 0.916 | **0.878 / −156** | **1.029 / +62** (one month = 3.1× net) | 50 % |
| long / 3.0 | FAIL | 1.063 / 1.021 | 0.964 | 0.922 / −103 | 1.026 / +58 (one month = 3.6× net) | 50 % |

The two directional findings of the leaky campaigns survive: **shorts lose everywhere** and
**PF rises monotonically with `_SD_MULT`** on both sides (a real, small effect). But no arm
passes: under the quote every arm is **negative on TRAIN**, and the two TEST-positive arms net
+60 pts on ~230 trades with one month carrying 3× the net. The live config fails bar 2 (mid
stress 0.972), bar 3 (quote TRAIN 0.878) and bar 4. The live record (Jul 18–Sep 17: BUY +$484,
PF 1.65, n ≈ 40) is inside the noise the parity study measured for every legacy leg.

**Verdict: S94 long-only/2.5 is not a validated edge. Retire, or arm PAPER for the drift-gate
parity question only.** `_SD_MULT` 3.0 is directionally better but still fails; nothing to ship.

## S93 FVG scalp — the shipped config is its own honest optimum, and it is marginal

| arm | bars 1–5 (mid) | mid TEST base / stress | mid TRAIN | quote 0.70 TRAIN PF / pts | quote 0.70 TEST PF / pts | +months / max-month share |
|---|---|---|---|---|---|---|
| **(13,14) / 1.5 (live)** | **NEAR** | **1.158 / 1.095** | **1.079** | **0.936 / −48** | **1.115 / +105** | 50 % / 1.07 |
| (13,14) / 2.0 | NEAR | 1.112 / 1.056 | 1.023 | 0.956 / −36 | 1.063 / +62 | 30 % / 1.74 |
| (13,14) / 1.0 | FAIL | 1.013 / 0.945 | 1.006 | 0.850 / −94 | 1.009 / +7 | 40 % |
| (12,13,14) / 1.0–2.0 | FAIL | 0.906–1.027 | 0.971–1.030 | 0.821–0.891 | 0.890–0.966 | 10–40 % |
| pre-09-01 hours (7,8,9,12,13,14) / any | FAIL | 0.812–0.860 | 0.832–0.881 | 0.698–0.768 | 0.745–0.794 | 10–20 % |

The **09-01 hours change is confirmed honestly** (pre-09-01 hours: quote TEST 0.75–0.79 vs
1.12), and `_TP_R` 1.5 beats 1.0 and 2.0 on TEST as before. But the honest S93 is thin: under
the quote it is negative on TRAIN (−48 pts) and its TEST net (+105 pts) is carried by one
month (share 1.07: the other nine months sum to zero). It fails the monthly bar on mid too
(max month 79 % of net).

**Verdict: keep S93 as configured — nothing in the grid beats it — but it is a marginal leg
whose honest expectancy is ≈ 0 on TRAIN and one-month-dependent on TEST.** Its live record
(PF 0.82 → 1.22 after the hours change, n 60) says the same: not distinguishable from zero.

## What this leaves the book with

Three armed legs after 09-19: **S93** (marginal, honest), **S94 long-only** (fails), **Neymar
VIP** (copy, −$414 since July). On the honest harness the only strategy that passes the
24-month screen is **s95** (retired 07-23 on its live record; its sim under-books entries by
1.1 pt, so 1.32/1.27 is a floor) — the reconciliation is now the highest-value open item.
