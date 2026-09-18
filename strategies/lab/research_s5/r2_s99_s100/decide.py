"""decide.py -- apply PROTOCOL.md §3 to results/scores.csv.

Per dimension: qualifiers = arms beating the incumbent on TRAIN quote-S5 PF AND TRAIN quote-S5
points at 0.70; pick = best TRAIN PF among qualifiers; the pick's TEST is then read against
bars 1-7 (bars 1-5 on quote-S5 at 0.45 with the 0.70 twin for bar 2; bar 7 = beat the
incumbent on TEST quote-S5 PF AND points at 0.70). Every arm's numbers are printed (the
non-qualifiers "for the record"). Output: results/decision.txt, results/table_<s>.csv.

    ../.venv/bin/python -m lab.research_s5.r2_s99_s100.decide
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

DIMS = {
    "s99": {
        "_incumbent": "s99_base_c0.75",
        "A mingap": ["s99_mingap0.25_c0.75", "s99_mingap0.5_c0.75", "s99_mingap1.0_c0.75"],
        "B buf": ["s99_buf0.1_c0.75", "s99_buf0.5_c0.75", "s99_buf1.0_c0.75"],
        "C bias": ["s99_bias_with_ema21_c0.75", "s99_bias_with_h1_c0.75"],
        "C bias (controls)": ["s99_bias_against_ema21_c0.75", "s99_bias_against_h1_c0.75"],
        "D sweepn": ["s99_sweepn24_c0.75", "s99_sweepn96_c0.75"],
        "E retrace": ["s99_retrace12_c0.75", "s99_retrace48_c0.75"],
        "F sides": ["s99_buy_c0.75", "s99_sell_c0.75"],
        "F regime": ["s99_above_c0.75", "s99_below_c0.75"],
        "F session": ["s99_nolondon_c0.75", "s99_nony_c0.75"],
        "_controls": ["s99_ident_c0.75", "s99_base_w400_c0.75", "s99_base_c1.00"],
    },
    "s100": {
        "_incumbent": "s100_base_c0.75",
        "A minsl": ["s100_minsl2.0_c0.75", "s100_minsl2.5_c0.75", "s100_minsl3.0_c0.75"],
        "B hours": ["s100_hours_drop1_c0.75", "s100_hours_drop12_c0.75", "s100_hours_drop123_c0.75",
                    "s100_hours_nyonly_c0.75", "s100_hours_asialondon_c0.75"],
        "C er": ["s100_er_ranging_w1600_c0.75", "s100_er_strict_w1600_c0.75"],
        "D tpr": ["s100_tpr2.0_c0.75", "s100_tpr3.0_c0.75"],
        "E sides": ["s100_buy_c0.75", "s100_sell_c0.75"],
        "E regime": ["s100_above_c0.75", "s100_below_c0.75"],
        "_controls": ["s100_er_off_w1600_c0.75", "s100_base_c1.00"],
    },
}
# dimensions whose incumbent is not the baseline
ALT_INCUMBENT = {"C er": "s100_er_off_w1600_c0.75"}


def main() -> None:
    sc = pd.read_csv(RES / "scores.csv")
    lines = []
    def p(*a):
        s = " ".join(str(x) for x in a); print(s); lines.append(s)

    def get(label, model, cost):
        r = sc[(sc.label == label) & (sc.model == model) & (abs(sc.cost - cost) < 1e-9)]
        return r.iloc[0] if len(r) else None

    for strat, dims in DIMS.items():
        inc0 = dims["_incumbent"]
        rows = []
        labels = [inc0] + dims["_controls"] + [l for k, v in dims.items() if not k.startswith("_") for l in v]
        for lab in labels:
            m75, m100 = get(lab, "mid_M1", 0.75), get(lab, "mid_M1", 1.00)
            q45, q70 = get(lab, "quote_S5", 0.45), get(lab, "quote_S5", 0.70)
            if q70 is None:
                rows.append(dict(label=lab, status="missing")); continue
            rows.append(dict(
                label=lab, n=int(q70.n),
                mid_train_pf75=m75.train_pf, mid_test_pf75=m75.test_pf, mid_test_pf100=m100.test_pf, mid_test_pts100=m100.test_pts,
                q_train_pf45=q45.train_pf, q_train_pf70=q70.train_pf, q_train_pts70=q70.train_pts,
                q_test_pf45=q45.test_pf, q_test_pf70=q70.test_pf, q_test_pts70=q70.test_pts, q_test_n=int(q70.test_n),
                q_test_pos_share=q45.test_pos_share, q_test_max_share=q45.test_max_share, q_gold_corr=q45.gold_corr,
                q_up_pts=q45.up_pts, q_dn_pts=q45.dn_pts,
                bar1=bool(q45.bar1_n), bar2=bool(q45.bar2_base and q70.bar2_base), bar3=bool(q45.bar3_train),
                bar4=bool(q45.bar4_monthly), bar5=bool(q45.bar5_regime),
                n_outside_s5=int(q70.n_outside_s5)))
        tab = pd.DataFrame(rows).set_index("label")
        tab.to_csv(RES / f"table_{strat}.csv")
        p(f"\n==================== {strat.upper()} ====================")
        with pd.option_context("display.width", 300, "display.max_columns", 40, "display.max_rows", 200):
            p(tab.to_string())

        p(f"\n--- {strat} TRAIN-justified selection (PROTOCOL §3) ---")
        for dim, arms in dims.items():
            if dim.startswith("_"):
                continue
            inc = ALT_INCUMBENT.get(dim, inc0)
            if inc not in tab.index:
                p(f"{dim}: incumbent {inc} missing"); continue
            I = tab.loc[inc]
            p(f"\n{dim}  (incumbent {inc}: TRAIN q@0.70 PF {I.q_train_pf70} / pts {I.q_train_pts70}; TEST q@0.70 PF {I.q_test_pf70} / pts {I.q_test_pts70})")
            quals = []
            for a in arms:
                if a not in tab.index or "n" not in tab.columns or pd.isna(tab.loc[a].get("n")):
                    p(f"  {a}: missing"); continue
                R = tab.loc[a]
                q = (R.q_train_pf70 > I.q_train_pf70) and (R.q_train_pts70 > I.q_train_pts70)
                b7 = (R.q_test_pf70 > I.q_test_pf70) and (R.q_test_pts70 > I.q_test_pts70)
                bars15 = all([R.bar1, R.bar2, R.bar3, R.bar4, R.bar5])
                p(f"  {a:<34} n={int(R.n):<5} TRAIN q70 PF {R.q_train_pf70:<6} pts {R.q_train_pts70:>8}  qualifies={str(q):<5} | "
                  f"TEST q45/q70 PF {R.q_test_pf45}/{R.q_test_pf70} pts@70 {R.q_test_pts70:>8}  bars1-5={'PASS' if bars15 else 'fail'} "
                  f"[{int(R.bar1)}{int(R.bar2)}{int(R.bar3)}{int(R.bar4)}{int(R.bar5)}] bar7={str(b7)}")
                if q:
                    quals.append((R.q_train_pf70, a))
            if dim.endswith("(controls)"):
                p("  (control arms: not eligible for selection)"); continue
            if not quals:
                p("  => no qualifier on TRAIN: dimension dead"); continue
            pick = max(quals)[1]
            R = tab.loc[pick]
            b7 = (R.q_test_pf70 > I.q_test_pf70) and (R.q_test_pts70 > I.q_test_pts70)
            bars15 = all([R.bar1, R.bar2, R.bar3, R.bar4, R.bar5])
            verdict = "PASS bars 1-5 and 7 -> candidate (bar 6 plateau judged in REPORT)" if (bars15 and b7) else \
                      ("fails bars 1-5" if not bars15 else "passes 1-5, fails bar 7")
            p(f"  => PICK {pick} (TRAIN q70 PF {R.q_train_pf70}); TEST read once: q45/q70 PF {R.q_test_pf45}/{R.q_test_pf70}, pts@70 {R.q_test_pts70} -> {verdict}")
    (RES / "decision.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
