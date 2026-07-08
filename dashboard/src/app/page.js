"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import io from "socket.io-client";
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const VIEW_NS = "http://localhost:5000/view";
const METRICS_URL = "http://localhost:5000/metrics";
const EVENTS_URL = "http://localhost:5000/events";
const CAMERAS_URL = "http://localhost:5000/cameras";

export default function Home() {
  const router = useRouter();
  const canvasRef = useRef(null);
  const socketRef = useRef(null);
  const lastFrameTimes = useRef({});

  // Camera selection
  const [cameras, setCameras] = useState([]);
  const [selectedCamera, setSelectedCamera] = useState("camera_0");
  const selectedCameraRef = useRef("camera_0");

  // Real-time system states
  const [isConnected, setIsConnected] = useState(false);
  const [frameId, setFrameId] = useState(0);
  const [fps, setFps] = useState(0);
  const [rois, setRois] = useState([]);
  const [risk, setRisk] = useState(0.0);
  const [surveillance_state, setSurveillance_state] = useState("normal");
  
  // Bandwidth metrics
  const [sioKbps, setSioKbps] = useState(0);
  const [udpKbps, setUdpKbps] = useState(0);
  const [bandwidthSaved, setBandwidthSaved] = useState(0);
  const [metricsHistory, setMetricsHistory] = useState([]);
  const [activityHistory, setActivityHistory] = useState([]);

  // Adaptive codec state (GOP & resolution from edge node)
  const [gopSize, setGopSize] = useState(120);
  const [resW, setResW] = useState(640);
  const [resH, setResH] = useState(480);

  // Sliders & Controls (Synced with server)
  const [bgScale, setBgScale] = useState(0.5);
  const [bgQuality, setBgQuality] = useState(20);
  const [roiQuality, setRoiQuality] = useState(90);
  const [detectEveryN, setDetectEveryN] = useState(3);

  // Switches
  const [privacyBlur, setPrivacyBlur] = useState(false);
  const [ethicalMode, setEthicalMode] = useState(false);
  const [maskFaces, setMaskFaces] = useState(false);
  const [codec, setCodec] = useState("libx265");
  const [bitrate, setBitrate] = useState(2000);

  // Event list (risk transitions)
  const [eventLog, setEventLog] = useState([]);
  const [activeProfile, setActiveProfile] = useState("custom");

  // Local state for auto-adaptation lock
  const [autoLockSecs, setAutoLockSecs] = useState(0);

  useEffect(() => {
    // Connect to Node.js socket server
    const socket = io(VIEW_NS, { transports: ["websocket", "polling"] });
    socketRef.current = socket;

    socket.on("connect", () => {
      setIsConnected(true);
      console.log("Connected to CCTV view server:", socket.id);
    });

    socket.on("disconnect", () => {
      setIsConnected(false);
    });

    // Frame receiver — filter by selected camera (using ref to avoid stale closure)
    socket.on("frame", (msg) => {
      try {
        const camId = msg.camera_id || "camera_0";
        if (camId !== selectedCameraRef.current) return;

        setFrameId(msg.frame_id);
        setRisk(msg.risk || 0.0);
        setSurveillance_state(msg.state || "normal");
        setRois(msg.rois || []);
        setGopSize(msg.gop_size || 120);
        setResW(msg.res_w || 0);
        setResH(msg.res_h || 0);

        const now = Date.now();
        const last = lastFrameTimes.current[camId] || now;
        setFps((1000 / (now - last || 1)).toFixed(1));
        lastFrameTimes.current[camId] = now;

        // Draw onto local canvas
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        const img = new Image();

        img.onload = () => {
          const streamW = msg.orig_w || img.width;
          const streamH = msg.orig_h || img.height;
          if (canvas.width !== streamW || canvas.height !== streamH) {
            canvas.width = streamW;
            canvas.height = streamH;
          }

          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

          // Draw HUD corner markings
          drawHUDOverlay(ctx, canvas.width, canvas.height);

          // Render Priority ROI bounding boxes on top with neon glows
          (msg.rois || []).forEach((r) => {
            const [x1, y1, x2, y2] = r.bbox;
            const priority = r.priority || "medium";

            let color = "rgba(52, 211, 153, 1)"; // Low -> green
            if (priority === "high") color = "rgba(239, 68, 68, 1)"; // High -> red
            if (priority === "medium") color = "rgba(245, 158, 11, 1)"; // Medium -> orange

            // Draw glowing rectangle
            ctx.shadowColor = color;
            ctx.shadowBlur = 12;
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
            ctx.shadowBlur = 0;

            // Draw classification badge label
            ctx.font = "bold 13px monospace";
            const label = priority.toUpperCase();
            const labelW = ctx.measureText(label).width + 12;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.roundRect(x1, y1 - 22, labelW, 20, 3);
            ctx.fill();

            ctx.fillStyle = "#000000";
            ctx.fillText(label, x1 + 6, y1 - 7);
          });
        };

        img.src = `data:image/jpeg;base64,${msg.vis_frame}`;
      } catch (err) {
        console.error("Frame render error:", err);
      }
    });

    // Real-time metrics receiver
    socket.on("metrics", (m) => {
      const sio = parseFloat(m.sio_kbps) || 0;
      const udp = parseFloat(m.udp_kbps) || 0;
      setSioKbps(sio);
      setUdpKbps(udp);
      setBandwidthSaved(sio > 0 ? Math.max(0, ((sio - udp) / sio) * 100) : 0);

      // Add to metrics history array
      setMetricsHistory((prev) => {
        const entry = {
          time: new Date(m.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          sio: parseFloat(sio.toFixed(1)),
          udp: parseFloat(udp.toFixed(1)),
          psnr: parseFloat(m.psnr) || 0,
        };
        const next = [...prev, entry];
        return next.slice(-25); // retain last 25 entries
      });
    });

    // Risk Event notification receiver
    socket.on("event", (evt) => {
      setEventLog((prev) => [evt, ...prev].slice(0, 100));
    });

    // Fallback/Initial fetching of events & historical metrics
    fetch(EVENTS_URL)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) setEventLog(data.reverse());
      })
      .catch(() => {});  // silent — server may not be up yet

    fetch(METRICS_URL)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          const formatted = data.slice(-25).map((m) => ({
            time: new Date(m.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
            sio: parseFloat(parseFloat(m.sio_kbps || 0).toFixed(1)),
            udp: parseFloat(parseFloat(m.udp_kbps || 0).toFixed(1)),
            psnr: parseFloat(m.psnr) || 0,
          }));
          setMetricsHistory(formatted);
        }
      })
      .catch(() => {});  // silent — server may not be up yet

    // Fetch camera list
    fetch(CAMERAS_URL)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setCameras(data);
        }
      })
      .catch(() => {});

    return () => {
      socket.disconnect();
    };
  }, []);

  // Keep ref in sync with state
  useEffect(() => {
    selectedCameraRef.current = selectedCamera;
  }, [selectedCamera]);

  // Refresh camera list periodically
  useEffect(() => {
    const interval = setInterval(() => {
      fetch(CAMERAS_URL)
        .then((res) => res.json())
        .then((data) => {
          if (Array.isArray(data)) setCameras(data);
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Update activity charts on frame updates
  useEffect(() => {
    if (frameId % 5 === 0) {
      setActivityHistory((prev) => {
        const entry = {
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          risk: parseFloat(risk.toFixed(2)),
          rois: rois.length,
        };
        return [...prev, entry].slice(-25);
      });
    }
  }, [frameId, risk, rois]);

  // Sync sliders to server on adjustment
  const emitControl = (changes) => {
    if (socketRef.current?.connected) {
      socketRef.current.emit("control", { camera_id: selectedCamera, ...changes });
      // Simulating control lock timer (10s)
      setAutoLockSecs(10);
    }
  };

  // Timer for user control override lock
  useEffect(() => {
    if (autoLockSecs > 0) {
      const t = setTimeout(() => setAutoLockSecs(autoLockSecs - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [autoLockSecs]);

  // Profile switches
  const applyProfile = (profileName, config) => {
    setActiveProfile(profileName);
    emitControl(config);
    if (config.bg_scale !== undefined) setBgScale(config.bg_scale);
    if (config.bg_quality !== undefined) setBgQuality(config.bg_quality);
    if (config.roi_quality !== undefined) setRoiQuality(config.roi_quality);
    if (config.detect_every_n !== undefined) setDetectEveryN(config.detect_every_n);
    if (config.privacy_blur !== undefined) setPrivacyBlur(config.privacy_blur);
    if (config.ethical_mode !== undefined) setEthicalMode(config.ethical_mode);
    if (config.mask_faces !== undefined) setMaskFaces(config.mask_faces);
    if (config.codec !== undefined) setCodec(config.codec);
    if (config.bitrate !== undefined) setBitrate(config.bitrate);
  };

  const drawHUDOverlay = (ctx, w, h) => {
    ctx.strokeStyle = "rgba(0, 240, 255, 0.4)";
    ctx.lineWidth = 2;
    const len = 20;

    // Top-Left
    ctx.beginPath();
    ctx.moveTo(10, 10 + len);
    ctx.lineTo(10, 10);
    ctx.lineTo(10 + len, 10);
    ctx.stroke();

    // Top-Right
    ctx.beginPath();
    ctx.moveTo(w - 10 - len, 10);
    ctx.lineTo(w - 10, 10);
    ctx.lineTo(w - 10, 10 + len);
    ctx.stroke();

    // Bottom-Left
    ctx.beginPath();
    ctx.moveTo(10, h - 10 - len);
    ctx.lineTo(10, h - 10);
    ctx.lineTo(10 + len, h - 10);
    ctx.stroke();

    // Bottom-Right
    ctx.beginPath();
    ctx.moveTo(w - 10 - len, h - 10);
    ctx.lineTo(w - 10, h - 10);
    ctx.lineTo(w - 10, h - 10 - len);
    ctx.stroke();
  };

  return (
    <div className="min-h-screen bg-[#08090f] text-[#c9ccd8] font-sans antialiased overflow-x-hidden">
      
      {/* Background Neon Gradients */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-purple-900/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-cyan-900/10 rounded-full blur-[120px]" />
      </div>

      {/* Header */}
      <header className="relative z-10 border-b border-slate-900 bg-slate-950/80 backdrop-blur-md px-6 py-4 flex flex-col md:flex-row items-center justify-between gap-4 shadow-xl">
        <div className="flex items-center gap-3">
          <div className="relative flex h-3 w-3">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${isConnected ? 'bg-cyan-400' : 'bg-red-400'}`}></span>
            <span className={`relative inline-flex rounded-full h-3 w-3 ${isConnected ? 'bg-cyan-500' : 'bg-red-500'}`}></span>
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-wider text-white flex items-center gap-2">
              NEXUS surveillance <span className="text-xs bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 px-2 py-0.5 rounded font-mono">V2.4</span>
            </h1>
            <p className="text-xs text-slate-500 tracking-wide">RISK-AWARE ADAPTIVE SURVEILLANCE COMPRESSION SYSTEM</p>
          </div>
        </div>

        {/* Camera Selector */}
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Camera:</label>
          <select
            value={selectedCamera}
            onChange={(e) => setSelectedCamera(e.target.value)}
            className="bg-slate-900 border border-slate-800 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-cyan-500 min-w-[160px]"
          >
            {cameras.length === 0 && <option value="camera_0">Camera 0 (default)</option>}
            {cameras.map((cam) => (
              <option key={cam.id} value={cam.id}>
                {cam.name || cam.id} {cam.status === "online" ? "🟢" : "🔴"}
              </option>
            ))}
          </select>
        </div>

        {/* Global Stats */}
        <div className="flex flex-wrap items-center gap-6 text-sm">
          <div className="bg-slate-900/50 border border-slate-800/60 rounded px-3 py-1.5 flex items-center gap-2">
            <span className="text-slate-500">Live State:</span>
            <span className={`font-bold px-2 py-0.5 rounded text-xs uppercase tracking-wider ${
              surveillance_state === "critical" ? "bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse" :
              surveillance_state === "alert" ? "bg-amber-500/20 text-amber-400 border border-amber-500/30" :
              "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
            }`}>
              {surveillance_state}
            </span>
          </div>
          <div className="text-slate-400">
            <span className="text-slate-500">Auto-BW Lock:</span> <span className="font-mono text-cyan-400 font-semibold">{autoLockSecs > 0 ? `${autoLockSecs}s` : "Inactive"}</span>
          </div>
          <div className="text-slate-400">
            <span className="text-slate-500">Server Status:</span> <span className="text-emerald-400 font-semibold">ONLINE</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => router.push("/grid")}
              className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg"
            >
              Grid View
            </button>
            <button
              onClick={() => router.push("/playback")}
              className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg"
            >
              Playback
            </button>
          </div>
        </div>
      </header>

      {/* Main Grid Content */}
      <main className="relative z-10 p-6 grid grid-cols-1 xl:grid-cols-12 gap-6 max-w-[1800px] mx-auto">
        
        {/* Left Side: Video Panel & Preserver (7 Columns) */}
        <section className="xl:col-span-7 flex flex-col gap-6">
          
          {/* Video Feed Card */}
          <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-4 shadow-2xl flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-900 pb-3">
              <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase flex items-center gap-2">
                <span className="w-2.5 h-2.5 bg-cyan-500 rounded-full animate-pulse"></span>
                {cameras.find(c => c.id === selectedCamera)?.name || selectedCamera}
              </h3>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span>FPS: <strong className="text-cyan-400 font-mono">{fps}</strong></span>
                <span>Frame: <strong className="text-slate-300 font-mono">{frameId}</strong></span>
                <span>Active ROIs: <strong className="text-amber-400 font-mono">{rois.length}</strong></span>
              </div>
            </div>

            {/* Video Canvas Container */}
            <div className="relative aspect-video w-full bg-[#030407] rounded-xl overflow-hidden border border-slate-800 shadow-inner group">
              <canvas
                ref={canvasRef}
                className="w-full h-full object-contain block"
              />
              
              {/* Dynamic State Overlay Indicator */}
              <div className="absolute top-4 left-4 pointer-events-none">
                <div className={`px-4 py-2 rounded-lg backdrop-blur-md font-mono text-sm font-bold shadow-lg border transition-all duration-300 ${
                  surveillance_state === "critical" ? "bg-red-950/80 text-red-400 border-red-500/50 shadow-red-500/10" :
                  surveillance_state === "alert" ? "bg-amber-950/80 text-amber-400 border-amber-500/50 shadow-amber-500/10" :
                  "bg-slate-950/80 text-emerald-400 border-slate-800"
                }`}>
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${
                      surveillance_state === "critical" ? "bg-red-500 animate-ping" :
                      surveillance_state === "alert" ? "bg-amber-500 animate-pulse" :
                      "bg-emerald-500"
                    }`} />
                    {surveillance_state.toUpperCase()} MODE // RISK: {risk.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Bottom HUD readout */}
              <div className="absolute bottom-4 left-4 right-4 pointer-events-none flex justify-between items-end">
                <div className="bg-slate-950/80 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-800 text-[10px] font-mono text-slate-500 leading-normal">
                  CAM: <span className="text-slate-300">{selectedCamera}</span><br/>
                  SYS.REF: <span className="text-slate-300">UDP://127.0.0.1</span><br/>
                  DEC: <span className="text-slate-300">YOLOv8N-INFERENCE</span><br/>
                  ENC: <span className="text-slate-300">{codec.toUpperCase()} @ {bitrate}kbps</span>
                </div>
                
                {surveillance_state === "critical" && (
                  <div className="bg-red-500 text-white font-mono text-[10px] font-bold px-3 py-1.5 rounded-lg border border-red-600 animate-bounce flex items-center gap-1.5 shadow-lg shadow-red-500/20">
                    <span className="w-1.5 h-1.5 bg-white rounded-full animate-ping" />
                    BUFFERING INCIDENT EVIDENCE
                  </div>
                )}
              </div>
            </div>

            {/* Micro Stats Bar */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 bg-slate-950/60 border border-slate-900/80 rounded-xl p-4 text-center">
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">Incoming Preview</p>
                <p className="text-xl font-bold font-mono text-slate-200 mt-1">{sioKbps.toFixed(1)} <span className="text-xs text-slate-500 font-normal">kbps</span></p>
              </div>
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">Compressed H.265 Stream</p>
                <p className="text-xl font-bold font-mono text-cyan-400 mt-1">{udpKbps.toFixed(1)} <span className="text-xs text-slate-500 font-normal">kbps</span></p>
              </div>
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">Net Bandwidth Saved</p>
                <p className="text-xl font-bold font-mono text-emerald-400 mt-1">{bandwidthSaved.toFixed(0)}%</p>
              </div>
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">Risk Score</p>
                <p className={`text-xl font-bold font-mono mt-1 ${
                  risk > 0.55 ? "text-red-400" : risk > 0.25 ? "text-amber-400" : "text-emerald-400"
                }`}>{risk.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">GOP Size</p>
                <p className={`text-xl font-bold font-mono mt-1 ${
                  gopSize <= 10 ? "text-red-400" : gopSize <= 30 ? "text-amber-400" : "text-purple-400"
                }`}>{gopSize} <span className="text-xs text-slate-500 font-normal">frames</span></p>
              </div>
              <div>
                <p className="text-[10px] text-slate-500 tracking-wider uppercase font-semibold">Resolution</p>
                <p className="text-xl font-bold font-mono text-slate-200 mt-1">{resW}×{resH}</p>
              </div>
            </div>
          </div>

          {/* Forensic Preserver Info */}
          <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-5 shadow-xl flex flex-col gap-4">
            <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase border-b border-slate-900 pb-2 flex items-center gap-2">
              📂 Incident Preserver Status
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">Rolling Pre-Event Buffer:</span>
                  <span className="font-mono text-cyan-400">15 Seconds (Active)</span>
                </div>
                <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
                  <div className="bg-gradient-to-r from-cyan-500 to-blue-500 h-full w-[100%] rounded-full animate-pulse" />
                </div>
                <p className="text-[10px] text-slate-500">Automatically stores 15 seconds of raw full-resolution frames in memory. When a critical event is triggered, this buffer is instantly written to the server storage directory as evidence.</p>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">Post-Event Active Recorder:</span>
                  <span className="font-mono text-red-400">10 Seconds (Trigger On-Demand)</span>
                </div>
                <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
                  <div className={`h-full rounded-full transition-all duration-300 ${
                    surveillance_state === "critical" ? "bg-red-500 w-[100%] animate-pulse" : "bg-slate-800 w-[0%]"
                  }`} />
                </div>
                <p className="text-[10px] text-slate-500">Saves the subsequent 10 seconds of full-resolution frames after the event trigger finishes. Ideal for complete forensic reconstructions of perimeter breaches or unauthorized entry.</p>
              </div>
            </div>
          </div>

        </section>

        {/* Middle Side: Control Center (5 Columns split in grid layout) */}
        <section className="xl:col-span-5 flex flex-col gap-6">
          
          {/* Controls Panel */}
          <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-5 shadow-2xl flex flex-col gap-5">
            <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase border-b border-slate-900 pb-2">
              ⚙️ Surveillance Control Center
            </h3>

            {/* Presets Grid */}
            <div>
              <p className="text-xs text-slate-500 mb-3 font-semibold uppercase tracking-wider">Operational Profiles</p>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => applyProfile("ultra_low", { bg_scale: 0.2, bg_quality: 5, roi_quality: 50, detect_every_n: 6, codec: "libx265", bitrate: 600 })}
                  className={`p-3 rounded-xl border text-left transition-all duration-200 ${
                    activeProfile === "ultra_low" 
                      ? "bg-purple-950/40 border-purple-500/50 text-white shadow-lg shadow-purple-500/10" 
                      : "bg-slate-900/40 border-slate-800/80 hover:border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <p className="text-xs font-bold font-mono">📡 ULTRA LOW BW</p>
                  <p className="text-[9px] text-slate-500 mt-1 leading-normal">Scale 0.2, Qual 5%, Det every 6f. Designed for remote satellite nodes.</p>
                </button>

                <button
                  onClick={() => applyProfile("balanced", { bg_scale: 0.5, bg_quality: 20, roi_quality: 85, detect_every_n: 3, codec: "libx265", bitrate: 1500 })}
                  className={`p-3 rounded-xl border text-left transition-all duration-200 ${
                    activeProfile === "balanced" 
                      ? "bg-cyan-950/40 border-cyan-500/50 text-white shadow-lg shadow-cyan-500/10" 
                      : "bg-slate-900/40 border-slate-800/80 hover:border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <p className="text-xs font-bold font-mono">⚖️ BALANCED MODE</p>
                  <p className="text-[9px] text-slate-500 mt-1 leading-normal">Scale 0.5, Qual 20%, Det every 3f. Standard campus gate operation.</p>
                </button>

                <button
                  onClick={() => applyProfile("high_quality", { bg_scale: 0.8, bg_quality: 45, roi_quality: 95, detect_every_n: 1, codec: "libx265", bitrate: 3500 })}
                  className={`p-3 rounded-xl border text-left transition-all duration-200 ${
                    activeProfile === "high_quality" 
                      ? "bg-emerald-950/40 border-emerald-500/50 text-white shadow-lg shadow-emerald-500/10" 
                      : "bg-slate-900/40 border-slate-800/80 hover:border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <p className="text-xs font-bold font-mono">💎 HIGH QUALITY</p>
                  <p className="text-[9px] text-slate-500 mt-1 leading-normal">Scale 0.8, Qual 45%, Det every 1f. Maximized forensic fidelity.</p>
                </button>

                <button
                  onClick={() => applyProfile("privacy", { bg_scale: 0.5, bg_quality: 20, roi_quality: 90, detect_every_n: 3, privacy_blur: true, mask_faces: true, codec: "libx265", bitrate: 1500 })}
                  className={`p-3 rounded-xl border text-left transition-all duration-200 ${
                    activeProfile === "privacy" 
                      ? "bg-rose-950/40 border-rose-500/50 text-white shadow-lg shadow-rose-500/10" 
                      : "bg-slate-900/40 border-slate-800/80 hover:border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <p className="text-xs font-bold font-mono">🔒 PRIVACY SHIELD</p>
                  <p className="text-[9px] text-slate-500 mt-1 leading-normal">Enables blur background & mask faces automatically.</p>
                </button>
              </div>
            </div>

            {/* Interactive Sliders */}
            <div className="space-y-4">
              <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider border-t border-slate-900 pt-3">Manual Override Parameters</p>
              
              {/* BG Scale */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">Background Scale:</span>
                  <span className="font-mono text-cyan-400 font-bold">{bgScale.toFixed(2)}</span>
                </div>
                <input
                  type="range" min="0.2" max="1.0" step="0.05" value={bgScale}
                  onChange={(e) => { setBgScale(parseFloat(e.target.value)); emitControl({ bg_scale: parseFloat(e.target.value) }); setActiveProfile("custom"); }}
                  className="w-full accent-cyan-500 bg-slate-900 rounded-lg appearance-none h-1.5 cursor-pointer"
                />
              </div>

              {/* BG Quality */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">Background Quality (QP/CRF):</span>
                  <span className="font-mono text-cyan-400 font-bold">{bgQuality}%</span>
                </div>
                <input
                  type="range" min="5" max="95" step="5" value={bgQuality}
                  onChange={(e) => { setBgQuality(parseInt(e.target.value)); emitControl({ bg_quality: parseInt(e.target.value) }); setActiveProfile("custom"); }}
                  className="w-full accent-cyan-500 bg-slate-900 rounded-lg appearance-none h-1.5 cursor-pointer"
                />
              </div>

              {/* ROI Quality */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">ROI Preservation Quality:</span>
                  <span className="font-mono text-cyan-400 font-bold">{roiQuality}%</span>
                </div>
                <input
                  type="range" min="40" max="100" step="5" value={roiQuality}
                  onChange={(e) => { setRoiQuality(parseInt(e.target.value)); emitControl({ roi_quality: parseInt(e.target.value) }); setActiveProfile("custom"); }}
                  className="w-full accent-cyan-500 bg-slate-900 rounded-lg appearance-none h-1.5 cursor-pointer"
                />
              </div>

              {/* Detect Every N */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">Detection Interval (Every N Frames):</span>
                  <span className="font-mono text-cyan-400 font-bold">{detectEveryN}f</span>
                </div>
                <input
                  type="range" min="1" max="15" step="1" value={detectEveryN}
                  onChange={(e) => { setDetectEveryN(parseInt(e.target.value)); emitControl({ detect_every_n: parseInt(e.target.value) }); setActiveProfile("custom"); }}
                  className="w-full accent-cyan-500 bg-slate-900 rounded-lg appearance-none h-1.5 cursor-pointer"
                />
              </div>
            </div>

            {/* Privacy Toggles */}
            <div className="space-y-3 border-t border-slate-900 pt-4">
              <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Ethical Compliance & Privacy</p>
              
              <div className="flex items-center justify-between bg-slate-900/30 p-2.5 rounded-xl border border-slate-900">
                <div>
                  <p className="text-xs font-semibold text-slate-300">Ethical Mode (Empty Background)</p>
                  <p className="text-[10px] text-slate-500">Transmits black screen unless an ROI is active</p>
                </div>
                <input
                  type="checkbox" checked={ethicalMode}
                  onChange={(e) => { setEthicalMode(e.target.checked); emitControl({ ethical_mode: e.target.checked }); }}
                  className="w-10 h-5 bg-slate-800 rounded-full appearance-none relative checked:bg-cyan-500 cursor-pointer transition-colors duration-200 before:content-[''] before:absolute before:w-4 before:h-4 before:bg-white before:rounded-full before:top-0.5 before:left-0.5 checked:before:translate-x-5 before:transition-transform before:duration-200"
                />
              </div>

              <div className="flex items-center justify-between bg-slate-900/30 p-2.5 rounded-xl border border-slate-900">
                <div>
                  <p className="text-xs font-semibold text-slate-300">Background Privacy Blur</p>
                  <p className="text-[10px] text-slate-500">Applies strong Gaussian blur to background pixels</p>
                </div>
                <input
                  type="checkbox" checked={privacyBlur}
                  onChange={(e) => { setPrivacyBlur(e.target.checked); emitControl({ privacy_blur: e.target.checked }); }}
                  className="w-10 h-5 bg-slate-800 rounded-full appearance-none relative checked:bg-cyan-500 cursor-pointer transition-colors duration-200 before:content-[''] before:absolute before:w-4 before:h-4 before:bg-white before:rounded-full before:top-0.5 before:left-0.5 checked:before:translate-x-5 before:transition-transform before:duration-200"
                />
              </div>

              <div className="flex items-center justify-between bg-slate-900/30 p-2.5 rounded-xl border border-slate-900">
                <div>
                  <p className="text-xs font-semibold text-slate-300">Mask Detected Faces/Persons</p>
                  <p className="text-[10px] text-slate-500">Anonymizes human subjects but keeps vehicles clear</p>
                </div>
                <input
                  type="checkbox" checked={maskFaces}
                  onChange={(e) => { setMaskFaces(e.target.checked); emitControl({ mask_faces: e.target.checked }); }}
                  className="w-10 h-5 bg-slate-800 rounded-full appearance-none relative checked:bg-cyan-500 cursor-pointer transition-colors duration-200 before:content-[''] before:absolute before:w-4 before:h-4 before:bg-white before:rounded-full before:top-0.5 before:left-0.5 checked:before:translate-x-5 before:transition-transform before:duration-200"
                />
              </div>
            </div>

            {/* Codec & Bitrate */}
            <div className="grid grid-cols-2 gap-4 border-t border-slate-900 pt-4">
              <div>
                <label className="block text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-1">Target Codec</label>
                <select
                  value={codec}
                  onChange={(e) => { setCodec(e.target.value); emitControl({ codec: e.target.value }); }}
                  className="w-full bg-slate-900 border border-slate-800 text-slate-300 text-xs rounded-lg p-2 focus:outline-none focus:border-cyan-500"
                >
                  <option value="libx264">H.264 (libx264)</option>
                  <option value="libx265">H.265 (libx265)</option>
                  <option value="hevc_nvenc">H.265 (Nvidia NVENC)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-1">Override Max Bitrate</label>
                <select
                  value={bitrate}
                  onChange={(e) => { setBitrate(parseInt(e.target.value)); emitControl({ bitrate: parseInt(e.target.value) }); }}
                  className="w-full bg-slate-900 border border-slate-800 text-slate-300 text-xs rounded-lg p-2 focus:outline-none focus:border-cyan-500"
                >
                  <option value={500}>500 kbps (GPRS)</option>
                  <option value={1000}>1000 kbps (3G)</option>
                  <option value={2000}>2000 kbps (LTE)</option>
                  <option value={4000}>4000 kbps (Broadband)</option>
                  <option value={8000}>8000 kbps (LAN)</option>
                </select>
              </div>
            </div>

          </div>

          {/* Analytics Line Chart */}
          <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-5 shadow-2xl flex flex-col gap-3">
            <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase flex items-center justify-between">
              <span>📡 Bandwidth Performance</span>
              <span className="text-xs text-cyan-400 font-mono">Live Metrics</span>
            </h3>
            
            <div className="h-[200px] w-full mt-2 min-w-0">
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <AreaChart data={metricsHistory}>
                  <defs>
                    <linearGradient id="colorUdp" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="colorSio" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#c084fc" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#c084fc" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" opacity={0.3} />
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={10} />
                  <Tooltip contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155", color: "#f8fafc" }} />
                  <Area type="monotone" dataKey="sio" stroke="#c084fc" strokeWidth={2} fillOpacity={1} fill="url(#colorSio)" name="Raw Preview (kbps)" isAnimationActive={false} />
                  <Area type="monotone" dataKey="udp" stroke="#22d3ee" strokeWidth={2} fillOpacity={1} fill="url(#colorUdp)" name="Compressed Stream (kbps)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Incident Log Terminal */}
          <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-5 shadow-2xl flex flex-col gap-3">
            <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase flex items-center justify-between border-b border-slate-900 pb-2">
              <span>🚨 Live Incident Terminal</span>
              <span className="text-[10px] bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-0.5 rounded font-mono animate-pulse">RECORDING EVENTS</span>
            </h3>

            <div className="h-[210px] overflow-y-auto font-mono text-[11px] space-y-2.5 pr-2 custom-scrollbar">
              {eventLog.length === 0 ? (
                <p className="text-slate-600 italic text-center py-8">Waiting for event triggers...</p>
              ) : (
                eventLog.map((evt, idx) => (
                  <div key={evt.eventId || idx} className="bg-slate-900/40 border border-slate-900 p-2.5 rounded-lg flex flex-col gap-1.5">
                    <div className="flex justify-between items-center">
                      <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase ${
                        evt.state === "critical" ? "bg-red-500/20 text-red-400 border border-red-500/30" : "bg-amber-500/20 text-amber-400 border border-amber-500/30"
                      }`}>
                        {evt.state}
                      </span>
                      <span className="text-slate-500">{new Date(evt.ts || Date.now()).toLocaleTimeString()}</span>
                    </div>

                    <div className="grid grid-cols-2 gap-x-4 text-slate-400">
                      <div>Event ID: <span className="text-slate-200">{evt.eventId}</span></div>
                      <div>Risk Score: <span className="text-red-400 font-bold">{parseFloat(evt.risk).toFixed(2)}</span></div>
                      <div>Frame ID: <span className="text-slate-300">{evt.frameId}</span></div>
                      <div>Objects: <span className="text-slate-300">{evt.numRois} detected</span></div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

        </section>

      </main>

      <footer className="border-t border-slate-900 bg-slate-950/60 py-6 text-center text-xs text-slate-600 relative z-10">
        <p>© 2026 NEXUS SURVEILLANCE LABS // CSE DEPARTMENT. ALL RIGHTS RESERVED.</p>
        <p className="mt-1 text-slate-700">Optimizing low-bandwidth nodes with risk-aware evidence preservation algorithms.</p>
      </footer>
    </div>
  );
}
