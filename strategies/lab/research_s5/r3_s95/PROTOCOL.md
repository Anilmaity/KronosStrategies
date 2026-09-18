# r3_s95 — reconciliation of the only honest PASS (pre-registered 2026-09-19, before any number)

**Question.** s95_session_breakout passes the honest 24-month screen (closed-bar harness, 0.75/1.00:
TEST PF 1.315 / 1.274, TRAIN 1.279, 60 % TEST months positive) yet was retired 2026-07-23 as an
operator decision after 13 live trades (8 during the known stale-fill bug, −$86; 5 after, +$28) because
the manager's session_vol gating cut its sim value. Its harness books entries ~1.1 pt worse than the
market offered (execution study: `entry_comp_vs_nominal` −0.60, `nominal_gap_mean` −1.07). Is it a
strategy the book should re-arm on paper?

**Inputs.** Honest trade list `lab/results/xau2y_stage1_fixed/s95_session_breakout_c0.75.trades.parquet`
(1,014 trades, sessions 1/7/12/13/14 UTC, median stop 20 pt, max hold 180 min, 63 % TP / 26 % TIME /
11 % SL on mid). S5 cache. Live rows for 'Session Breakout M5 ORB' and 'S95 Session Breakout'.

**H1 — fill-model expectancy.** Apply the r2_live_parity `02c` convention to every honest trade:
market fill at the sided S5 quote at bar-open + 66 s; `entry_drift` gate with live defaults
(`shared.gate_rules.drift_budget_pts`); accepted trades resolve SL/TP/TIME on the quote from the fill
over the 180-min hold; +0.26 pt slip on SL exits. No further cost (the spread is paid in the fill).
Report TRAIN/TEST PF, points, drift-rejection share, and the standard quote-S5 nominal-entry series at
0.45/0.70 alongside for comparability. Bars 1–5 of the xau2y protocol on the 02c series (n ≥ 40; TEST
PF > 1 under 02c AND under quote-S5 0.70; TRAIN PF > 0.9; monthly; regime).

**H2 — the nominal-entry term.** Mean (fill − nominal) in the trade direction: expected negative
(the market fills better than the harness assumed). Report it and the share of trades where the
quote fill is better than nominal.

**H3 — live window.** The 02c sim on the same dates (2026-07-01 → 07-17) vs the 13 live trades:
count, points, outcomes; the first 8 are flagged as stale-fill-bug trades (entries one M5 bar late)
and reported separately. No conclusion is drawn from n = 13; this is a consistency check only.

**H4 — book fit.** Daily-R correlation with S93 (honest Stage-1 list) and with the copy leg's
live daily USD; equal-risk 24-month book S93 + s95 vs S93 alone (R per trade, months positive).

**Decision rule.** Recommend PAPER re-arm (always_on policy, not session_vol) iff H1 passes bars 1–5
under 02c and under quote-S5 0.70 and H2 confirms the fill term is not worse than the harness's.
Otherwise report the numbers and stop. Comparisons: 1 primary series, 4 read-outs; nothing tuned.
