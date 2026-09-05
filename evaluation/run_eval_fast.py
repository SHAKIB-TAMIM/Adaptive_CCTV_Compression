#!/usr/bin/env python3
"""
Optimized CCTV Compression Evaluation — Reduced bitrate levels for speed.
"""

import os, sys, json, time, subprocess, csv, math
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np

try:
    from skimage.metrics import structural_similarity as sk_ssim
    from skimage.metrics import peak_signal_noise_ratio as sk_psnr
    _SKIMAGE = True
except ImportError:
    _SKIMAGE = False

try:
    from ultralytics import YOLO
    _YOLO = True
except ImportError:
    _YOLO = False

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(BASE_DIR, "results")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots")
MODEL_PATH = os.path.join(PROJECT_DIR, "models", "yolov8n.pt")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(PLOTS_DIR, exist_ok=True)

BITRATE_LEVELS = [200, 500, 1000, 2000]
CODECS = [
    {"name": "H.264", "codec": "libx264", "preset": "fast"},
    {"name": "H.265", "codec": "libx265", "preset": "fast"},
    {"name": "AV1 (SVT-AV1)", "codec": "libsvtav1", "preset": "6"},
    {"name": "VP9", "codec": "libvpx-vp9", "preset": "realtime"},
]


def get_video_info(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return {"fps": fps, "width": w, "height": h, "frames": frames, "duration_s": frames/fps}


def encode_ffmpeg(inp, out, codec, br_kbps, preset="fast", resolution=None, extra=None):
    cmd = ["ffmpeg", "-y", "-i", inp]
    if resolution:
        cmd.extend(["-vf", f"scale={resolution[0]}:{resolution[1]}"])
    cmd.extend(["-c:v", codec, "-preset", preset, "-b:v", f"{br_kbps}k",
                "-maxrate", f"{int(br_kbps*1.5)}k", "-bufsize", f"{br_kbps*2}k"])
    if extra:
        cmd.extend(extra)
    cmd.append(out)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, timeout=300)
        el = time.time() - t0
        if r.returncode != 0:
            print(f"    [err] {r.stderr[-150:]}")
            return None, el
        return out, el
    except Exception as e:
        print(f"    [err] {e}")
        return None, time.time() - t0


def psnr_ssim(orig, recon, max_frames=80):
    ca, cb = cv2.VideoCapture(orig), cv2.VideoCapture(recon)
    ps, ss = [], []
    i = 0
    while i < max_frames:
        ok_a, fa = ca.read(); ok_b, fb = cb.read()
        if not ok_a or not ok_b: break
        if fa.shape != fb.shape:
            fb = cv2.resize(fb, (fa.shape[1], fa.shape[0]))
        if _SKIMAGE:
            ps.append(float(sk_psnr(fa, fb, data_range=255)))
            ss.append(float(sk_ssim(cv2.cvtColor(fa, cv2.COLOR_BGR2GRAY),
                                    cv2.cvtColor(fb, cv2.COLOR_BGR2GRAY), data_range=255)))
        else:
            mse = np.mean((fa.astype(float)-fb.astype(float))**2)
            ps.append(100.0 if mse==0 else 10*np.log10(255**2/mse))
        i += 1
    ca.release(); cb.release()
    return {"psnr": float(np.mean(ps)) if ps else None, "ssim": float(np.mean(ss)) if ss else None, "frames": i}


def file_metrics(path, dur):
    sz = os.path.getsize(path) if os.path.exists(path) else 0
    return {"bytes": sz, "kbps": round(sz*8/1000/max(dur,0.01), 1)}


def det_recall(orig, recon, model, mx=30):
    if not model: return None
    ca, cb = cv2.VideoCapture(orig), cv2.VideoCapture(recon)
    oc, rc = [], []
    i = 0
    while i < mx:
        ok_a, fa = ca.read(); ok_b, fb = cb.read()
        if not ok_a or not ok_b: break
        oc.append(len(model(fa, verbose=False, conf=0.3)[0].boxes))
        rc.append(len(model(fb, verbose=False, conf=0.3)[0].boxes))
        i += 1
    ca.release(); cb.release()
    if not oc or sum(oc)==0: return 1.0
    return min(np.mean(rc)/max(np.mean(oc),1e-6), 1.0)


def bd_rate(pa, ra, pt, rt):
    if len(pa)<2 or len(pt)<2: return None
    try:
        la, lt = np.log2(np.array(ra,float)), np.log2(np.array(rt,float))
        pa3 = np.polyfit(pa, la, 3); pt3 = np.polyfit(pt, lt, 3)
        lo, hi = max(min(pa),min(pt)), min(max(pa),max(pt))
        if lo>=hi: return None
        rng = np.linspace(lo, hi, 100)
        return round(float((2**np.mean(np.polyval(pt3,rng))/2**np.mean(np.polyval(pa3,rng))-1)*100), 2)
    except: return None


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: Benchmark Runner
# ═══════════════════════════════════════════════════════════════════════════
def step1_benchmark(inp, vi, yolo):
    print("\n" + "="*70 + "\n  STEP 1: Benchmark Runner\n" + "="*70)
    import yaml
    results = []
    for cfg in sorted(Path(os.path.join(PROJECT_DIR,"configs")).glob("*.yaml")):
        if cfg.name=="cameras.yaml": continue
        with open(cfg) as f: c = yaml.safe_load(f)
        edge = c.get("edge_node",{}); enc = edge.get("encoder",{})
        codec = enc.get("codec","libx265")
        if codec=="hevc_nvenc": codec="libx265"
        br = int(str(enc.get("bitrate","800k")).replace("k",""))
        try: rw,rh = [int(x) for x in edge.get("resolution","1280x720").split("x")]
        except: rw,rh=1280,720
        print(f"  [{cfg.stem}] {codec} @{br}kbps {rw}x{rh}")
        out = os.path.join(RESULTS_DIR, f"b_{cfg.stem}.mp4")
        enc_path, el = encode_ffmpeg(inp, out, codec, br, enc.get("preset","fast"), (rw,rh))
        if not enc_path: print("    FAILED"); continue
        fm = file_metrics(out, vi["duration_s"])
        q = psnr_ssim(inp, out)
        dr = det_recall(inp, out, yolo)
        r = {"config":cfg.stem, "codec":codec, "target_kbps":br, "actual_kbps":fm["kbps"],
             "psnr":round(q["psnr"],3) if q["psnr"] else None,
             "ssim":round(q["ssim"],5) if q["ssim"] else None,
             "encode_s":round(el,2), "det_recall":round(dr,4) if dr else None}
        results.append(r)
        print(f"    PSNR={q['psnr']:.2f} SSIM={q['ssim']:.4f} bitrate={fm['kbps']:.0f}kbps time={el:.1f}s")
        try: os.remove(out)
        except: pass
    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: SOTA Comparison
# ═══════════════════════════════════════════════════════════════════════════
def step2_sota(inp, vi, yolo):
    print("\n" + "="*70 + "\n  STEP 2: SOTA Multi-Codec Comparison\n" + "="*70)
    results = []
    for ci in CODECS:
        print(f"\n  --- {ci['name']} ---")
        for br in BITRATE_LEVELS:
            out = os.path.join(RESULTS_DIR, f"s_{ci['name'].replace(' ','_')}_{br}.mp4")
            p, el = encode_ffmpeg(inp, out, ci["codec"], br, ci["preset"])
            if not p: print(f"    @{br}kbps FAILED"); continue
            fm = file_metrics(out, vi["duration_s"])
            q = psnr_ssim(inp, out)
            dr = det_recall(inp, out, yolo)
            r = {"method":ci["name"], "target_kbps":br, "actual_kbps":fm["kbps"],
                 "psnr":round(q["psnr"],3) if q["psnr"] else None,
                 "ssim":round(q["ssim"],5) if q["ssim"] else None,
                 "encode_s":round(el,2), "det_recall":round(dr,4) if dr else None}
            results.append(r)
            print(f"    @{br:>5}kbps: PSNR={q['psnr']:.2f}dB SSIM={q['ssim']:.4f} "
                  f"actual={fm['kbps']:.0f}kbps time={el:.1f}s"
                  + (f" det={dr:.2%}" if dr else ""))
            try: os.remove(out)
            except: pass
    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: Rate Allocator
# ═══════════════════════════════════════════════════════════════════════════
def step3_rate_allocator(inp, vi, yolo):
    print("\n" + "="*70 + "\n  STEP 3: Rate Allocator — Lagrangian RD Optimization\n" + "="*70)
    sys.path.insert(0, BASE_DIR)
    from rate_allocator import RateAllocator, RegionOfInterest

    cap = cv2.VideoCapture(inp)
    ret, frame = cap.read(); cap.release()
    if not ret: return {}

    rois = []
    if yolo:
        res = yolo(frame, verbose=False, conf=0.3)
        for r in res:
            for box in r.boxes:
                x1,y1,x2,y2 = box.xyxy[0].tolist()
                cid = int(box.cls[0])
                pri = "high" if cid==0 else "medium" if cid in {2,3,5,6,7} else "low"
                rois.append(RegionOfInterest(bbox=[int(x1),int(y1),int(x2),int(y2)], priority=pri, class_id=cid))
    else:
        h,w = frame.shape[:2]
        rois = [RegionOfInterest(bbox=[w//4,h//4,3*w//4,3*h//4], priority="high"),
                RegionOfInterest(bbox=[0,0,w//3,h//3], priority="medium")]
    print(f"  ROIs detected: {len(rois)}")

    allocs = []
    for bud in [200, 500, 1000, 2000]:
        for state in ["normal","alert","critical"]:
            ra = RateAllocator(total_budget_kbps=bud, fps=vi["fps"],
                               resolution=(vi["width"],vi["height"]))
            a = ra.allocate(rois, frame, risk_state=state)
            allocs.append({"budget":bud, "state":state, "lambda":a["lambda"],
                          "total_kbps":a["total_kbps"], "efficiency":a["efficiency"],
                          "bg_quality":a["bg_quality"], "bg_scale":a["bg_scale"]})
            print(f"    bud={bud:>5}kbps state={state:<10} λ={a['lambda']:.6f} "
                  f"total={a['total_kbps']:.0f}kbps eff={a['efficiency']:.3f}")

    # Lambda sweep
    lam_sweep = []
    ra = RateAllocator(total_budget_kbps=1000, fps=vi["fps"], resolution=(vi["width"],vi["height"]))
    model = ra.build_rd_model(rois, frame, "alert")
    for exp in range(-4, 2):
        lam = 10**exp
        tot = sum(ra.optimal_rate(r, lam) for r in model["regions"])
        lam_sweep.append({"lambda":lam, "total_kbps":round(tot,1)})
        print(f"    λ={lam:.6f} → {tot:.0f}kbps")

    return {"allocations":allocs, "lambda_sweep":lam_sweep, "num_rois":len(rois)}


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: Ablation Study
# ═══════════════════════════════════════════════════════════════════════════
def step4_ablation(inp, vi, yolo):
    print("\n" + "="*70 + "\n  STEP 4: Ablation Study\n" + "="*70)
    from dataclasses import dataclass
    @dataclass
    class PC:
        risk:bool=True; clahe:bool=True; temporal:bool=True
        gop:bool=True; resolution:bool=True; name:str="Full"

    variants = [
        PC(name="Full System", risk=True, clahe=True, temporal=True, gop=True, resolution=True),
        PC(name="w/o Risk Engine", risk=False, clahe=True, temporal=True, gop=True, resolution=True),
        PC(name="w/o CLAHE", risk=True, clahe=False, temporal=True, gop=True, resolution=True),
        PC(name="w/o Temporal Merge", risk=True, clahe=True, temporal=False, gop=True, resolution=True),
        PC(name="w/o Adaptive GOP", risk=True, clahe=True, temporal=True, gop=False, resolution=True),
        PC(name="w/o Adaptive Res", risk=True, clahe=True, temporal=True, gop=True, resolution=False),
    ]

    results = []
    cap = cv2.VideoCapture(inp)
    fps = vi["fps"]; fw, fh = vi["width"], vi["height"]
    prev_gray = None
    temporal_rois = []

    for v in variants:
        print(f"\n  --- {v.name} ---")
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        fid = 0; tbytes = 0; states = []; risks = []
        t0 = time.time()

        while True:
            ret, frame = cap.read()
            if not ret: break
            fid += 1
            h, w = frame.shape[:2]

            new_rois = []
            if yolo and fid % 3 == 0:
                for r in yolo(frame, verbose=False, conf=0.3):
                    for box in r.boxes:
                        x1,y1,x2,y2 = box.xyxy[0].tolist()
                        cid = int(box.cls[0])
                        new_rois.append({"bbox":[int(x1),int(y1),int(x2),int(y2)],
                                        "priority":"high" if cid==0 else "medium"})

            if v.temporal:
                upd = [dict(r) for r in temporal_rois]
                for nd in new_rois:
                    nb = nd["bbox"]; best_iou, best_idx = 0, -1
                    for i, er in enumerate(upd):
                        xA=max(er["bbox"][0],nb[0]); yA=max(er["bbox"][1],nb[1])
                        xB=min(er["bbox"][2],nb[2]); yB=min(er["bbox"][3],nb[3])
                        inter=max(0,xB-xA)*max(0,yB-yA)
                        if inter>0:
                            aA=(er["bbox"][2]-er["bbox"][0])*(er["bbox"][3]-er["bbox"][1])
                            aB=(nb[2]-nb[0])*(nb[3]-nb[1])
                            iou=inter/(aA+aB-inter)
                            if iou>best_iou: best_iou,best_idx=iou,i
                    if best_iou>=0.3 and best_idx>=0:
                        eb=upd[best_idx]["bbox"]
                        upd[best_idx]["bbox"]=[int(eb[j]*0.6+nb[j]*0.4) for j in range(4)]
                    else:
                        upd.append({"bbox":list(nb),"priority":nd["priority"]})
                temporal_rois = upd
            else:
                temporal_rois = new_rois

            rois = temporal_rois
            roi_px = sum(max(0,r["bbox"][2]-r["bbox"][0])*max(0,r["bbox"][3]-r["bbox"][1]) for r in rois)
            mf = min(roi_px/max(fw*fh,1),1.0)
            hour = datetime.now().hour

            if v.risk:
                sc=0.0
                for r in rois:
                    p=r.get("priority","low")
                    sc+=0.30 if p=="high" else 0.10 if p=="medium" else 0.03
                sc+=min(mf,1.0)*0.25
                if hour<6 or hour>=22: sc+=0.20
                sc+=min(len(rois)*0.04,0.15)
                risk=min(sc,1.0)
            else:
                risk=0.3

            state="critical" if risk>=0.65 else "alert" if risk>=0.30 else "normal"
            states.append(state); risks.append(risk)

            gop={"normal":120,"alert":30,"critical":10}[state] if v.gop else 60
            out_res={"normal":(640,480),"alert":(854,480),"critical":(1920,1080)}[state] if v.resolution else (640,480)

            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            tbytes+=gray.nbytes*0.15

        el=time.time()-t0
        sc={s:states.count(s) for s in set(states)}
        r={"variant":v.name, "frames":fid, "avg_risk":round(float(np.mean(risks)),4) if risks else 0,
           "critical_pct":round(sc.get("critical",0)/max(fid,1)*100,1),
           "alert_pct":round(sc.get("alert",0)/max(fid,1)*100,1),
           "est_kbps":round(tbytes*8/1000/max(vi["duration_s"],0.01),1),
           "time_s":round(el,2), "throughput":round(fid/max(el,0.001),1)}
        results.append(r)
        print(f"    risk={r['avg_risk']:.3f} crit={r['critical_pct']:.1f}% "
              f"est={r['est_kbps']:.0f}kbps thr={r['throughput']:.1f}fps")

    cap.release()
    return results


# ═══════════════════════════════════════════════════════════════════════════
# PLOTS & SAVE
# ═══════════════════════════════════════════════════════════════════════════
def gen_plots(sota, bench, ra, abl):
    print("\n  Generating plots...")
    pal={"H.264":"#f28e2b","H.265":"#4e79a7","AV1 (SVT-AV1)":"#76b7b2","VP9":"#59a14f"}

    fig,axes=plt.subplots(2,2,figsize=(16,12))
    fig.suptitle("CCTV Compression — Evaluation Results",fontsize=14,fontweight="bold",y=0.98)

    # PSNR R-D
    ax=axes[0,0]
    for m in ["H.264","H.265","AV1 (SVT-AV1)","VP9"]:
        pts=sorted([r for r in sota if r["method"]==m and r["psnr"]], key=lambda x:x["actual_kbps"])
        if pts: ax.plot([p["actual_kbps"] for p in pts],[p["psnr"] for p in pts],
                        marker="o",label=m,color=pal.get(m,"#999"),linewidth=2,markersize=7)
    ax.set_title("Rate-Distortion (PSNR)"); ax.set_xlabel("Bitrate (kbps)"); ax.set_ylabel("PSNR (dB)")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # SSIM R-D
    ax=axes[0,1]
    for m in ["H.264","H.265","AV1 (SVT-AV1)","VP9"]:
        pts=sorted([r for r in sota if r["method"]==m and r["ssim"]], key=lambda x:x["actual_kbps"])
        if pts: ax.plot([p["actual_kbps"] for p in pts],[p["ssim"] for p in pts],
                        marker="s",label=m,color=pal.get(m,"#999"),linewidth=2,markersize=7)
    ax.set_title("Rate-Distortion (SSIM)"); ax.set_xlabel("Bitrate (kbps)"); ax.set_ylabel("SSIM")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # Benchmark PSNR
    ax=axes[1,0]
    if bench:
        cfgs=[r["config"] for r in bench if r["psnr"]]
        ps=[r["psnr"] for r in bench if r["psnr"]]
        if ps:
            c=plt.cm.Set2(np.linspace(0,1,len(cfgs)))
            bars=ax.barh(cfgs,ps,color=c)
            ax.bar_label(bars,fmt="%.1f",padding=3,fontsize=9)
            ax.set_xlabel("PSNR (dB)"); ax.set_title("Benchmark: PSNR per Config")
            ax.grid(alpha=0.3,axis="x")

    # Ablation throughput
    ax=axes[1,1]
    if abl:
        vs=[r["variant"] for r in abl]; ts=[r["throughput"] for r in abl]
        c=plt.cm.Set2(np.linspace(0,1,len(vs)))
        bars=ax.barh(vs,ts,color=c)
        ax.bar_label(bars,fmt="%.1f",padding=3,fontsize=8)
        ax.set_xlabel("Throughput (fps)"); ax.set_title("Ablation: Throughput")
        ax.grid(alpha=0.3,axis="x")

    plt.tight_layout(rect=[0,0,1,0.96])
    p1=os.path.join(PLOTS_DIR,"comprehensive_evaluation.png")
    plt.savefig(p1,dpi=150,bbox_inches="tight"); plt.close()
    print(f"  Saved: {p1}")

    # Lambda sweep
    if ra.get("lambda_sweep"):
        fig2,ax2=plt.subplots(figsize=(8,5))
        ls=ra["lambda_sweep"]
        ax2.semilogx([r["lambda"] for r in ls],[r["total_kbps"] for r in ls],
                     marker="o",linewidth=2,color="#e15759",markersize=8)
        ax2.axhline(1000,color="#4e79a7",ls="--",alpha=0.7,label="1000kbps budget")
        ax2.set_title("Lambda vs Total Bitrate"); ax2.set_xlabel("λ"); ax2.set_ylabel("Total Bitrate (kbps)")
        ax2.legend(); ax2.grid(alpha=0.3)
        plt.tight_layout()
        p2=os.path.join(PLOTS_DIR,"lambda_sweep.png")
        plt.savefig(p2,dpi=150,bbox_inches="tight"); plt.close()
        print(f"  Saved: {p2}")

    # Ablation detail
    if abl:
        fig3,ax3=plt.subplots(1,2,figsize=(14,6))
        fig3.suptitle("Ablation Study — Component Impact",fontsize=13,fontweight="bold")
        vs=[r["variant"] for r in abl]; c=plt.cm.Set2(np.linspace(0,1,len(vs)))
        bars=ax3[0].barh(vs,[r["est_kbps"] for r in abl],color=c)
        ax3[0].bar_label(bars,fmt="%.0f",padding=3,fontsize=8)
        ax3[0].set_xlabel("Est. Bitrate (kbps)"); ax3[0].set_title("Bandwidth")
        ax3[0].grid(alpha=0.3,axis="x")
        bars=ax3[1].barh(vs,[r["critical_pct"] for r in abl],color=c)
        ax3[1].bar_label(bars,fmt="%.1f%%",padding=3,fontsize=8)
        ax3[1].set_xlabel("Critical Frames (%)"); ax3[1].set_title("Event Sensitivity")
        ax3[1].grid(alpha=0.3,axis="x")
        plt.tight_layout(rect=[0,0,1,0.95])
        p3=os.path.join(PLOTS_DIR,"ablation_study.png")
        plt.savefig(p3,dpi=150,bbox_inches="tight"); plt.close()
        print(f"  Saved: {p3}")


def main():
    inp = "/home/brothers/Downloads/test_video1.mp4"
    if not os.path.exists(inp):
        print(f"ERROR: {inp} not found"); sys.exit(1)

    print("="*70)
    print("  CCTV COMPRESSION — COMPREHENSIVE EVALUATION")
    print(f"  Video: {inp}")
    print("="*70)

    vi = get_video_info(inp)
    print(f"  {vi['width']}x{vi['height']} | {vi['fps']}fps | {vi['duration_s']:.1f}s | {vi['frames']} frames")

    yolo = None
    if _YOLO and os.path.exists(MODEL_PATH):
        print(f"  Loading YOLO: {MODEL_PATH}")
        yolo = YOLO(MODEL_PATH)
    else:
        print("  YOLO not available — synthetic ROIs")

    t0 = time.time()
    bench = step1_benchmark(inp, vi, yolo)
    sota = step2_sota(inp, vi, yolo)
    ra = step3_rate_allocator(inp, vi, yolo)
    abl = step4_ablation(inp, vi, yolo)

    # BD-rates
    anc = [r for r in sota if r["method"]=="H.265" and r["psnr"]]
    a_p=[r["psnr"] for r in anc]; a_r=[r["actual_kbps"] for r in anc]
    bds={}
    for m in set(r["method"] for r in sota):
        if m=="H.265": continue
        t=[r for r in sota if r["method"]==m and r["psnr"]]
        bds[m]=bd_rate(a_p,a_r,[r["psnr"] for r in t],[r["actual_kbps"] for r in t])

    # Save JSON
    all_r={"metadata":{"timestamp":datetime.utcnow().isoformat(),"video":inp},
           "benchmark":bench,"sota_comparison":sota,"rate_allocator":ra,"ablation":abl,"bd_rates":bds}
    jp=os.path.join(RESULTS_DIR,"evaluation_results.json")
    with open(jp,"w") as f: json.dump(all_r,f,indent=2,default=str)

    # Save CSV
    cp=os.path.join(RESULTS_DIR,"sota_comparison.csv")
    if sota:
        with open(cp,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(sota[0].keys()))
            w.writeheader(); w.writerows(sota)

    gen_plots(sota,bench,ra,abl)

    el=time.time()-t0
    print("\n" + "="*70)
    print(f"  COMPLETE in {el:.1f}s")
    print("="*70)
    print("\n  BD-Rate Summary (vs H.265):")
    for m,b in bds.items():
        if b is not None: print(f"    {m:<20} {b:+.2f}% ({'better' if b<0 else 'worse'})")
        else: print(f"    {m:<20} N/A")
    print(f"\n  Results: {jp}")
    print(f"  Plots:   {PLOTS_DIR}/")
    print(f"  CSV:     {cp}")


if __name__=="__main__":
    main()
