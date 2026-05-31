"""
btc_research/dashboard.py
-------------------------
Build a self-contained interactive dashboard (btc_research/dashboard.html) from
the research artifacts + live recompute. Zero install: Plotly.js from CDN, all
data embedded. Open the HTML in any browser.

Panels:
  - verdict banner + KPI cards
  - 4h price with z-score and live reversion-signal markers
  - candidate equity curves
  - volatility by UTC hour / session
  - momentum-vs-reversion structure table + intraday edge-scan table
  - validation gauntlet table (7 checks per candidate)
  - LIVE SIGNAL card (current 4h z-score, levels)
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from btc_research.data import get_candles
from btc_research import engine, strategies as S

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "dashboard.html")


def _load_json(name):
    p = os.path.join(CACHE, name)
    return json.load(open(p)) if os.path.exists(p) else {}


def _equity(trades):
    ts = sorted([t for t in trades if t.outcome != "OPEN"], key=lambda t: t.exit_time)
    x, y, cum = [], [], 0.0
    for t in ts:
        cum += t.pnl_pts
        x.append(pd.Timestamp(t.exit_time).strftime("%Y-%m-%d"))
        y.append(round(cum, 1))
    return x, y


def build():
    df = get_candles("4h").reset_index(drop=True)
    N, ZT = 30, 1.5
    df["ma"] = df["close"].rolling(N).mean()
    df["sd"] = df["close"].rolling(N).std()
    df["z"] = (df["close"] - df["ma"]) / df["sd"]
    atr = S._atr(df, 14)

    # price + signal markers
    px_x = df["time"].dt.strftime("%Y-%m-%d %H:%M").tolist()
    px_y = df["close"].round(1).tolist()
    z_y = df["z"].round(2).tolist()
    sig_short = (df["z"] > ZT) & (df["z"].shift(1) <= ZT)
    sig_long = (df["z"] < -ZT) & (df["z"].shift(1) >= -ZT)
    sx_s = df.loc[sig_short, "time"].dt.strftime("%Y-%m-%d %H:%M").tolist()
    sy_s = df.loc[sig_short, "close"].round(1).tolist()
    sx_l = df.loc[sig_long, "time"].dt.strftime("%Y-%m-%d %H:%M").tolist()
    sy_l = df.loc[sig_long, "close"].round(1).tolist()

    # equity curves
    eq = {}
    for name, gen in {
        "ZREV z=1.5": lambda d: S.zscore_revert_swing(d, N=30, z_thr=1.5, sl_atr=5.0, max_hold=6),
        "ZREV z=2.0": lambda d: S.zscore_revert_swing(d, N=30, z_thr=2.0, sl_atr=5.0, max_hold=6),
        "SWEEPREV": lambda d: S.sweep_reversal(d, L=20, sl_buf=5.0, max_hold=6),
    }.items():
        trades = engine.run(df, gen(df), name, cost_pts=30.0)
        x, y = _equity(trades)
        eq[name] = {"x": x, "y": y}

    # live signal (latest closed 4h bar)
    last = df.iloc[-1]
    a = float(atr.iloc[-1])
    z = float(last["z"])
    live = {
        "time": str(last["time"]), "price": round(float(last["close"]), 1),
        "z": round(z, 2), "ma": round(float(last["ma"]), 1), "atr": round(a, 1),
    }
    if z > ZT:
        live.update(signal="SHORT (fade)", entry=live["price"],
                    sl=round(live["price"] + 5 * a, 1), tp=live["ma"])
    elif z < -ZT:
        live.update(signal="LONG (fade)", entry=live["price"],
                    sl=round(live["price"] - 5 * a, 1), tp=live["ma"])
    else:
        live.update(signal="NO SIGNAL — waiting for |z|>1.5", entry=None, sl=None, tp=None)

    char = _load_json("characterization.json")
    trend = _load_json("trend_scan.json")
    val = _load_json("validation.json")
    fwd = _load_json("forward_test.json")

    D = {
        "price": {"x": px_x, "y": px_y, "z": z_y,
                  "sx_s": sx_s, "sy_s": sy_s, "sx_l": sx_l, "sy_l": sy_l},
        "equity": eq,
        "vol_by_hour": char.get("vol_by_hour_bps", {}),
        "vol_by_session": char.get("vol_by_session_bps", {}),
        "structure": trend.get("structure", {}),
        "regime": trend.get("regime", {}),
        "edge_scans": char.get("edge_scans", []),
        "validation": val,
        "forward": fwd,
        "live": live,
    }
    html = _render(D)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"[dashboard] wrote {OUT}")
    print(f"[dashboard] live signal: {live['signal']}  (z={live['z']}, price={live['price']})")


def _render(D):
    data_json = json.dumps(D)
    return r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>BTC_USD Edge Research — Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 body{background:#0e1117;color:#e6e6e6;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:24px}
 h1{margin:0 0 4px} .sub{color:#8b949e;margin-bottom:18px}
 .banner{background:#3d2b00;border:1px solid #8a6d00;border-radius:8px;padding:14px 18px;margin-bottom:18px}
 .banner b{color:#ffce5a}
 .cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:18px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;min-width:150px}
 .card .v{font-size:22px;font-weight:700} .card .k{color:#8b949e;font-size:12px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
 .panel{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:18px}
 .full{grid-column:1/3}
 table{border-collapse:collapse;width:100%;font-size:13px} th,td{padding:5px 8px;text-align:right;border-bottom:1px solid #21262d}
 th:first-child,td:first-child{text-align:left} th{color:#8b949e;font-weight:600}
 .pass{color:#3fb950} .fail{color:#f85149} .warn{color:#d29922}
 .sig{font-size:20px;font-weight:700}
</style></head><body>
<h1>BTC_USD Edge Research</h1>
<div class="sub">24.4M ticks · 2024-12-30 → 2026-05-29 · TimescaleDB <code>ltp</code> · generated by btc_research/</div>
<div class="banner">
 <b>VERDICT: REFINE — do not deploy as-is.</b> Intraday BTC is near-efficient (no simple edge).
 A genuine swing mean-reversion edge exists (fade 4h z-extremes → mean: PF&nbsp;1.31, WR&nbsp;57.6%,
 survives cost stress &amp; Monte-Carlo) but it is <b>regime-dependent</b> — strong in 2025's range,
 decays in 2026's downtrend (out-of-sample WFE&nbsp;0.35&nbsp;&lt;&nbsp;0.5). A weekly-trend gate calibrated
 only on 2025 and <b>tested once on held-out 2026 did NOT rescue it</b> (forward PF&nbsp;0.97→0.83,
 walk-forward WFE&nbsp;0.01) — every gate that wins in-sample fails out-of-sample. The only honest path
 to deployable is a <b>genuine forward paper-test as new ticks arrive</b> (<code>forward_test.py --refresh</code>).
</div>
<div id="cards" class="cards"></div>
<div id="live" class="panel"></div>
<div class="grid">
 <div class="panel full"><h3>4h price + z-score &amp; live reversion signals</h3><div id="price" style="height:420px"></div></div>
 <div class="panel full"><h3>Candidate equity curves (pts, $30 round-trip cost)</h3><div id="equity" style="height:360px"></div></div>
 <div class="panel"><h3>Volatility by UTC hour (5m |ret| bps)</h3><div id="volhour" style="height:300px"></div></div>
 <div class="panel"><h3>Momentum vs reversion by timeframe</h3><div id="structure"></div></div>
 <div class="panel full"><h3>Validation gauntlet (7 checks)</h3><div id="validation"></div></div>
 <div class="panel full"><h3>Held-out forward test — weekly gate calibrated on 2025, tested once on 2026</h3><div id="forward"></div></div>
 <div class="panel full"><h3>Intraday raw edge scans (signed bps · |t|&gt;2 ≈ significant)</h3><div id="scans"></div></div>
</div>
<script>
const D = """ + data_json + r""";
const dark = {paper_bgcolor:'#161b22',plot_bgcolor:'#161b22',font:{color:'#e6e6e6'},margin:{t:10,r:10,b:40,l:55}};

// KPI cards
const reg = D.regime||{};
const cards=[['Ticks','24.39M'],['Span (mo)','~17'],['Daily range','3.46%'],
 ['Net move',(reg.total_ret_pct||0)+'%'],['Range',(reg.min||0).toLocaleString()+'–'+(reg.max||0).toLocaleString()],
 ['Up-days',(reg.up_day_pct||0)+'%']];
document.getElementById('cards').innerHTML = cards.map(c=>
 `<div class="card"><div class="v">${c[1]}</div><div class="k">${c[0]}</div></div>`).join('');

// live signal
const L=D.live; const col = L.signal.startsWith('NO')?'#8b949e':'#ffce5a';
document.getElementById('live').innerHTML =
 `<h3>Live signal — latest 4h close ${L.time}</h3>`+
 `<div class="sig" style="color:${col}">${L.signal}</div>`+
 `<div class="k">price ${L.price} · z ${L.z} · mean ${L.ma} · ATR ${L.atr}`+
 (L.entry?` · entry ${L.entry} · SL ${L.sl} · TP ${L.tp}`:'')+`</div>`;

// price + z + markers (z on secondary axis)
Plotly.newPlot('price',[
 {x:D.price.x,y:D.price.y,name:'4h close',line:{color:'#58a6ff',width:1}},
 {x:D.price.x,y:D.price.z,name:'z-score',yaxis:'y2',line:{color:'#8b949e',width:1}},
 {x:D.price.sx_s,y:D.price.sy_s,name:'fade short',mode:'markers',marker:{color:'#f85149',size:7,symbol:'triangle-down'}},
 {x:D.price.sx_l,y:D.price.sy_l,name:'fade long',mode:'markers',marker:{color:'#3fb950',size:7,symbol:'triangle-up'}}
],Object.assign({},dark,{yaxis:{title:'price'},yaxis2:{title:'z',overlaying:'y',side:'right',showgrid:false},
 legend:{orientation:'h'}}),{responsive:true});

// equity
Plotly.newPlot('equity',Object.entries(D.equity).map(([k,v])=>({x:v.x,y:v.y,name:k,mode:'lines'})),
 Object.assign({},dark,{yaxis:{title:'cum pnl (pts)'},legend:{orientation:'h'}}),{responsive:true});

// vol by hour
const vh=D.vol_by_hour||{}; const hk=Object.keys(vh).sort((a,b)=>a-b);
Plotly.newPlot('volhour',[{x:hk,y:hk.map(h=>vh[h]),type:'bar',marker:{color:'#58a6ff'}}],
 Object.assign({},dark,{xaxis:{title:'UTC hour'},yaxis:{title:'bps'}}),{responsive:true});

// structure table
const st=D.structure||{}; let sh='<table><tr><th>TF</th><th>autocorr(1)</th><th>VR(5)</th><th>VR(10)</th><th>read</th></tr>';
for(const tf of Object.keys(st)){const a=st[tf].autocorr||{},v=st[tf].vr||{};
 const vr5=v['5']??v[5], read=(vr5<0.9)?'<span class="pass">mean-revert</span>':(vr5>1.1?'<span class="warn">trend</span>':'mixed');
 sh+=`<tr><td>${tf}</td><td>${a['1']??a[1]}</td><td>${vr5}</td><td>${v['10']??v[10]}</td><td>${read}</td></tr>`;}
document.getElementById('structure').innerHTML=sh+'</table>';

// validation table
const val=D.validation||[]; let vh2='<table><tr><th>candidate</th><th>n</th><th>WR%</th><th>PF</th><th>expR</th><th>DD/PnL</th><th>OOS WFE</th><th>k-fold+</th><th>MC P(loss)</th><th>verdict</th></tr>';
for(const r of val){const f=r.full||{},o=r.oos||{},kf=r.kfold||{},mc=r.monte_carlo||{},vd=r.verdict||{};
 const vc=vd.verdict==='PASS'?'pass':(vd.verdict==='REFINE'?'warn':'fail');
 const wfeC=(o.wfe>=0.5)?'pass':'fail';
 vh2+=`<tr><td>${r.name}</td><td>${f.n}</td><td>${f.wr}</td><td>${f.pf}</td><td>${f.exp_R}</td><td>${vd.dd_ratio}</td>`+
 `<td class="${wfeC}">${o.wfe}</td><td>${kf.consistency_pct}%</td><td>${mc.p_negative}</td><td class="${vc}">${vd.verdict}</td></tr>`;}
document.getElementById('validation').innerHTML=vh2+'</table>';

// forward test
const F=D.forward||{};
if(F.forward_ungated){
 const du=(F.design_top||{}).ungated||{}; const lock=F.locked_gate||'-';
 const dg=(F.design_top||{})[lock]||{};
 const fu=(F.forward_ungated||{}).summary||{}, fg=(F.forward_gated||{}).summary||{};
 const better=(fg.exp_R>fu.exp_R)&&(fg.pf>1.0);
 let fh=`<table><tr><th>window</th><th>config</th><th>n</th><th>WR%</th><th>PF</th><th>expR</th><th>pnl</th></tr>`+
  `<tr><td>design 2025</td><td>ungated</td><td>${du.n}</td><td>${du.wr??'-'}</td><td>${du.pf}</td><td>${du.exp_R}</td><td>${du.pnl}</td></tr>`+
  `<tr><td>design 2025</td><td>${lock} (best)</td><td>${dg.n}</td><td>${dg.wr??'-'}</td><td class="pass">${dg.pf}</td><td class="pass">${dg.exp_R}</td><td>${dg.pnl}</td></tr>`+
  `<tr><td><b>forward 2026 (held-out)</b></td><td>ungated</td><td>${fu.n}</td><td>${fu.wr}</td><td>${fu.pf}</td><td>${fu.exp_R}</td><td>${fu.pnl}</td></tr>`+
  `<tr><td><b>forward 2026 (held-out)</b></td><td>gated (locked)</td><td>${fg.n}</td><td>${fg.wr}</td><td class="fail">${fg.pf}</td><td class="fail">${fg.exp_R}</td><td>${fg.pnl}</td></tr>`+
  `</table>`+
  `<div class="k" style="margin-top:8px">Rolling walk-forward WFE=<b class="fail">${F.wf_wfe}</b> (need ≥0.5) · OOS-consistency ${F.wf_consistency}% · `+
  `<b style="color:${better?'#3fb950':'#f85149'}">${better?'gate improved held-out':'gate did NOT improve held-out — edge stays regime-fragile'}</b></div>`;
 document.getElementById('forward').innerHTML=fh;
}else{document.getElementById('forward').innerHTML='<div class="k">run forward_test.py</div>';}

// edge scans
const sc=D.edge_scans||[]; let sch='<table><tr><th>edge</th><th>n</th><th>edge bps</th><th>win%</th><th>t-stat</th></tr>';
for(const r of sc){const tc=Math.abs(r.t_stat)>2?(r.t_stat>0?'pass':'fail'):'';
 sch+=`<tr><td>${r.name}</td><td>${r.n}</td><td>${r.mean_edge_bps}</td><td>${r.win_pct}</td><td class="${tc}">${r.t_stat}</td></tr>`;}
document.getElementById('scans').innerHTML=sch+'</table>';
</script></body></html>"""


if __name__ == "__main__":
    build()
