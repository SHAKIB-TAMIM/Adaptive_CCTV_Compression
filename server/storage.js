const fs = require("fs");
const path = require("path");

const RECORDINGS_DIR = path.join(__dirname, "recordings");
const RETENTION_DAYS = 7;

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

  const baseDir = path.join(RECORDINGS_DIR, cameraId, date);
  const frameDir = path.join(baseDir, "frames");
  ensureDir(frameDir);

  const frameId = msg.frame_id.toString().padStart(8, "0");

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

function purgeOldRecordings() {
  if (!fs.existsSync(RECORDINGS_DIR)) return;
  const now = Date.now();
  const cutoff = now - RETENTION_DAYS * 86400 * 1000;

  const camDirs = fs.readdirSync(RECORDINGS_DIR);
  for (const camDir of camDirs) {
    const camPath = path.join(RECORDINGS_DIR, camDir);
    if (!fs.statSync(camPath).isDirectory()) continue;
    const dateDirs = fs.readdirSync(camPath);
    for (const dateDir of dateDirs) {
      const datePath = path.join(camPath, dateDir);
      if (!fs.statSync(datePath).isDirectory()) continue;
      // Parse date string (YYYY-MM-DD) to timestamp
      const dateTs = new Date(dateDir + "T00:00:00Z").getTime();
      if (isNaN(dateTs) || dateTs < cutoff) {
        fs.rmSync(datePath, { recursive: true, force: true });
        console.log(`[retention] Purged old recording: ${camDir}/${dateDir}`);
      }
    }
    // Remove empty camera dirs
    if (fs.existsSync(camPath) && fs.readdirSync(camPath).length === 0) {
      fs.rmdirSync(camPath);
    }
  }
}

module.exports = { saveFrame, purgeOldRecordings };
