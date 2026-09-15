"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const SERVER_URL = process.env.NEXT_PUBLIC_SERVER_URL || "http://localhost:5000";

const EMPTY_CAM = { id: "", name: "", source: "", udp_port: 1234, fps: 15, enabled: true };

export default function ConfigPage() {
  const [cameras, setCameras] = useState([]);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ ...EMPTY_CAM });
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchCameras = async () => {
    try {
      const res = await fetch(`${SERVER_URL}/cameras`);
      const data = await res.json();
      setCameras(data);
    } catch {
      setMsg("Cannot reach server. Make sure it is running.");
    }
  };

  useEffect(() => {
    fetchCameras();
    const interval = setInterval(fetchCameras, 3000);
    return () => clearInterval(interval);
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.id || !form.source) {
      setMsg("Camera ID and Source are required");
      return;
    }
    setLoading(true);
    try {
      const method = editing ? "PUT" : "POST";
      const url = editing ? `${SERVER_URL}/cameras/${editing}` : `${SERVER_URL}/cameras`;
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (data.success) {
        setMsg(editing ? "Camera updated!" : "Camera added!");
        setEditing(null);
        setForm({ ...EMPTY_CAM });
        fetchCameras();
      } else {
        setMsg(data.error || "Failed");
      }
    } catch (e) {
      setMsg("Error: " + e.message);
    }
    setLoading(false);
  };

  const remove = async (id) => {
    if (!confirm(`Delete camera "${id}"?`)) return;
    try {
      await fetch(`${SERVER_URL}/cameras/${id}`, { method: "DELETE" });
      setMsg("Camera deleted");
      fetchCameras();
    } catch (e) {
      setMsg("Error: " + e.message);
    }
  };

  const saveToYaml = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${SERVER_URL}/cameras/save`, { method: "POST" });
      const data = await res.json();
      if (data.success) {
        setMsg(`Saved ${data.saved} cameras to cameras.yaml`);
      } else {
        setMsg(data.error || "Failed to save");
      }
    } catch (e) {
      setMsg("Error: " + e.message);
    }
    setLoading(false);
  };

  const startEdit = (cam) => {
    setEditing(cam.id);
    setForm({ ...EMPTY_CAM, ...cam, fps: cam.fps || 15, udp_port: cam.udp_port || 1234 });
  };

  const presets = [
    { label: "USB Webcam", source: "0", hint: "Local camera (device index 0)" },
    { label: "IP Webcam (Phone)", source: "http://192.168.1.50:8080/video", hint: "Android IP Webcam app" },
    { label: "RTSP Camera", source: "rtsp://admin:password@192.168.1.100:554/stream1", hint: "Office/security camera" },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-white p-6">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-cyan-400">Camera Configuration</h1>
          <p className="text-slate-500 text-sm mt-1">Add, edit, or remove cameras. No code editing needed.</p>
        </div>

        {/* Message */}
        {msg && (
          <div className="mb-4 p-3 bg-cyan-500/10 border border-cyan-500/30 rounded text-cyan-300 text-sm flex justify-between">
            <span>{msg}</span>
            <button onClick={() => setMsg("")} className="text-slate-500 hover:text-white">x</button>
          </div>
        )}

        {/* Existing Cameras */}
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-slate-300 mb-3">Active Cameras</h2>
          {cameras.length === 0 ? (
            <p className="text-slate-600 text-sm">No cameras configured. Add one below.</p>
          ) : (
            <div className="space-y-2">
              {cameras.map((cam) => (
                <div key={cam.id} className="flex items-center justify-between bg-slate-900 border border-slate-800 rounded-lg p-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${cam.status === "online" ? "bg-emerald-400" : "bg-slate-600"}`} />
                      <span className="font-semibold text-white">{cam.name || cam.id}</span>
                      <span className="text-xs text-slate-500 font-mono">{cam.id}</span>
                    </div>
                    <div className="text-xs text-slate-500 mt-1 ml-4 font-mono">{cam.source}</div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => startEdit(cam)} className="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1 rounded">Edit</button>
                    <button onClick={() => remove(cam.id)} className="text-xs bg-red-900/30 hover:bg-red-900/50 text-red-400 px-3 py-1 rounded">Delete</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Add / Edit Form */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <h2 className="text-lg font-semibold text-slate-300 mb-4">
            {editing ? `Edit Camera: ${editing}` : "Add New Camera"}
          </h2>

          {/* Quick Presets */}
          {!editing && (
            <div className="mb-4">
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Quick Preset</label>
              <div className="flex gap-2">
                {presets.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => set("source", p.source)}
                    className="text-xs bg-slate-800 hover:bg-cyan-900/30 border border-slate-700 hover:border-cyan-500/50 text-slate-400 hover:text-cyan-300 px-3 py-2 rounded transition-colors"
                    title={p.hint}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-1">Camera ID *</label>
              <input
                value={form.id}
                onChange={(e) => set("id", e.target.value)}
                placeholder="e.g. office_main"
                disabled={!!editing}
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white placeholder-slate-600 focus:border-cyan-500 focus:outline-none disabled:opacity-50"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-1">Display Name</label>
              <input
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="e.g. Main Office"
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white placeholder-slate-600 focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <div className="col-span-2">
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-1">Camera Source *</label>
              <input
                value={form.source}
                onChange={(e) => set("source", e.target.value)}
                placeholder="0 (webcam) or rtsp://... or http://.../video"
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white placeholder-slate-600 focus:border-cyan-500 focus:outline-none font-mono"
              />
              <p className="text-xs text-slate-600 mt-1">
                USB webcam: <code className="text-slate-400">0</code> &nbsp;|&nbsp;
                RTSP: <code className="text-slate-400">rtsp://user:pass@ip:port/path</code> &nbsp;|&nbsp;
                Phone: <code className="text-slate-400">http://phone-ip:8080/video</code>
              </p>
            </div>
            <div>
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-1">UDP Port</label>
              <input
                type="number"
                value={form.udp_port}
                onChange={(e) => set("udp_port", parseInt(e.target.value) || 1234)}
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-1">Frame Rate (FPS)</label>
              <input
                type="number"
                value={form.fps}
                onChange={(e) => set("fps", parseInt(e.target.value) || 15)}
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-cyan-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex gap-3 mt-5">
            <button
              onClick={save}
              disabled={loading}
              className="bg-cyan-600 hover:bg-cyan-500 text-white px-5 py-2 rounded font-semibold text-sm disabled:opacity-50"
            >
              {loading ? "Saving..." : editing ? "Update Camera" : "Add Camera"}
            </button>
            {editing && (
              <button
                onClick={() => { setEditing(null); setForm({ ...EMPTY_CAM }); }}
                className="bg-slate-800 hover:bg-slate-700 text-slate-300 px-5 py-2 rounded text-sm"
              >
                Cancel
              </button>
            )}
            <button
              onClick={saveToYaml}
              disabled={loading}
              className="ml-auto bg-emerald-900/30 hover:bg-emerald-900/50 border border-emerald-500/30 text-emerald-400 px-5 py-2 rounded text-sm disabled:opacity-50"
              title="Save to file so cameras persist after restart"
            >
              Save to File (cameras.yaml)
            </button>
          </div>
        </div>

        {/* Info */}
        <div className="mt-8 text-xs text-slate-600 space-y-1">
          <p>Changes take effect after restarting the edge-node: <code className="text-slate-400">./start.sh</code></p>
          <p>Click <strong className="text-emerald-400">Save to File</strong> to make changes permanent across restarts.</p>
          <p>For phone cameras: install &quot;IP Webcam&quot; app on Android, start the server, and enter <code className="text-slate-400">http://phone-ip:8080/video</code></p>
        </div>

        {/* Back to Dashboard */}
        <div className="mt-6">
          <Link href="/" className="text-cyan-400 hover:text-cyan-300 text-sm">&larr; Back to Dashboard</Link>
        </div>
      </div>
    </div>
  );
}
