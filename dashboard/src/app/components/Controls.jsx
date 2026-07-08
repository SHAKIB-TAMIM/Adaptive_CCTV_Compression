"use client";

import { useState } from "react";
import { io } from "socket.io-client";

const SERVER_VIEW_NS = "http://127.0.0.1:5000/view";
const socket = io(SERVER_VIEW_NS);

export default function Controls() {
  const [privacyBlur, setPrivacyBlur] = useState(false);
  const [codec, setCodec] = useState("libx265");
  const [bitrate, setBitrate] = useState(2000);

  const [ethicalMode, setEthicalMode] = useState(false);
  const [maskFaces, setMaskFaces] = useState(false);

  const handleBlurToggle = (e) => {
    const val = e.target.checked;
    setPrivacyBlur(val);
    socket.emit("control", { privacy_blur: val });
  };

  const handleEthicalToggle = (e) => {
    const val = e.target.checked;
    setEthicalMode(val);
    socket.emit("control", { ethical_mode: val });
  };

  const handleMaskFacesToggle = (e) => {
    const val = e.target.checked;
    setMaskFaces(val);
    socket.emit("control", { mask_faces: val });
  };

  const handleCodecChange = (e) => {
    const val = e.target.value;
    setCodec(val);
    socket.emit("control", { codec: val });
  };

  const handleBitrateChange = (e) => {
    const val = parseInt(e.target.value, 10);
    setBitrate(val);
    socket.emit("control", { bitrate: val });
  };

  return (
    <div style={{
      background: "#222",
      padding: "20px",
      borderRadius: "8px",
      marginTop: "20px",
      color: "#ddd",
      display: "flex",
      gap: "20px",
      flexWrap: "wrap",
      alignItems: "center"
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <input 
          type="checkbox" 
          id="blurToggle" 
          checked={privacyBlur} 
          onChange={handleBlurToggle} 
          style={{ width: "20px", height: "20px" }}
        />
        <label htmlFor="blurToggle" style={{ fontSize: "16px", cursor: "pointer" }}>Enable Privacy Blur</label>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <input 
          type="checkbox" 
          id="ethicalToggle" 
          checked={ethicalMode} 
          onChange={handleEthicalToggle} 
          style={{ width: "20px", height: "20px" }}
        />
        <label htmlFor="ethicalToggle" style={{ fontSize: "16px", cursor: "pointer" }}>Ethical Mode (Drop BG)</label>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <input 
          type="checkbox" 
          id="maskFacesToggle" 
          checked={maskFaces} 
          onChange={handleMaskFacesToggle} 
          style={{ width: "20px", height: "20px" }}
        />
        <label htmlFor="maskFacesToggle" style={{ fontSize: "16px", cursor: "pointer" }}>Mask Faces Only</label>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <label htmlFor="codecSelect">Codec: </label>
        <select 
          id="codecSelect" 
          value={codec} 
          onChange={handleCodecChange}
          style={{ background: "#333", color: "#fff", padding: "6px", borderRadius: "4px" }}
        >
          <option value="libx264">H.264 (libx264)</option>
          <option value="libx265">H.265 (libx265)</option>
          <option value="hevc_nvenc">H.265 (NVENC)</option>
        </select>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <label htmlFor="bitrateRange">Target Bitrate ({bitrate} kbps): </label>
        <input 
          type="range" 
          id="bitrateRange" 
          min="500" 
          max="8000" 
          step="500" 
          value={bitrate} 
          onChange={handleBitrateChange} 
        />
      </div>
    </div>
  );
}
