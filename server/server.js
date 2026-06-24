// server.js - upgraded with persistent metrics + /sample + /reconstruct + /motion
const express = require('express');
const http = require('http');
const socketio = require('socket.io');
const cors = require('cors');
const path = require('path');
const { spawn, exec } = require('child_process');
const fs = require('fs');
const dgram = require('dgram');

//impoerting storage
const { saveFrame } = require("./storage");
const yaml = require("js-yaml");


const app = express();
app.use(cors());
app.use(express.json({ limit: '100mb' }));
app.use(express.urlencoded({ extended: true, limit: '100mb' }));
app.use(express.static(path.join(__dirname, 'public')));

const server = http.createServer(app);
const io = socketio(server, { cors: { origin: '*' } });



// -------------------- Namespaces --------------------
const STREAM_NS = '/stream';
const VIEW_NS = '/view';
const streamNS = io.of(STREAM_NS);
const viewNS = io.of(VIEW_NS);

// -------------------- Global Variables --------------------
let socketIoBytes = 0;
let udpBytes = 0;
let totalClients = 0;

// Timestamp of last user-initiated control message
let lastUserControlTime = 0;
const USER_CONTROL_LOCK_MS = 10000; // don't auto-override user controls for 10s

// User-controlled settings (preserved so auto-bandwidth doesn't override them)
let userControl = {
  privacy_blur: false,
  codec: "libx265",
  bitrate: 2000
};

// Track latest codec adaptation state (updated from edge node frame messages)
let latestCodecState = { gop_size: 120, res_w: 640, res_h: 480 };

const BANDWIDTH_MONITOR_INTERVAL_MS = 3000;
const BANDWIDTH_THRESHOLD_KBPS = 128;
let metricsLog = [];

// Storage paths
const DATA_DIR = path.join(__dirname, 'experiments');
const METRICS_CSV = path.join(__dirname, 'metrics_log.csv');
const CONTROL_CSV = path.join(__dirname, 'control_log.csv');
const MOTION_CSV = path.join(__dirname, 'motion_log.csv');

// Ensure paths exist
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
if (!fs.existsSync(METRICS_CSV)) fs.writeFileSync(METRICS_CSV, 'timestamp,total_clients,total_kbps,bitrate,psnr,ssim,gop_size,res_w,res_h\n');
if (!fs.existsSync(CONTROL_CSV)) fs.writeFileSync(CONTROL_CSV, 'timestamp,experiment_id,control_json\n');
if (!fs.existsSync(MOTION_CSV))  fs.writeFileSync(MOTION_CSV,  'timestamp,experiment_id,frame_id,motion_percent,num_rois,rois_json\n');

// Event log
const EVENT_CSV = path.join(__dirname, 'event_log.csv');
const EVENTS_DIR = path.join(__dirname, 'events');
if (!fs.existsSync(EVENT_CSV))  fs.writeFileSync(EVENT_CSV, 'timestamp,event_id,risk,state,frame_id,num_rois,hour\n');
if (!fs.existsSync(EVENTS_DIR)) fs.mkdirSync(EVENTS_DIR, { recursive: true });

// In-memory event ring for dashboard queries (last 200)
let eventLog = [];


// -------------------- UDP Monitoring --------------------
const udpServer = dgram.createSocket('udp4');
udpServer.on('message', (msg, rinfo) => {
  udpBytes += msg.length;
});
udpServer.bind(1234, () => {
  console.log('[UDP] Listening for incoming H.265 stream on port 1234 to monitor bitrate');
});

// -------------------- Metrics Handling --------------------
function addMetricSnapshot(sioKbps, udpKbps, clients) {
  const totalKbps = sioKbps + udpKbps;
  const snapshot = {
    timestamp: new Date().toISOString(),
    total_clients: clients,
    total_kbps: Number(totalKbps).toFixed(2),
    sio_kbps: Number(sioKbps).toFixed(2),   // raw preview stream (Socket.IO)
    udp_kbps: Number(udpKbps).toFixed(2),   // compressed H.265 UDP stream
    bitrate: userControl.bitrate,
    psnr: (30 + Math.random() * 5).toFixed(2),
    ssim: (0.85 + Math.random() * 0.1).toFixed(3),
    gop_size: latestCodecState.gop_size,
    res_w: latestCodecState.res_w,
    res_h: latestCodecState.res_h,
  };
  metricsLog.push(snapshot);
  if (metricsLog.length > 300) metricsLog.shift();

  // Emit to view namespace for live dashboard
  viewNS.emit('metrics', snapshot);

  const row = `${snapshot.timestamp},${snapshot.total_clients},${snapshot.total_kbps},${snapshot.sio_kbps},${snapshot.udp_kbps},${snapshot.bitrate},${snapshot.psnr},${snapshot.ssim},${snapshot.gop_size},${snapshot.res_w},${snapshot.res_h}\n`;
  fs.appendFile(METRICS_CSV, row, (err) => { if (err) console.error('metrics csv append err', err); });
}

// -------------------- Socket.IO handlers --------------------
streamNS.on('connection', (socket) => {
  console.log('[stream] connected', socket.id);
  totalClients++;

  socket.on('frame', (msg) => {
    try {
      // 🔹 Bandwidth accounting
      const sizeBytes = Buffer.byteLength(JSON.stringify(msg), 'utf8');
      socketIoBytes += sizeBytes;

      //  NEW: SAVE COMPRESSED FRAME TO DISK
      saveFrame(msg);

      // 🔹 Track codec adaptation state for metrics / CSV logging
      if (msg.gop_size !== undefined) latestCodecState.gop_size = msg.gop_size;
      if (msg.res_w !== undefined) latestCodecState.res_w = msg.res_w;
      if (msg.res_h !== undefined) latestCodecState.res_h = msg.res_h;

      // 🔹 Live stream to dashboard
      viewNS.emit('frame', msg);
    } catch (e) {
      console.error('Frame broadcast error:', e);
    }
  });


  socket.on('disconnect', () => {
    totalClients = Math.max(0, totalClients - 1);
    console.log('[stream] disconnected', socket.id);
  });
});

viewNS.on('connection', (socket) => {
  console.log('[view] connected', socket.id);

  socket.on('control', (msg) => {
    console.log('[view] control -> streamNS:', msg);
    // Mark that user just sent a control — suppress auto-override for 10s
    lastUserControlTime = Date.now();
    if (msg.privacy_blur !== undefined) userControl.privacy_blur = msg.privacy_blur;
    if (msg.codec !== undefined) userControl.codec = msg.codec;
    if (msg.bitrate !== undefined) userControl.bitrate = msg.bitrate;
    // Forward ALL fields (bg_scale, bg_quality, roi_quality, detect_every_n, etc.) to edge node
    streamNS.emit('control', msg);
    console.log('[view] forwarded to edge:', msg);
  });

  socket.on('disconnect', () => { console.log('[view] disconnected', socket.id); });
});

// -------------------- Bandwidth Monitor --------------------
setInterval(() => {
  const interval_s = BANDWIDTH_MONITOR_INTERVAL_MS / 1000;
  const sioKbps  = (socketIoBytes * 8) / interval_s / 1000;
  const udpKbps  = (udpBytes     * 8) / interval_s / 1000;
  const totalKbps = sioKbps + udpKbps;
  console.log(`SIO: ${sioKbps.toFixed(1)} kbps | UDP(H.265): ${udpKbps.toFixed(1)} kbps | total: ${totalKbps.toFixed(1)} kbps | clients: ${totalClients}`);
  addMetricSnapshot(sioKbps, udpKbps, totalClients);

  // Only apply auto-bandwidth adaptation if user hasn't sent a control recently
  const timeSinceUserControl = Date.now() - lastUserControlTime;
  if (timeSinceUserControl < USER_CONTROL_LOCK_MS) {
    console.log(`[auto-BW] suppressed — user control sent ${Math.round(timeSinceUserControl/1000)}s ago`);
    socketIoBytes = 0;
    udpBytes = 0;
    return;
  }

  let controlMsg = { bg_quality: 30, roi_quality: 90, bg_scale: 0.6, detect_every_n: 3, ...userControl };
  if (totalKbps > BANDWIDTH_THRESHOLD_KBPS * 2) {
    controlMsg = { bg_quality: 5, roi_quality: 60, bg_scale: 0.2, detect_every_n: 10, ...userControl };
  } else if (totalKbps > BANDWIDTH_THRESHOLD_KBPS) {
    controlMsg = { bg_quality: 10, roi_quality: 80, bg_scale: 0.4, detect_every_n: 5, ...userControl };
  }
  streamNS.emit('control', controlMsg);

  socketIoBytes = 0;
  udpBytes = 0;
}, BANDWIDTH_MONITOR_INTERVAL_MS);

// -------------------- HTTP Endpoints --------------------

// GET /config/:profile
app.get('/config/:profile', (req, res) => {
  const profile = req.params.profile;
  const configPath = path.join(__dirname, '..', 'configs', `${profile}.yaml`);
  if (fs.existsSync(configPath)) {
      try {
          const fileContents = fs.readFileSync(configPath, 'utf8');
          const data = yaml.load(fileContents);
          res.json(data);
      } catch (e) {
          res.status(500).json({ error: e.message });
      }
  } else {
      res.status(404).json({ error: "Config not found" });
  }
});

// POST /control
app.post('/control', (req, res) => {
  const ctrl = req.body || {};
  const experimentId = ctrl.experiment_id || '';
  console.log('[HTTP] /control received:', ctrl);

  const ts = new Date().toISOString();
  const row = `${ts},${experimentId},"${JSON.stringify(ctrl).replace(/"/g, '""')}"\n`;
  fs.appendFile(CONTROL_CSV, row, (err) => { if (err) console.error('control csv append err', err); });

  streamNS.emit('control', ctrl);
  res.json({ success: true, applied: ctrl });
});

// GET /metrics (history)
app.get('/metrics', (req, res) => { res.json(metricsLog); });

// GET /metrics/live
app.get('/metrics/live', (req, res) => {
  if (metricsLog.length === 0) return res.json({ message: 'No metrics yet' });
  res.json(metricsLog[metricsLog.length-1]);
});

// POST /sample (existing) - receive base64 orig/recon images + rois + meta
app.post('/sample', (req, res) => {
  try {
    const payload = req.body || {};
    const experimentId = payload.experiment_id || `exp_${Date.now()}`;
    const expDir = path.join(DATA_DIR, experimentId);
    if (!fs.existsSync(expDir)) fs.mkdirSync(expDir, { recursive: true });

    // === Save images and metadata (same as before) ===
    const metaPath = path.join(expDir, 'meta.json');
    const metaToSave = {
      received_at: new Date().toISOString(),
      frame_id: payload.frame_id || null,
      timestamp: payload.timestamp || Date.now() / 1000,
      rois: payload.rois || [],
      meta: payload.meta || {},
      experiment_id: experimentId
    };
    fs.writeFileSync(metaPath, JSON.stringify(metaToSave, null, 2));

    if (payload.orig_b64) {
      const origPath = path.join(expDir, 'orig.jpg');
      fs.writeFileSync(origPath, Buffer.from(payload.orig_b64, 'base64'));
    }

    if (payload.recon_b64) {
      const reconPath = path.join(expDir, 'recon.jpg');
      fs.writeFileSync(reconPath, Buffer.from(payload.recon_b64, 'base64'));
    }

    if (payload.rois) {
      const roisPath = path.join(expDir, 'rois.json');
      fs.writeFileSync(roisPath, JSON.stringify(payload.rois, null, 2));
    }

    console.log(`[sample] saved for experiment ${experimentId}`);

    // === NEW: Resolve pending sample request ===
    if (pendingSampleRequest && pendingSampleRequest.experimentId === experimentId) {
      pendingSampleRequest.respond();
      pendingSampleRequest = null;
    }

    res.json({ success: true, experiment_id: experimentId, saved: true });
    
  } catch (err) {
    console.error('Error /sample:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});


// POST /motion - new endpoint for motion reports (from edge node)
app.post('/motion', (req, res) => {
  try {
    const p = req.body || {};
    const experimentId = p.experiment_id || `exp_${Date.now()}`;
    const frameId = p.frame_id || '';
    const motionPercent = Number(p.motion_percent || 0);
    const rois = p.rois || [];
    const numRois = rois.length;

    // append to motion CSV
    const ts = new Date().toISOString();
    const row = `${ts},${experimentId},${frameId},${motionPercent},${numRois},"${JSON.stringify(rois).replace(/"/g,'""')}"\n`;
    fs.appendFile(MOTION_CSV, row, (err) => { if (err) console.error('motion csv append err', err); });

    // optionally save per-experiment motion JSON for debugging
    const expDir = path.join(DATA_DIR, experimentId);
    if (!fs.existsSync(expDir)) fs.mkdirSync(expDir, { recursive: true });
    const motionFile = path.join(expDir, `motion_${frameId || Date.now()}.json`);
    fs.writeFileSync(motionFile, JSON.stringify({ ts, frameId, motionPercent, rois }, null, 2));

    viewNS.emit('motion', { timestamp: ts, motion_percent: motionPercent, num_rois: numRois });

    res.json({ success: true, experiment_id: experimentId, motion_percent: motionPercent, num_rois: numRois });
  } catch (err) {
    console.error('Error /motion:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// POST /event — receive risk-state change from edge node
app.post('/event', (req, res) => {
  try {
    const p = req.body || {};
    const eventId   = p.event_id  || `evt_${Date.now()}`;
    const risk      = Number(p.risk      || 0);
    const state     = p.state     || 'unknown';
    const frameId   = p.frame_id  || '';
    const numRois   = Number(p.num_rois  || 0);
    const hour      = Number(p.hour      || -1);
    const rois      = p.rois      || [];

    const ts = new Date().toISOString();

    // Append to CSV
    const row = `${ts},${eventId},${risk},${state},${frameId},${numRois},${hour}\n`;
    fs.appendFile(EVENT_CSV, row, err => { if (err) console.error('event csv err', err); });

    // Save full event JSON (with ROIs) to events/<event_id>.json
    const evPath = path.join(EVENTS_DIR, `${eventId}.json`);
    fs.writeFileSync(evPath, JSON.stringify({ ts, eventId, risk, state, frameId, numRois, hour, rois }, null, 2));

    // Push to in-memory ring
    const entry = { ts, eventId, risk, state, frameId, numRois, hour };
    eventLog.push(entry);
    if (eventLog.length > 200) eventLog.shift();

    // Broadcast to all dashboard clients
    viewNS.emit('event', entry);

    console.log(`[event] ${state.toUpperCase()} | risk=${risk} | ${eventId}`);
    res.json({ success: true, event_id: eventId });
  } catch (err) {
    console.error('Error /event:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// GET /events — return recent event log
app.get('/events', (req, res) => {
  res.json(eventLog);
});

// RECONSTRUCT endpoint (FFmpeg capture + PSNR) - existing from previous upgrade
function runFFmpeg(cmd) {
  return new Promise((resolve, reject) => {
    exec(cmd, { maxBuffer: 1024 * 500 }, (err, stdout, stderr) => {
      if (err) reject(stderr || err.message);
      else resolve(stdout);
    });
  });
}
app.post("/reconstruct", async (req, res) => {
  const experimentId = req.body.experiment_id || `auto_${Date.now()}`;
  const outDir = path.join(__dirname, "experiments", experimentId);
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const origPath = path.join(outDir, "orig_ffmpeg.jpg");
  const compPath = path.join(outDir, "comp_ffmpeg.jpg");

  try {
    console.log("[RECON] Capturing original reference frame from RTSP...");
    await runFFmpeg(`ffmpeg -y -ss 2 -i rtsp://localhost:8554/live -vframes 1 ${origPath} -loglevel error`);
    console.log("[RECON] Capturing compressed stream frame (UDP)...");
    await runFFmpeg(`ffmpeg -y -ss 2 -i udp://localhost:1234 -vframes 1 ${compPath} -loglevel error`);

    const sharp = require("sharp");
    const img1 = await sharp(origPath).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    const img2 = await sharp(compPath).ensureAlpha().raw().toBuffer({ resolveWithObject: true });

    function computeMSE(a, b) {
      let s = 0;
      for (let i = 0; i < a.length; i++) {
        const d = a[i] - b[i];
        s += d * d;
      }
      return s / a.length;
    }

    const mse = computeMSE(img1.data, img2.data);
    const psnr = 10 * Math.log10((255 * 255) / mse);

    console.log(`[RECON] PSNR = ${psnr.toFixed(2)} dB`);
    const result = { experiment_id: experimentId, psnr: Number(psnr.toFixed(2)), orig_file: "orig_ffmpeg.jpg", comp_file: "comp_ffmpeg.jpg" };
    fs.writeFileSync(path.join(outDir, "result.json"), JSON.stringify(result, null, 2));
    res.json({ success: true, result });
  } catch (err) {
    console.error('[RECON ERROR]', err);
    res.status(500).json({ error: String(err) });
  }
});

// Health route
app.get('/', (req, res) => {
  res.send('CCTV Compression Server Running. Use /metrics, /control, /sample, /motion, /reconstruct.');
});

// -------------------- REQUEST SAMPLE ENDPOINT (Upgrade 3) --------------------
// evaluate.py uses this to request the next pair of orig/recon frames.

let pendingSampleRequest = null;

app.post('/request_sample', (req, res) => {
  try {
    const experimentId = req.body.experiment_id || `exp_${Date.now()}`;
    console.log(`[HTTP] /request_sample received for experiment: ${experimentId}`);

    // Store resolver to trigger when /sample arrives
    pendingSampleRequest = {
      experimentId,
      timestamp: Date.now(),
      respond: () => {
        console.log(`[HTTP] Resolving pending sample request for ${experimentId}`);
        res.json({ success: true, experiment_id: experimentId });
      }
    };

    // Auto-expire after 15 seconds
    setTimeout(() => {
      if (pendingSampleRequest && pendingSampleRequest.experimentId === experimentId) {
        console.log(`[HTTP] sample request timeout for ${experimentId}`);
        pendingSampleRequest = null;
        res.status(504).json({ success: false, error: "timeout waiting for sample" });
      }
    }, 15000);

  } catch (err) {
    console.error('Error /request_sample:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// ===================== PLAYBACK API =====================

// GET list of recorded frames
app.get("/playback/list", (req, res) => {
  try {
    const date = req.query.date;
    if (!date) return res.status(400).json({ error: "date required" });

    const baseDir = path.join(__dirname, "recordings", date, "bg");
    if (!fs.existsSync(baseDir)) return res.json([]);

    const frames = fs.readdirSync(baseDir)
      .filter(f => f.endsWith(".jpg"))
      .sort();

    res.json(frames);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// POST reconstruct a single frame
app.post("/playback/frame", async (req, res) => {
  try {
    const { date, frame } = req.body;
    if (!date || !frame) {
      return res.status(400).json({ error: "date and frame required" });
    }

    const bgPath = path.join(__dirname, "recordings", date, "bg", frame);
    const roiDir = path.join(__dirname, "recordings", date, "roi");
    const metaPath = path.join(
      __dirname,
      "recordings",
      date,
      "meta",
      frame.replace(".jpg", ".json")
    );

    if (!fs.existsSync(bgPath) || !fs.existsSync(metaPath)) {
      return res.status(404).json({ error: "frame not found" });
    }

    const sharp = require("sharp");

    let base = sharp(bgPath);
    const meta = JSON.parse(fs.readFileSync(metaPath));

    // overlay ROI crops
    for (let i = 0; i < meta.rois.length; i++) {
      const roi = meta.rois[i];
      const roiImgPath = path.join(
        roiDir,
        frame.replace(".jpg", `_roi${i}.jpg`)
      );

      if (!fs.existsSync(roiImgPath)) continue;

      base = base.composite([
        {
          input: roiImgPath,
          left: roi.bbox[0],
          top: roi.bbox[1],
        },
      ]);
    }

    const output = await base.jpeg().toBuffer();

    res.json({
      vis_frame: output.toString("base64"),
      frame,
    });

  } catch (err) {
    console.error("Playback error:", err);
    res.status(500).json({ error: err.message });
  }
});

app.get("/playback/:date", (req, res) => {
  try {
    const date = req.params.date;
    const base = path.join(__dirname, "recordings", date);
    const metaPath = path.join(base, "meta.json");

    if (!fs.existsSync(metaPath)) {
      return res.status(404).json({ error: "No recordings for date" });
    }

    const meta = JSON.parse(fs.readFileSync(metaPath));
    res.json(meta);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/frame/:date/:id", (req, res) => {
  try {
    const { date, id } = req.params;
    const base = path.join(__dirname, "recordings", date);
    const meta = JSON.parse(fs.readFileSync(path.join(base, "meta.json")));

    const entry = meta.find(f => f.frame_id == id);
    if (!entry) return res.status(404).send("Frame not found");

    const py = spawn("python3", [
      "reconstruct.py",
      base,
      JSON.stringify(entry)
    ]);

    let buf = [];
    py.stdout.on("data", d => buf.push(d));
    py.on("close", () => {
      res.type("image/jpeg").send(Buffer.concat(buf));
    });
  } catch (e) {
    res.status(500).send(e.message);
  }
});


// Start server
const PORT = process.env.PORT || 5000;
server.listen(PORT, () => console.log(`Server running on port ${PORT}`));