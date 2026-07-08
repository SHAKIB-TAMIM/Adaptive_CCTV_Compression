"use client";

import { useEffect, useState, useRef } from "react";

const API_URL = "http://localhost:5000";

export default function RecordingPlayback() {
  const [recordings, setRecordings] = useState({});
  const [selectedCam, setSelectedCam] = useState("");
  const [selectedDate, setSelectedDate] = useState("");
  const [frames, setFrames] = useState([]);
  const [currentFrameIdx, setCurrentFrameIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [cameras, setCameras] = useState([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    fetch(`${API_URL}/cameras`)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) setCameras(data);
      })
      .catch(() => {});
    fetch(`${API_URL}/recordings`)
      .then((r) => r.json())
      .then(setRecordings)
      .catch(() => {});
  }, []);

  const refreshRecordings = () => {
    fetch(`${API_URL}/recordings`)
      .then((r) => r.json())
      .then(setRecordings)
      .catch(() => {});
  };

  const loadFrames = async () => {
    if (!selectedCam || !selectedDate) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/recordings/${selectedCam}/${selectedDate}`);
      const data = await res.json();
      setFrames(data);
      setCurrentFrameIdx(0);
    } catch (e) {
      console.error("Failed to load frames:", e);
    }
    setLoading(false);
  };

  useEffect(() => {
    if (selectedCam && selectedDate) {
      loadFrames();
    }
  }, [selectedCam, selectedDate]);

  useEffect(() => {
    if (playing && frames.length > 0) {
      timerRef.current = setInterval(() => {
        setCurrentFrameIdx((prev) => {
          const next = prev + 1;
          if (next >= frames.length) {
            setPlaying(false);
            return prev;
          }
          return next;
        });
      }, 66);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, frames.length]);

  const currentFrame = frames[currentFrameIdx];

  return (
    <div className="bg-slate-950/40 border border-slate-900 backdrop-blur-md rounded-2xl p-5 shadow-2xl flex flex-col gap-4">
      <h3 className="text-sm font-semibold tracking-wider text-slate-400 uppercase border-b border-slate-900 pb-2 flex items-center gap-2">
        <span className="w-2.5 h-2.5 bg-purple-500 rounded-full animate-pulse"></span>
        Recording Playback
      </h3>

      {/* Controls */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div>
          <label className="block text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-1">Camera</label>
          <select
            value={selectedCam}
            onChange={(e) => setSelectedCam(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 text-slate-300 text-xs rounded-lg p-2 focus:outline-none focus:border-purple-500"
          >
            <option value="">Select camera</option>
            {cameras.map((cam) => (
              <option key={cam.id} value={cam.id}>{cam.name || cam.id}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-1">Date</label>
          <select
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 text-slate-300 text-xs rounded-lg p-2 focus:outline-none focus:border-purple-500"
          >
            <option value="">Select date</option>
            {(recordings[selectedCam] || []).map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>

        <div className="flex items-end gap-2">
          <button
            onClick={() => setPlaying(!playing)}
            disabled={!currentFrame}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-bold rounded-lg transition-colors"
          >
            {playing ? "⏸ Pause" : "▶ Play"}
          </button>
          <button
            onClick={refreshRecordings}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg transition-colors"
          >
            ↻ Refresh
          </button>
        </div>

        <div className="flex items-end justify-end text-xs text-slate-500 font-mono">
          {frames.length > 0 && (
            <span>Frame {currentFrameIdx + 1} / {frames.length}</span>
          )}
        </div>
      </div>

      {/* Timeline scrubber */}
      {frames.length > 1 && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 w-8 text-right">{currentFrameIdx}</span>
          <input
            type="range"
            min="0"
            max={frames.length - 1}
            value={currentFrameIdx}
            onChange={(e) => { setCurrentFrameIdx(Number(e.target.value)); setPlaying(false); }}
            className="flex-1 accent-purple-500 bg-slate-900 rounded-lg appearance-none h-1.5 cursor-pointer"
          />
          <span className="text-[10px] text-slate-500 w-8">{frames.length - 1}</span>
        </div>
      )}

      {/* Frame display */}
      <div className="relative aspect-video w-full bg-[#030407] rounded-xl overflow-hidden border border-slate-800">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/80 z-10">
            <p className="text-slate-400 text-sm">Loading frames...</p>
          </div>
        )}
        {currentFrame ? (
          <img
            src={`${API_URL}/recordings/${selectedCam}/${selectedDate}/frame/${currentFrame}`}
            className="w-full h-full object-contain"
            alt={`Frame ${currentFrame}`}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-slate-600 text-sm">
            {selectedCam && selectedDate ? "No frames found" : "Select camera and date to view recordings"}
          </div>
        )}
      </div>

      {/* Frame info */}
      {currentFrame && (
        <div className="text-[10px] text-slate-500 font-mono">
          File: {currentFrame} | Camera: {selectedCam} | Date: {selectedDate}
        </div>
      )}
    </div>
  );
}
