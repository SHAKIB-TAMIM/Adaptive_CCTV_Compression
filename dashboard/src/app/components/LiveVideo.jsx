"use client";

import { useEffect, useRef, useState } from "react";
import { io } from "socket.io-client";

const SERVER_VIEW_NS = "http://127.0.0.1:5000/view";

export default function LiveVideo({ mode = "monitor" }) {
  const canvasRef = useRef(null);
  const socketRef = useRef(null);
  const lastTimeRef = useRef(Date.now());
  const [fps, setFps] = useState(0);

  useEffect(() => {
    socketRef.current = io(SERVER_VIEW_NS);

    socketRef.current.on("connect", () => {
      console.log("[LiveVideo] connected to view namespace");
    });

    socketRef.current.on("frame", (msg) => {
      drawFrame(msg);

      const now = Date.now();
      const dt = now - lastTimeRef.current;
      setFps((1000 / (dt || 1)).toFixed(1));
      lastTimeRef.current = now;
    });

    socketRef.current.on("disconnect", () => {
      console.warn("[LiveVideo] disconnected");
    });

    return () => socketRef.current.disconnect();
  }, []);

  function drawFrame(msg) {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const img = new Image();

    // ============================
    // MODE 1 — LIVE MONITOR
    // ============================
    if (mode === "monitor" && msg.vis_frame) {
      img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
      };
      img.src = `data:image/jpeg;base64,${msg.vis_frame}`;
      return;
    }

    // ============================
    // MODE 2 — COMPRESSION ANALYSIS
    // ============================
    img.onload = () => {
      const w = msg.orig_w || img.width;
      const h = msg.orig_h || img.height;

      canvas.width = w;
      canvas.height = h;

      // draw compressed background
      ctx.drawImage(img, 0, 0, w, h);

      // draw ROI boxes
      ctx.strokeStyle = "red";
      ctx.lineWidth = 2;
      (msg.rois || []).forEach((r) => {
        const [x1, y1, x2, y2] = r.bbox;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      });
    };

    img.src = `data:image/jpeg;base64,${msg.bg_data}`;
  }

  return (
    <div>
      <canvas
        ref={canvasRef}
        style={{
          width: "100%",
          border: "2px solid #222",
          borderRadius: "6px",
        }}
      />
      <div style={{ marginTop: 6 }}>
        <strong>FPS:</strong> {fps}
      </div>
    </div>
  );
}
