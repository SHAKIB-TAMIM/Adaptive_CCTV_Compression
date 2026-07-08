"use client";

import { useEffect, useRef, useState } from "react";
import { io } from "socket.io-client";

export default function VideoCanvas() {
  const canvasRef = useRef(null);
  const socketRef = useRef(null);
  const [stats, setStats] = useState({ frame: 0, fps: 0 });
  const lastTimeRef = useRef(Date.now());

  useEffect(() => {
    // Connect to the backend's "view" namespace
    socketRef.current = io("http://127.0.0.1:5000/view");

    socketRef.current.on("connect", () => {
      console.log(" Connected to server:", socketRef.current.id);
    });

    socketRef.current.on("frame", (msg) => {
      handleFrame(msg);
    });

    socketRef.current.on("disconnect", () => {
      console.warn(" Disconnected from server");
    });

    return () => socketRef.current.disconnect();
  }, []);

  function handleFrame(msg) {
    const now = Date.now();
    const dt = now - lastTimeRef.current;
    lastTimeRef.current = now;
    const fps = 1000 / (dt || 1);
    setStats({ frame: msg.frame_id, fps: Math.round(fps) });
    renderFrame(msg);
  }

  // function renderFrame(msg) {
  //   const canvas = canvasRef.current;
  //   if (!canvas) return;

  //   const ctx = canvas.getContext("2d");
  //   const bgImg = new Image();

  //   bgImg.onload = () => {
  //     canvas.width = msg.orig_w || bgImg.width;
  //     canvas.height = msg.orig_h || bgImg.height;

  //     // Draw the compressed background
  //     ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);

  //     // Draw the compressed ROIs
  //     if (Array.isArray(msg.rois)) {
  //       msg.rois.forEach((roi) => {
  //         const [x1, y1, x2, y2] = roi.bbox;
  //         const roiImg = new Image();
  //         roiImg.onload = () => {
  //           ctx.drawImage(
  //             roiImg,
  //             0,
  //             0,
  //             roiImg.width,
  //             roiImg.height,
  //             x1,
  //             y1,
  //             x2 - x1,
  //             y2 - y1
  //           );
  //         };
  //         roiImg.src = "data:image/jpeg;base64," + roi.data;
  //       });
  //     }
  //   };

  //   bgImg.src = "data:image/jpeg;base64," + msg.bg_data;
  // }
    function renderFrame(msg) {
    if (!msg.vis_frame) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const img = new Image();

    img.onload = () => {
      canvas.width = img.width;
      canvas.height = img.height;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);
    };

    img.src = "data:image/jpeg;base64," + msg.vis_frame;
  }



  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ marginBottom: "10px" }}>
        <strong>Frame:</strong> {stats.frame} &nbsp; | &nbsp;
        <strong>FPS:</strong> {stats.fps}
      </div>
      <canvas
        ref={canvasRef}
        width={640}
        height={480}
        style={{
          border: "2px solid #333",
          borderRadius: "10px",
          boxShadow: "0 0 8px rgba(0,0,0,0.3)",
        }}
      />
    </div>
  );
}
