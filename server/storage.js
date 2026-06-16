const fs = require("fs");
const path = require("path");

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function saveFrame(msg) {
  const date = new Date(msg.timestamp * 1000)
    .toISOString()
    .slice(0, 10);

  const baseDir = path.join(__dirname, "recordings", date);
  const bgDir = path.join(baseDir, "bg");
  const roiDir = path.join(baseDir, "roi");

  ensureDir(bgDir);
  ensureDir(roiDir);

  const frameId = msg.frame_id.toString().padStart(6, "0");

  // 🔹 Save background frame
  if (msg.bg_data) {
    const bgBuffer = Buffer.from(msg.bg_data, "base64");
    fs.writeFileSync(
      path.join(bgDir, `frame_${frameId}.jpg`),
      bgBuffer
    );
  }

  // 🔹 Save ROI frames
  (msg.rois || []).forEach((r, i) => {
    if (!r.data) return;
    const roiBuffer = Buffer.from(r.data, "base64");
    fs.writeFileSync(
      path.join(roiDir, `frame_${frameId}_roi${i}.jpg`),
      roiBuffer
    );
  });

  // 🔹 Append metadata
  const metaPath = path.join(baseDir, "meta.json");
  const meta = fs.existsSync(metaPath)
    ? JSON.parse(fs.readFileSync(metaPath))
    : [];

  meta.push({
    frame_id: msg.frame_id,
    timestamp: msg.timestamp,
    rois: msg.rois.map(r => r.bbox),
    orig_w: msg.orig_w,
    orig_h: msg.orig_h
  });

  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
}

module.exports = { saveFrame };
