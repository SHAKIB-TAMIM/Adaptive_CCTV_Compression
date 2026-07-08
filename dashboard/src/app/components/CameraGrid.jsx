"use client";

import { useEffect, useRef, useState } from "react";
import { io } from "socket.io-client";

const VIEW_NS = "http://localhost:5000/view";
const CAMERAS_URL = "http://localhost:5000/cameras";

export default function CameraGrid({ onSelectCamera }) {
  const socketRef = useRef(null);
  const canvasRefs = useRef({});
  const [cameras, setCameras] = useState([]);
  const [frameData, setFrameData] = useState({});
  const lastFrameTimes = useRef({});

  useEffect(() => {
    const socket = io(VIEW_NS);
    socketRef.current = socket;

    socket.on("frame", (msg) => {
      const camId = msg.camera_id || "camera_0";
      setFrameData((prev) => ({ ...prev, [camId]: msg }));
    });

    fetch(CAMERAS_URL)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) setCameras(data);
      })
      .catch(() => {});

    const camInterval = setInterval(() => {
      fetch(CAMERAS_URL)
        .then((r) => r.json())
        .then((data) => {
          if (Array.isArray(data)) setCameras(data);
        })
        .catch(() => {});
    }, 5000);

    return () => {
      socket.disconnect();
      clearInterval(camInterval);
    };
  }, []);

  // Render frames to canvases
  useEffect(() => {
    for (const camId of Object.keys(frameData)) {
      const canvas = canvasRefs.current[camId];
      if (!canvas) continue;
      const msg = frameData[camId];
      const ctx = canvas.getContext("2d");
      const img = new Image();
      img.onload = () => {
        canvas.width = msg.orig_w || img.width;
        canvas.height = msg.orig_h || img.height;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      };
      img.src = `data:image/jpeg;base64,${msg.vis_frame}`;
    }
  }, [frameData]);

  if (cameras.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 bg-slate-950/40 border border-slate-900 rounded-2xl">
        <p className="text-slate-500 text-sm">No cameras registered. Add cameras via POST /cameras API.</p>
      </div>
    );
  }

  let gridCols = "md:grid-cols-1";
  if (cameras.length <= 2) gridCols = "md:grid-cols-1";
  else if (cameras.length <= 4) gridCols = "md:grid-cols-2";
  else gridCols = "md:grid-cols-3";

  return (
    <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-4 shadow-2xl">
      <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase mb-4 flex items-center gap-2">
        <span className="w-2.5 h-2.5 bg-cyan-500 rounded-full animate-pulse"></span>
        Multi-Camera Grid ({cameras.length} online)
      </h3>
      <div className={`grid grid-cols-1 ${gridCols} gap-4`}>
        {cameras.map((cam) => {
          const statusColor =
            cam.status === "online"
              ? "bg-emerald-500"
              : cam.status === "registered"
              ? "bg-amber-500"
              : "bg-red-500";
          return (
            <div
              key={cam.id}
              className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden cursor-pointer hover:border-cyan-500/50 transition-colors"
              onClick={() => onSelectCamera && onSelectCamera(cam.id)}
            >
              <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${statusColor}`}></span>
                  <span className="text-xs font-semibold text-slate-300">{cam.name || cam.id}</span>
                </div>
                <span className="text-[10px] text-slate-500 font-mono">{cam.source}</span>
              </div>
              <div className="relative aspect-video bg-[#030407]">
                <canvas
                  ref={(el) => { if (el) canvasRefs.current[cam.id] = el; }}
                  className="w-full h-full object-contain"
                />
                <div className="absolute top-2 right-2">
                  <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase ${
                    cam.status === "online"
                      ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                      : "bg-red-500/20 text-red-400 border border-red-500/30"
                  }`}>
                    {cam.status}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
