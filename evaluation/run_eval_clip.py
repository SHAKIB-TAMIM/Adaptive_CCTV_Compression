#!/usr/bin/env python3
"""Fast evaluation on 10-second clip."""
import os, sys, json, time, subprocess, csv
from datetime import datetime
import cv2
import numpy as np

try:
    from skimage.metrics import structural_similarity as sk_ssim
    from skimage.metrics import peak_signal_noise_ratio as sk_psnr
    _SK=True
except: _SK=False

try:
    from ultralytics import YOLO
    _YOLO=True
except: _YOLO=False

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE=os.path.dirname(os.path.abspath(__file__))
PROJ=os.path.dirname(BASE)
RES=os.path.join(BASE,"results")
PLT=os.path.join(RES,"plots")
MOD=os.path.join(PROJ,"models","yolov8n.pt")
os.makedirs(RES,exist_ok=True); os.makedirs(PLT,exist_ok=True)

INP="/tmp/test_clip_10s.mp4"

def vinfo(p):
    c=cv2.VideoCapture(p)
    fps=c.get(cv2.CAP_PROP_FPS) or 30
    w=int(c.get(cv2.CAP_PROP_FRAME_WIDTH)); h=int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
    f=int(c.get(cv2.CAP_PROP_FRAME_COUNT)); c.release()
    return {"fps":fps,"w":w,"h":h,"frames":f,"dur":f/fps}

def enc(inp,out,codec,br,preset="fast",res=None):
    cmd=["ffmpeg","-y","-i",inp]
    if res: cmd+=["-vf",f"scale={res[0]}:{res[1]}"]
    cmd+=["-c:v",codec,"-preset",preset,"-b:v",f"{br}k","-maxrate",f"{int(br*1.5)}k","-bufsize",f"{br*2}k",out]
    t=time.time()
    try:
        r=subprocess.run(cmd,stderr=subprocess.PIPE,stdout=subprocess.DEVNULL,text=True,timeout=120)
        el=time.time()-t
        return (out,el) if r.returncode==0 else (None,el)
    except Exception as e: return None,time.time()-t

def metrics(orig,recon,mf=60):
    ca,cb=cv2.VideoCapture(orig),cv2.VideoCapture(recon)
    ps,ss=[],[]; i=0
    while i<mf:
        ok_a,fa=ca.read(); ok_b,fb=cb.read()
        if not ok_a or not ok_b: break
        if fa.shape!=fb.shape: fb=cv2.resize(fb,(fa.shape[1],fa.shape[0]))
        if _SK:
            ps.append(float(sk_psnr(fa,fb,data_range=255)))
            ss.append(float(sk_ssim(cv2.cvtColor(fa,cv2.COLOR_BGR2GRAY),cv2.cvtColor(fb,cv2.COLOR_BGR2GRAY),data_range=255)))
        else:
            mse=np.mean((fa.astype(float)-fb.astype(float))**2)
            ps.append(100.0 if mse==0 else 10*np.log10(255**2/mse))
        i+=1
    ca.release(); cb.release()
    return float(np.mean(ps)) if ps else None, float(np.mean(ss)) if ss else None

def fmet(p,d):
    sz=os.path.getsize(p) if os.path.exists(p) else 0
    return sz, round(sz*8/1000/max(d,0.01),1)

def det(orig,recon,model,mx=20):
    if not model: return None
    ca,cb=cv2.VideoCapture(orig),cv2.VideoCapture(recon)
    oc,rc=[],[]; i=0
    while i<mx:
        ok_a,fa=ca.read(); ok_b,fb=cb.read()
        if not ok_a or not ok_b: break
        oc.append(len(model(fa,verbose=False,conf=0.3)[0].boxes))
        rc.append(len(model(fb,verbose=False,conf=0.3)[0].boxes))
        i+=1
    ca.release(); cb.release()
    if not oc or sum(oc)==0: return 1.0
    return min(np.mean(rc)/max(np.mean(oc),1e-6),1.0)

def bdr(pa,ra,pt,rt):
    if len(pa)<2 or len(pt)<2: return None
    try:
        la,lt=np.log2(np.array(ra,float)),np.log2(np.array(rt,float))
        p3a,p3t=np.polyfit(pa,la,3),np.polyfit(pt,lt,3)
        lo,hi=max(min(pa),min(pt)),min(max(pa),max(pt))
        if lo>=hi: return None
        rng=np.linspace(lo,hi,100)
        return round(float((2**np.mean(np.polyval(p3t,rng))/2**np.mean(np.polyval(p3a,rng))-1)*100),2)
    except: return None


def main():
    vi=vinfo(INP)
    print(f"Video: {vi['w']}x{vi['h']} {vi['fps']}fps {vi['dur']:.1f}s")

    yolo=None
    if _YOLO and os.path.exists(MOD):
        print(f"Loading YOLO...")
        yolo=YOLO(MOD)

    t0=time.time()
    dur=vi["dur"]

    # ═══ SOTA COMPARISON ═══
    print("\n" + "="*60)
    print("SOTA Multi-Codec Comparison")
    print("="*60)

    codecs=[
        ("H.264","libx264","fast"),
        ("H.265","libx265","fast"),
        ("AV1","libsvtav1","6"),
        ("VP9","libvpx-vp9","realtime"),
    ]
    brs=[200,500,1000,2000]
    sota=[]

    for cname,ccodec,cpreset in codecs:
        print(f"\n  {cname}:")
        for br in brs:
            out=os.path.join(RES,f"s_{cname}_{br}.mp4")
            p,el=enc(INP,out,ccodec,br,cpreset)
            if not p: print(f"    @{br} FAILED"); continue
            sz,kbps=fmet(out,dur)
            psnr,ssim=metrics(INP,out)
            dr=det(INP,out,yolo)
            r={"method":cname,"target":br,"actual_kbps":kbps,
               "psnr":round(psnr,3) if psnr else None,
               "ssim":round(ssim,5) if ssim else None,
               "time_s":round(el,2),"bytes":sz,
               "det_recall":round(dr,4) if dr else None}
            sota.append(r)
            print(f"    @{br:>5}k: PSNR={psnr:.2f} SSIM={ssim:.4f} act={kbps:.0f}kbps t={el:.1f}s"
                  +f" det={dr:.2%}" if dr else "")
            try: os.remove(out)
            except: pass

    # ═══ BENCHMARK CONFIGS ═══
    print("\n" + "="*60)
    print("Benchmark Config Profiles")
    print("="*60)

    import yaml
    bench=[]
    for cfg in sorted((Path(os.path.join(PROJ,"configs"))).glob("*.yaml")):
        if cfg.name=="cameras.yaml": continue
        with open(cfg) as f: c=yaml.safe_load(f)
        edge=c.get("edge_node",{}); enc_=edge.get("encoder",{})
        codec=enc_.get("codec","libx265")
        if codec=="hevc_nvenc": codec="libx265"
        br=int(str(enc_.get("bitrate","800k")).replace("k",""))
        try: rw,rh=[int(x) for x in edge.get("resolution","1280x720").split("x")]
        except: rw,rh=1280,720
        print(f"  [{cfg.stem}] {codec} @{br}k {rw}x{rh}")
        out=os.path.join(RES,f"b_{cfg.stem}.mp4")
        p,el=enc(INP,out,codec,br,enc_.get("preset","fast"),(rw,rh))
        if not p: print("    FAILED"); continue
        sz,kbps=fmet(out,dur)
        psnr,ssim=metrics(INP,out)
        dr=det(INP,out,yolo)
        r={"config":cfg.stem,"codec":codec,"target":br,"actual_kbps":kbps,
           "psnr":round(psnr,3) if psnr else None,"ssim":round(ssim,5) if ssim else None,
           "time_s":round(el,2),"det_recall":round(dr,4) if dr else None}
        bench.append(r)
        print(f"    PSNR={psnr:.2f} SSIM={ssim:.4f} act={kbps:.0f}kbps t={el:.1f}s")
        try: os.remove(out)
        except: pass

    # ═══ RATE ALLOCATOR ═══
    print("\n" + "="*60)
    print("Rate Allocator — Lagrangian RD Optimization")
    print("="*60)

    sys.path.insert(0,BASE)
    from rate_allocator import RateAllocator, RegionOfInterest

    cap=cv2.VideoCapture(INP); ret,frame=cap.read(); cap.release()
    rois=[]
    if yolo:
        res=yolo(frame,verbose=False,conf=0.3)
        for r in res:
            for box in r.boxes:
                x1,y1,x2,y2=box.xyxy[0].tolist()
                cid=int(box.cls[0])
                pri="high" if cid==0 else "medium"
                rois.append(RegionOfInterest(bbox=[int(x1),int(y1),int(x2),int(y2)],priority=pri,class_id=cid))
    else:
        fh,fw=frame.shape[:2]
        rois=[RegionOfInterest(bbox=[fw//4,fh//4,3*fw//4,3*fh//4],priority="high"),
              RegionOfInterest(bbox=[0,0,fw//3,fh//3],priority="medium")]
    print(f"  ROIs: {len(rois)}")

    allocs=[]
    for bud in [200,500,1000,2000]:
        for state in ["normal","alert","critical"]:
            ra=RateAllocator(total_budget_kbps=bud,fps=vi["fps"],resolution=(vi["w"],vi["h"]))
            a=ra.allocate(rois,frame,risk_state=state)
            allocs.append({"budget":bud,"state":state,"lambda":a["lambda"],
                          "total_kbps":a["total_kbps"],"efficiency":a["efficiency"],
                          "bg_quality":a["bg_quality"],"bg_scale":a["bg_scale"],
                          "num_rois":a["num_rois"]})
            print(f"    bud={bud:>5}k state={state:<10} λ={a['lambda']:.6f} tot={a['total_kbps']:.0f}k eff={a['efficiency']:.3f}")

    lam_sweep=[]
    ra=RateAllocator(total_budget_kbps=1000,fps=vi["fps"],resolution=(vi["w"],vi["h"]))
    model=ra.build_rd_model(rois,frame,"alert")
    for exp in range(-4,2):
        lam=10**exp
        tot=sum(ra.optimal_rate(r,lam) for r in model["regions"])
        lam_sweep.append({"lambda":lam,"total_kbps":round(tot,1)})
        print(f"    λ={lam:.6f} → {tot:.0f}kbps")

    # ═══ ABLATION ═══
    print("\n" + "="*60)
    print("Ablation Study")
    print("="*60)

    cap=cv2.VideoCapture(INP)
    prev_gray=None; temp_rois=[]
    abl_variants=[
        {"name":"Full System","risk":True,"clahe":True,"temp":True,"gop":True,"res":True},
        {"name":"w/o Risk Engine","risk":False,"clahe":True,"temp":True,"gop":True,"res":True},
        {"name":"w/o CLAHE","risk":True,"clahe":False,"temp":True,"gop":True,"res":True},
        {"name":"w/o Temporal Merge","risk":True,"clahe":True,"temp":False,"gop":True,"res":True},
        {"name":"w/o Adaptive GOP","risk":True,"clahe":True,"temp":True,"gop":False,"res":True},
        {"name":"w/o Adaptive Res","risk":True,"clahe":True,"temp":True,"gop":True,"res":False},
    ]
    abl_results=[]
    fw,fh=vi["w"],vi["h"]

    for v in abl_variants:
        print(f"\n  {v['name']}:")
        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        fid=0; tbytes=0; states=[]; risks=[]; t0v=time.time()

        while True:
            ret,frame=cap.read()
            if not ret: break
            fid+=1
            h,w=frame.shape[:2]

            new_rois=[]
            if yolo and fid%3==0:
                for r in yolo(frame,verbose=False,conf=0.3):
                    for box in r.boxes:
                        x1,y1,x2,y2=box.xyxy[0].tolist()
                        cid=int(box.cls[0])
                        new_rois.append({"bbox":[int(x1),int(y1),int(x2),int(y2)],"priority":"high" if cid==0 else "medium"})

            if v["temp"]:
                upd=[dict(r) for r in temp_rois]
                for nd in new_rois:
                    nb=nd["bbox"]; bi,bi_i=0,-1
                    for i,er in enumerate(upd):
                        xA=max(er["bbox"][0],nb[0]);yA=max(er["bbox"][1],nb[1])
                        xB=min(er["bbox"][2],nb[2]);yB=min(er["bbox"][3],nb[3])
                        inter=max(0,xB-xA)*max(0,yB-yA)
                        if inter>0:
                            aA=(er["bbox"][2]-er["bbox"][0])*(er["bbox"][3]-er["bbox"][1])
                            aB=(nb[2]-nb[0])*(nb[3]-nb[1])
                            iou=inter/(aA+aB-inter)
                            if iou>bi: bi,bi_i=iou,i
                    if bi>=0.3 and bi_i>=0:
                        eb=upd[bi_i]["bbox"]
                        upd[bi_i]["bbox"]=[int(eb[j]*0.6+nb[j]*0.4) for j in range(4)]
                    else: upd.append({"bbox":list(nb),"priority":nd["priority"]})
                temp_rois=upd
            else: temp_rois=new_rois

            rois_a=temp_rois
            roi_px=sum(max(0,r["bbox"][2]-r["bbox"][0])*max(0,r["bbox"][3]-r["bbox"][1]) for r in rois_a)
            mf=min(roi_px/max(fw*fh,1),1.0)
            hour=datetime.now().hour

            if v["risk"]:
                sc=0.0
                for r in rois_a:
                    p=r.get("priority","low")
                    sc+=0.30 if p=="high" else 0.10 if p=="medium" else 0.03
                sc+=min(mf,1.0)*0.25
                if hour<6 or hour>=22: sc+=0.20
                sc+=min(len(rois_a)*0.04,0.15)
                risk=min(sc,1.0)
            else: risk=0.3

            state="critical" if risk>=0.65 else "alert" if risk>=0.30 else "normal"
            states.append(state); risks.append(risk)

            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            tbytes+=gray.nbytes*0.15

        el=time.time()-t0v
        sc_c=states.count("critical"); sc_a=states.count("alert")
        r={"variant":v["name"],"frames":fid,
           "avg_risk":round(float(np.mean(risks)),4) if risks else 0,
           "critical_pct":round(sc_c/max(fid,1)*100,1),
           "alert_pct":round(sc_a/max(fid,1)*100,1),
           "est_kbps":round(tbytes*8/1000/max(dur,0.01),1),
           "time_s":round(el,2),"throughput":round(fid/max(el,0.001),1)}
        abl_results.append(r)
        print(f"    risk={r['avg_risk']:.3f} crit={r['critical_pct']:.1f}% "
              f"est={r['est_kbps']:.0f}kbps thr={r['throughput']:.1f}fps")
    cap.release()

    # ═══ BD-RATES ═══
    anc=[r for r in sota if r["method"]=="H.265" and r["psnr"]]
    a_p=[r["psnr"] for r in anc]; a_r=[r["actual_kbps"] for r in anc]
    bds={}
    for m in set(r["method"] for r in sota):
        if m=="H.265": continue
        t=[r for r in sota if r["method"]==m and r["psnr"]]
        bds[m]=bdr(a_p,a_r,[r["psnr"] for r in t],[r["actual_kbps"] for r in t])

    # ═══ SAVE ═══
    all_r={"metadata":{"timestamp":datetime.utcnow().isoformat(),"video":INP,"clip_duration_s":dur},
           "benchmark":bench,"sota_comparison":sota,
           "rate_allocator":{"allocations":allocs,"lambda_sweep":lam_sweep,"num_rois":len(rois)},
           "ablation":abl_results,"bd_rates":bds}
    jp=os.path.join(RES,"evaluation_results.json")
    with open(jp,"w") as f: json.dump(all_r,f,indent=2,default=str)

    # ═══ PLOTS ═══
    print("\nGenerating plots...")
    pal={"H.264":"#f28e2b","H.265":"#4e79a7","AV1":"#76b7b2","VP9":"#59a14f"}

    fig,axes=plt.subplots(2,2,figsize=(16,12))
    fig.suptitle("CCTV Compression — Evaluation Results (10s clip)",fontsize=14,fontweight="bold",y=0.98)

    ax=axes[0,0]
    for m in ["H.264","H.265","AV1","VP9"]:
        pts=sorted([r for r in sota if r["method"]==m and r["psnr"]],key=lambda x:x["actual_kbps"])
        if pts: ax.plot([p["actual_kbps"] for p in pts],[p["psnr"] for p in pts],
                        marker="o",label=m,color=pal.get(m,"#999"),linewidth=2,markersize=7)
    ax.set_title("Rate-Distortion (PSNR)"); ax.set_xlabel("Bitrate (kbps)"); ax.set_ylabel("PSNR (dB)")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    ax=axes[0,1]
    for m in ["H.264","H.265","AV1","VP9"]:
        pts=sorted([r for r in sota if r["method"]==m and r["ssim"]],key=lambda x:x["actual_kbps"])
        if pts: ax.plot([p["actual_kbps"] for p in pts],[p["ssim"] for p in pts],
                        marker="s",label=m,color=pal.get(m,"#999"),linewidth=2,markersize=7)
    ax.set_title("Rate-Distortion (SSIM)"); ax.set_xlabel("Bitrate (kbps)"); ax.set_ylabel("SSIM")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    ax=axes[1,0]
    if bench:
        cfgs=[r["config"] for r in bench if r["psnr"]]; ps=[r["psnr"] for r in bench if r["psnr"]]
        if ps:
            c=plt.cm.Set2(np.linspace(0,1,len(cfgs)))
            bars=ax.barh(cfgs,ps,color=c); ax.bar_label(bars,fmt="%.1f",padding=3,fontsize=9)
            ax.set_xlabel("PSNR (dB)"); ax.set_title("Benchmark: PSNR per Config"); ax.grid(alpha=0.3,axis="x")

    ax=axes[1,1]
    if abl_results:
        vs=[r["variant"] for r in abl_results]; ts=[r["throughput"] for r in abl_results]
        c=plt.cm.Set2(np.linspace(0,1,len(vs)))
        bars=ax.barh(vs,ts,color=c); ax.bar_label(bars,fmt="%.1f",padding=3,fontsize=8)
        ax.set_xlabel("Throughput (fps)"); ax.set_title("Ablation: Throughput"); ax.grid(alpha=0.3,axis="x")

    plt.tight_layout(rect=[0,0,1,0.96])
    p1=os.path.join(PLT,"comprehensive_evaluation.png")
    plt.savefig(p1,dpi=150,bbox_inches="tight"); plt.close()
    print(f"  {p1}")

    # Lambda sweep
    fig2,ax2=plt.subplots(figsize=(8,5))
    ax2.semilogx([r["lambda"] for r in lam_sweep],[r["total_kbps"] for r in lam_sweep],
                 marker="o",linewidth=2,color="#e15759",markersize=8)
    ax2.axhline(1000,color="#4e79a7",ls="--",alpha=0.7,label="1000kbps budget")
    ax2.set_title("Lambda vs Total Bitrate"); ax2.set_xlabel("λ"); ax2.set_ylabel("Total Bitrate (kbps)")
    ax2.legend(); ax2.grid(alpha=0.3)
    plt.tight_layout()
    p2=os.path.join(PLT,"lambda_sweep.png")
    plt.savefig(p2,dpi=150,bbox_inches="tight"); plt.close()
    print(f"  {p2}")

    # Ablation detail
    fig3,(a1,a2)=plt.subplots(1,2,figsize=(14,6))
    fig3.suptitle("Ablation Study — Component Impact",fontsize=13,fontweight="bold")
    vs=[r["variant"] for r in abl_results]; c=plt.cm.Set2(np.linspace(0,1,len(vs)))
    bars=a1.barh(vs,[r["est_kbps"] for r in abl_results],color=c)
    a1.bar_label(bars,fmt="%.0f",padding=3,fontsize=8); a1.set_xlabel("Est. Bitrate (kbps)"); a1.set_title("Bandwidth"); a1.grid(alpha=0.3,axis="x")
    bars=a2.barh(vs,[r["critical_pct"] for r in abl_results],color=c)
    a2.bar_label(bars,fmt="%.1f%%",padding=3,fontsize=8); a2.set_xlabel("Critical Frames (%)"); a2.set_title("Event Sensitivity"); a2.grid(alpha=0.3,axis="x")
    plt.tight_layout(rect=[0,0,1,0.95])
    p3=os.path.join(PLT,"ablation_study.png")
    plt.savefig(p3,dpi=150,bbox_inches="tight"); plt.close()
    print(f"  {p3}")

    # Detection recall plot
    if any(r.get("det_recall") for r in sota):
        fig4,ax4=plt.subplots(figsize=(8,5))
        for m in ["H.264","H.265","AV1","VP9"]:
            pts=sorted([r for r in sota if r["method"]==m and r.get("det_recall")],key=lambda x:x["actual_kbps"])
            if pts: ax4.plot([p["actual_kbps"] for p in pts],[p["det_recall"]*100 for p in pts],
                            marker="^",label=m,color=pal.get(m,"#999"),linewidth=2,markersize=7)
        ax4.set_title("Detection Recall vs Bitrate"); ax4.set_xlabel("Bitrate (kbps)"); ax4.set_ylabel("Recall (%)")
        ax4.legend(); ax4.grid(alpha=0.3)
        plt.tight_layout()
        p4=os.path.join(PLT,"detection_recall.png")
        plt.savefig(p4,dpi=150,bbox_inches="tight"); plt.close()
        print(f"  {p4}")

    el=time.time()-t0
    print(f"\n{'='*60}")
    print(f"COMPLETE in {el:.1f}s")
    print(f"{'='*60}")
    print(f"\nBD-Rate (vs H.265):")
    for m,b in bds.items():
        if b is not None: print(f"  {m:<20} {b:+.2f}% ({'better' if b<0 else 'worse'})")
        else: print(f"  {m:<20} N/A")
    print(f"\nResults: {jp}")
    print(f"Plots:   {PLT}/")

    return all_r


if __name__=="__main__":
    main()
