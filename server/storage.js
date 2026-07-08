const fs = require("fs");
const path = require("path");

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function saveFrame(msg) {
  const cameraId = msg.camera_id || "camera_0";
  const date = new Date(msg.timestamp * 1000)
    .toISOString()
    .slice(0, 10);

  // Per-camera, per-date directory structure
  const baseDir = path.join(__dirname, "recordings", cameraId, date);
  const frameDir = path.join(baseDir, "frames");
  ensureDir(frameDir);

  const frameId = msg.frame_id.toString().padStart(8, "0");

  // Save the vis_frame (compressed preview) as the recording frame
  if (msg.vis_frame) {
    const bgBuffer = Buffer.from(msg.vis_frame, "base64");
    fs.writeFileSync(
      path.join(frameDir, `frame_${frameId}.jpg`),
      bgBuffer
    );
  }

  // Append to per-camera per-date metadata
  const metaPath = path.join(baseDir, "meta.json");
  const meta = fs.existsSync(metaPath)
    ? JSON.parse(fs.readFileSync(metaPath))
    : [];

  meta.push({
    camera_id: cameraId,
    frame_id: msg.frame_id,
    timestamp: msg.timestamp,
    state: msg.state || "normal",
    risk: msg.risk || 0,
    rois: (msg.rois || []).map(r => r.bbox),
    orig_w: msg.orig_w,
    orig_h: msg.orig_h,
    gop_size: msg.gop_size,
    res_w: msg.res_w,
    res_h: msg.res_h,
  });

  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
}

module.exports = { saveFrame };
