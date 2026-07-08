"use client";

import { useEffect, useState, useRef } from "react";
import { io } from "socket.io-client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

const SERVER_VIEW_NS = "http://127.0.0.1:5000/view";

export default function MetricsDashboard() {
  const socketRef = useRef(null);
  const [data, setData] = useState([]);
  const [motionData, setMotionData] = useState([]);

  useEffect(() => {
    socketRef.current = io(SERVER_VIEW_NS);

    socketRef.current.on("metrics", (msg) => {
      setData((prev) => {
        const newData = [...prev, {
          time: new Date(msg.timestamp).toLocaleTimeString(),
          kbps: parseFloat(msg.total_kbps),
          bitrate: parseFloat(msg.bitrate),
          psnr: parseFloat(msg.psnr)
        }];
        if (newData.length > 20) {
          newData.shift(); // keep last 20 points
        }
        return newData;
      });
    });

    socketRef.current.on("motion", (msg) => {
      setMotionData((prev) => {
        const newData = [...prev, {
          time: new Date(msg.timestamp).toLocaleTimeString(),
          motion: parseFloat(msg.motion_percent),
          rois: parseInt(msg.num_rois)
        }];
        if (newData.length > 20) newData.shift();
        return newData;
      });
    });

    return () => socketRef.current.disconnect();
  }, []);

  return (
    <div style={{ width: "100%", height: "350px", marginTop: "20px", display: "flex", flexDirection: "column" }}>
      <h3 style={{ marginBottom: "10px", color: "#ddd", flexShrink: 0 }}>Live Bandwidth (kbps) & PSNR</h3>
      <div style={{ flex: 1, minHeight: 0, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
            <XAxis dataKey="time" stroke="#ccc" tick={{fill: '#ccc'}} />
            <YAxis yAxisId="left" stroke="#8884d8" />
            <YAxis yAxisId="right" orientation="right" stroke="#82ca9d" />
            <Tooltip contentStyle={{ backgroundColor: '#222', border: '1px solid #444', color: '#fff' }} />
            <Legend />
            <Line yAxisId="left" type="monotone" dataKey="kbps" stroke="#8884d8" name="Incoming Kbps" isAnimationActive={false} />
            <Line yAxisId="left" type="monotone" dataKey="bitrate" stroke="#ffc658" name="Target Bitrate" isAnimationActive={false} />
            <Line yAxisId="right" type="monotone" dataKey="psnr" stroke="#82ca9d" name="PSNR (dB)" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <h3 style={{ marginBottom: "10px", marginTop: "30px", color: "#ddd", flexShrink: 0 }}>Scene Activity (Motion & ROIs)</h3>
      <div style={{ flex: 1, minHeight: "250px", width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={motionData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
            <XAxis dataKey="time" stroke="#ccc" tick={{fill: '#ccc'}} />
            <YAxis yAxisId="left" stroke="#ff7300" />
            <YAxis yAxisId="right" orientation="right" stroke="#387908" />
            <Tooltip contentStyle={{ backgroundColor: '#222', border: '1px solid #444', color: '#fff' }} />
            <Legend />
            <Line yAxisId="left" type="step" dataKey="motion" stroke="#ff7300" name="Motion Intensity" isAnimationActive={false} />
            <Line yAxisId="right" type="step" dataKey="rois" stroke="#387908" name="Object Count" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
