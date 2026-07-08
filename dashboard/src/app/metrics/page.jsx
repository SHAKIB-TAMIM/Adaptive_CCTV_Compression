"use client";
import { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";

export default function MetricsPage() {
  const [metrics, setMetrics] = useState([]);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch("http://localhost:5000/metrics");
        const data = await res.json();
        setMetrics(data.slice(-50)); // Show last 50 records
      } catch (err) {
        console.error("Error fetching metrics:", err);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 3000); // auto-refresh
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="p-6 bg-gray-100 min-h-screen">
      <h1 className="text-3xl font-bold text-center mb-6  text-gray-800">📊 Live Compression Metrics Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Bitrate */}
        <div className="bg-white p-4 rounded-xl shadow">
          <h2 className="text-lg font-semibold mb-2 text-center text-gray-800">Bitrate (kbps)</h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={metrics}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="timestamp" hide />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="bitrate" stroke="#1f77b4" name="Bitrate" />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* PSNR */}
        <div className="bg-white p-4 rounded-xl shadow">
          <h2 className="text-lg font-semibold mb-2 text-center  text-gray-800">PSNR (dB)</h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={metrics}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="timestamp" hide />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="psnr" stroke="#ff7f0e" name="PSNR" />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* SSIM */}
        <div className="bg-white p-4 rounded-xl shadow">
          <h2 className="text-lg font-semibold mb-2 text-center  text-gray-800">SSIM</h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={metrics}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="timestamp" hide />
              <YAxis domain={[0.8, 1]} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="ssim" stroke="#2ca02c" name="SSIM" />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Client Count */}
        <div className="bg-white p-4 rounded-xl shadow">
          <h2 className="text-lg font-semibold mb-2 text-center  text-gray-800">Active Clients</h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={metrics}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="timestamp" hide />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="total_clients" stroke="#d62728" name="Clients" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
