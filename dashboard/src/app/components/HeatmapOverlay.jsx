"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = "http://localhost:5000";
const HEATMAP_REFRESH_MS = 1000;

export default function HeatmapOverlay({ cameraId, visible, width, height }) {
  const canvasRef = useRef(null);
  const [points, setPoints] = useState([]);
  const animRef = useRef(null);

  useEffect(() => {
    if (!visible || !cameraId) return;
    const fetchHeatmap = async () => {
      try {
        const res = await fetch(`${API_URL}/heatmap/${cameraId}`);
        const data = await res.json();
        if (Array.isArray(data)) setPoints(data);
      } catch (e) {}
    };
    fetchHeatmap();
    const interval = setInterval(fetchHeatmap, HEATMAP_REFRESH_MS);
    return () => clearInterval(interval);
  }, [cameraId, visible]);

  // Match canvas pixel dimensions to stream resolution so normalized 0-1 coords align
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const w = Number(width) || 640;
    const h = Number(height) || 480;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
  }, [width, height]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible || points.length === 0) return;
    const ctx = canvas.getContext("2d");

    const w = canvas.width;
    const h = canvas.height;

    const render = () => {
      ctx.clearRect(0, 0, w, h);

      if (points.length === 0) return;

      // Density grid
      const SIZE = 20;
      const cols = Math.ceil(w / SIZE);
      const rows = Math.ceil(h / SIZE);
      const grid = new Float32Array(cols * rows);

      for (const p of points) {
        const gx = Math.min(cols - 1, Math.max(0, Math.floor(p.x * cols)));
        const gy = Math.min(rows - 1, Math.max(0, Math.floor(p.y * rows)));
        grid[gy * cols + gx] += p.weight || 1;
      }

      const maxVal = Math.max(1, ...grid);

      for (let gy = 0; gy < rows; gy++) {
        for (let gx = 0; gx < cols; gx++) {
          const v = grid[gy * cols + gx] / maxVal;
          if (v < 0.02) continue;

          const cx = gx * SIZE + SIZE / 2;
          const cy = gy * SIZE + SIZE / 2;
          const radius = SIZE * 1.6;

          // Neon cyan-yellow-magenta palette visible on dark feeds
          const alpha = Math.min(0.85, 0.15 + v * 0.7);
          let r, g, b;
          if (v < 0.4) {
            r = 0; g = Math.round(255 * (v / 0.4)); b = Math.round(255);
          } else if (v < 0.7) {
            const t = (v - 0.4) / 0.3;
            r = Math.round(255 * t); g = 255; b = Math.round(255 * (1 - t));
          } else {
            const t = (v - 0.7) / 0.3;
            r = 255; g = Math.round(255 * (1 - t)); b = Math.round(80 * (1 - t));
          }

          ctx.save();
          ctx.shadowColor = `rgba(${r},${g},${b},0.6)`;
          ctx.shadowBlur = 18;

          const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
          grad.addColorStop(0, `rgba(${r},${g},${b},${alpha})`);
          grad.addColorStop(0.4, `rgba(${r},${g},${b},${(alpha * 0.45).toFixed(3)})`);
          grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      }

      animRef.current = requestAnimationFrame(render);
    };

    render();
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [points, visible, width, height]);

  if (!visible) return null;

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none"
      style={{ width: "100%", height: "100%" }}
    />
  );
}
