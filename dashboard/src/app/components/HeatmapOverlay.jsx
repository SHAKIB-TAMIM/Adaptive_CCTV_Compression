"use client";

import { useEffect, useRef, useState, useCallback } from "react";

const API_URL = "http://localhost:5000";
const HEATMAP_REFRESH_MS = 2000; // Reduced polling frequency (was 1000)
// Grid cell size for density accumulation (smaller = finer, larger = faster)
const GRID_CELL_SIZE = 16;
// Alpha blending lerp factor for temporal smoothing (0-1, lower = smoother)
const SMOOTHING_FACTOR = 0.35;

// Neon palette stops for heatmap visualization
function heatmapColor(value) {
  // value: 0.0 to 1.0
  let r, g, b;
  if (value < 0.15) {
    // Dim blue (low activity)
    const t = value / 0.15;
    r = Math.round(8 * t);
    g = Math.round(40 * t);
    b = Math.round(120 + 80 * t);
  } else if (value < 0.4) {
    // Blue -> Cyan
    const t = (value - 0.15) / 0.25;
    r = Math.round(8 + 50 * t);
    g = Math.round(40 + 180 * t);
    b = Math.round(200 - 40 * t);
  } else if (value < 0.6) {
    // Cyan -> Yellow
    const t = (value - 0.4) / 0.2;
    r = Math.round(58 + 197 * t);
    g = Math.round(220 + 35 * t);
    b = Math.round(160 - 150 * t);
  } else if (value < 0.82) {
    // Yellow -> Orange-Red
    const t = (value - 0.6) / 0.22;
    r = Math.round(255);
    g = Math.round(255 - 180 * t);
    b = Math.round(10 - 10 * t);
  } else {
    // Orange-Red -> Magenta (max activity)
    const t = (value - 0.82) / 0.18;
    r = Math.round(255);
    g = Math.round(75 - 60 * t);
    b = Math.round(80 + 175 * t);
  }
  return { r: Math.max(0, Math.min(255, r)), g: Math.max(0, Math.min(255, g)), b: Math.max(0, Math.min(255, b)) };
}

export default function HeatmapOverlay({ cameraId, visible, width, height }) {
  const canvasRef = useRef(null);
  const legendCanvasRef = useRef(null);
  const [points, setPoints] = useState([]);
  const animRef = useRef(null);
  const smoothedGridRef = useRef(null); // Persistent smoothed grid between renders
  const prevPointsRef = useRef([]);

  // Fetch heatmap data from server
  useEffect(() => {
    if (!visible || !cameraId) return;
    const fetchHeatmap = async () => {
      try {
        const res = await fetch(`${API_URL}/heatmap/${cameraId}`);
        const data = await res.json();
        if (Array.isArray(data)) setPoints(data);
      } catch (e) { /* silent */ }
    };
    fetchHeatmap();
    const interval = setInterval(fetchHeatmap, HEATMAP_REFRESH_MS);
    return () => clearInterval(interval);
  }, [cameraId, visible]);

  // Match canvas pixel dimensions
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const w = Number(width) || 640;
    const h = Number(height) || 480;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      smoothedGridRef.current = null; // Reset smoothing on resize
    }
  }, [width, height]);

  // Derive grid dimensions
  const getGridDims = useCallback((w, h) => ({
    cols: Math.ceil(w / GRID_CELL_SIZE),
    rows: Math.ceil(h / GRID_CELL_SIZE),
  }), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) {
      if (animRef.current) cancelAnimationFrame(animRef.current);
      return;
    }
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    if (w === 0 || h === 0) return;
    const { cols, rows } = getGridDims(w, h);

    const render = () => {
      ctx.clearRect(0, 0, w, h);

      if (points.length === 0) {
        prevPointsRef.current = [];
        animRef.current = requestAnimationFrame(render);
        return;
      }

      // Build current density grid from latest server points
      const currentGrid = new Float32Array(cols * rows);
      for (const p of points) {
        const weight = p.weight ?? 1;
        if (weight < 0.01) continue;
        const gx = Math.min(cols - 1, Math.max(0, Math.floor(p.x * cols)));
        const gy = Math.min(rows - 1, Math.max(0, Math.floor(p.y * rows)));
        currentGrid[gy * cols + gx] += weight;
      }

      // Temporal smoothing: lerp between previous and current grid
      if (!smoothedGridRef.current || smoothedGridRef.current.length !== cols * rows) {
        smoothedGridRef.current = new Float32Array(currentGrid);
      } else {
        const sg = smoothedGridRef.current;
        for (let i = 0; i < sg.length; i++) {
          sg[i] = sg[i] * (1 - SMOOTHING_FACTOR) + currentGrid[i] * SMOOTHING_FACTOR;
        }
      }
      const grid = smoothedGridRef.current;

      // Normalize grid to [0, 1]
      let maxVal = 0;
      for (let i = 0; i < grid.length; i++) {
        if (grid[i] > maxVal) maxVal = grid[i];
      }
      maxVal = Math.max(1, maxVal);

      // Spatial interpolation: render at sub-cell resolution for smooth appearance
      // We draw each cell as a smooth radial gradient using neighbor-aware values
      const cellW = w / cols;
      const cellH = h / rows;

      for (let gy = 0; gy < rows; gy++) {
        for (let gx = 0; gx < cols; gx++) {
          const raw = grid[gy * cols + gx];
          const v = raw / maxVal;
          if (v < 0.02) continue;

          const cx = gx * cellW + cellW / 2;
          const cy = gy * cellH + cellH / 2;

          // Smooth radius based on cell size and intensity
          const radius = cellW * 1.6 + v * cellW * 0.6;

          const { r, g, b } = heatmapColor(v);
          const alpha = Math.min(0.82, 0.12 + v * 0.7);

          ctx.save();
          ctx.shadowColor = `rgba(${r},${g},${b},0.5)`;
          ctx.shadowBlur = 14 + v * 12;

          const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
          grad.addColorStop(0, `rgba(${r},${g},${b},${alpha.toFixed(3)})`);
          grad.addColorStop(0.35, `rgba(${r},${g},${b},${(alpha * 0.5).toFixed(3)})`);
          grad.addColorStop(0.7, `rgba(${r},${g},${b},${(alpha * 0.15).toFixed(3)})`);
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
  }, [points, visible, width, height, getGridDims]);

  // Draw legend on separate canvas
  useEffect(() => {
    const legendCanvas = legendCanvasRef.current;
    if (!legendCanvas || !visible) return;
    const lctx = legendCanvas.getContext("2d");
    const lw = legendCanvas.width;
    const lh = legendCanvas.height;

    lctx.clearRect(0, 0, lw, lh);

    // Background
    lctx.fillStyle = "rgba(3, 4, 7, 0.6)";
    lctx.beginPath();
    lctx.roundRect(0, 0, lw, lh, 6);
    lctx.fill();

    // Title
    lctx.fillStyle = "rgba(148, 163, 184, 0.8)";
    lctx.font = "9px monospace";
    lctx.fillText("ACTIVITY", 8, 14);

    // Color gradient bar
    const barX = 8;
    const barY = 20;
    const barW = lw - 16;
    const barH = 8;
    const gradStops = 80;
    for (let i = 0; i < gradStops; i++) {
      const t = i / gradStops;
      const { r, g, b } = heatmapColor(t);
      lctx.fillStyle = `rgb(${r},${g},${b})`;
      lctx.fillRect(barX + (t * barW), barY, barW / gradStops + 1, barH);
    }

    // Labels
    lctx.fillStyle = "rgba(148, 163, 184, 0.6)";
    lctx.font = "7px monospace";
    lctx.fillText("Low", barX, barY + barH + 11);
    lctx.fillText("High", barX + barW - 20, barY + barH + 11);

    // Point count
    lctx.fillStyle = "rgba(148, 163, 184, 0.4)";
    lctx.font = "7px monospace";
    lctx.fillText(`${points.length} pts`, barX, barY + barH + 22);
  }, [points, visible]);

  if (!visible) return null;

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 pointer-events-none"
        style={{ width: "100%", height: "100%" }}
      />
      {/* Small legend in bottom-right corner */}
      <div className="absolute bottom-3 right-3 pointer-events-none z-30">
        <canvas
          ref={legendCanvasRef}
          width={110}
          height={50}
          className="rounded-md"
        />
      </div>
    </>
  );
}
