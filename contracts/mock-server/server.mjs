// Mock-бэкенд Transit Pulse для разработки дашборда без Python и датасета.
//
// Отдаёт REST /api/v1/* и WebSocket /ws/v1/stream строго по контракту
// (contracts/ts/contract.ts), проигрывая записанную сессию contracts/fixtures/ws_session.jsonl:
// один кадр (10 сим-секунд) в секунду. По концу сессии начинается новая —
// с новым session_id и свежим snapshot, как при перемотке реального потока.
//
// Запуск:   node contracts/mock-server/server.mjs      (порт 8000, MOCK_PORT / MOCK_SPEED)
// Справка:  GET http://localhost:8000/mock
// Без зависимостей: WebSocket реализован на node:http + node:crypto.

import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
const PORT = Number(process.env.MOCK_PORT ?? 8000);
const WS_PATH = "/ws/v1/stream";
const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

const load = (name) => JSON.parse(readFileSync(join(FIXTURES, name), "utf-8"));
const frames = readFileSync(join(FIXTURES, "ws_session.jsonl"), "utf-8")
  .split("\n")
  .filter(Boolean)
  .map((line) => JSON.parse(line));
const fixtures = {
  config: load("config.json"),
  health: load("health.json"),
  network: load("network.json"),
  segments: load("segments_risk.json"),
  summary: load("metrics_summary.json"),
  quality: load("metrics_quality.json"),
  ingest: load("ingest_stats.json"),
};
const baseSimSpeed = fixtures.config.sim_speed;

// ------------------------------------------------------------------ состояние
const state = {
  sessionNo: 1,
  sessionId: "mock-0001",
  frame: -1,
  simTime: frames[0].sim_time,
  speed: Number(process.env.MOCK_SPEED ?? 1),
  paused: false,
  vehicles: new Map(),
  incidents: new Map(),
  kpis: null,
  status: null,
  statusOverride: null,
};

function envelope(type, data) {
  return { type, schema_version: "1.0", session_id: state.sessionId, sim_time: state.simTime, data };
}

function snapshot() {
  return envelope("snapshot", {
    vehicles: [...state.vehicles.values()],
    incidents: [...state.incidents.values()],
    kpis: state.kpis,
    status: state.statusOverride ?? state.status,
  });
}

function apply(msg) {
  state.simTime = msg.sim_time;
  if (msg.type === "vehicles.delta") {
    for (const v of msg.data.vehicles) state.vehicles.set(v.vehicle_id, v);
    for (const id of msg.data.removed) state.vehicles.delete(id);
  } else if (msg.type.startsWith("incident.")) {
    state.incidents.set(msg.data.id, msg.data);
  } else if (msg.type === "kpis") {
    state.kpis = msg.data;
  } else if (msg.type === "system.status") {
    state.status = msg.data;
  }
}

function newSession(startFrame = 0) {
  state.sessionNo += 1;
  state.sessionId = `mock-${String(state.sessionNo).padStart(4, "0")}`;
  state.vehicles.clear();
  state.incidents.clear();
  state.frame = -1;
  // Прогоняем кадры до startFrame молча, чтобы snapshot был согласованным.
  while (state.frame < startFrame) stepFrame(false);
  broadcast(snapshot());
}

function stepFrame(emit = true) {
  if (state.frame + 1 >= frames.length) {
    newSession(0);
    return;
  }
  state.frame += 1;
  for (const raw of frames[state.frame].messages) {
    const msg = { ...raw, session_id: state.sessionId };
    apply(msg);
    if (emit && !(state.statusOverride && msg.type === "system.status")) broadcast(msg);
  }
}

stepFrame(false);
let timer = null;
function schedule() {
  clearInterval(timer);
  timer = setInterval(() => state.paused || stepFrame(), 1000 / state.speed);
}
schedule();

// ------------------------------------------------------------------ WebSocket
const clients = new Set();

function encodeFrame(text) {
  const payload = Buffer.from(text, "utf-8");
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}

function broadcast(msg) {
  const frame = encodeFrame(JSON.stringify(msg));
  for (const socket of clients) socket.write(frame);
}

function handleClientBytes(socket, chunk) {
  // Клиентские кадры нужны только для close/ping; текст от клиента игнорируем.
  socket.buffer = Buffer.concat([socket.buffer, chunk]);
  while (socket.buffer.length >= 2) {
    const opcode = socket.buffer[0] & 0x0f;
    let len = socket.buffer[1] & 0x7f;
    let offset = 2;
    if (len === 126) {
      if (socket.buffer.length < 4) return;
      len = socket.buffer.readUInt16BE(2);
      offset = 4;
    } else if (len === 127) {
      if (socket.buffer.length < 10) return;
      len = Number(socket.buffer.readBigUInt64BE(2));
      offset = 10;
    }
    const masked = socket.buffer[1] & 0x80;
    const total = offset + (masked ? 4 : 0) + len;
    if (socket.buffer.length < total) return;
    socket.buffer = socket.buffer.subarray(total);
    if (opcode === 0x8) {
      socket.end(Buffer.from([0x88, 0]));
      clients.delete(socket);
    } else if (opcode === 0x9) {
      socket.write(Buffer.from([0x8a, 0]));
    }
  }
}

function upgrade(req, socket) {
  const url = new URL(req.url, "http://localhost");
  const key = req.headers["sec-websocket-key"];
  if (url.pathname !== WS_PATH || !key) {
    socket.end("HTTP/1.1 404 Not Found\r\n\r\n");
    return;
  }
  const accept = createHash("sha1").update(key + WS_GUID).digest("base64");
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
  );
  socket.buffer = Buffer.alloc(0);
  socket.on("data", (chunk) => handleClientBytes(socket, chunk));
  socket.on("close", () => clients.delete(socket));
  socket.on("error", () => clients.delete(socket));
  clients.add(socket);
  socket.write(encodeFrame(JSON.stringify(snapshot())));
}

// ----------------------------------------------------------------------- REST
function send(res, status, body) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  });
  res.end(JSON.stringify(body));
}

const notFound = (res, what) => send(res, 404, { detail: `${what} не найден` });

async function readJson(req) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

function vehicleDetail(id) {
  const vehicle = state.vehicles.get(id);
  const file = join(FIXTURES, "vehicle_details", `${id}.json`);
  if (!vehicle && !existsSync(file)) return null;
  const incidentIds = [...state.incidents.values()].filter((i) => i.vehicle_id === id).map((i) => i.id);
  const base = existsSync(file)
    ? JSON.parse(readFileSync(file, "utf-8"))
    : { timeline: [], deviation_series: [], forecast: null };
  return { ...base, vehicle: vehicle ?? base.vehicle, incident_ids: incidentIds };
}

function ackIncident(id, body) {
  const incident = state.incidents.get(id);
  if (!incident) return null;
  const ack = { action_code: body.action_code ?? null, comment: body.comment ?? null, at: state.simTime };
  const updated = { ...incident, status: incident.status === "resolved" ? "resolved" : "ack", ack, updated_at: state.simTime };
  state.incidents.set(id, updated);
  broadcast(envelope("incident.updated", updated));
  return updated;
}

function replayControl(body) {
  if (body.action === "pause") state.paused = true;
  if (body.action === "resume" || body.action === "start") state.paused = false;
  if (body.action === "speed" && body.speed > 0) {
    state.speed = body.speed / baseSimSpeed;
    schedule();
  }
  if (body.action === "seek" && body.seek_to) {
    const idx = frames.findIndex((f) => f.sim_time >= body.seek_to);
    newSession(Math.max(idx, 0));
  }
  return clock();
}

function clock() {
  return {
    sim_time: state.simTime,
    session_id: state.sessionId,
    speed: baseSimSpeed * state.speed,
    state: state.paused ? "paused" : "running",
  };
}

function setMockStatus(body) {
  if (!body.mode || body.mode === "AUTO") {
    state.statusOverride = null;
  } else {
    const reason = body.reason ?? `Режим ${body.mode} включён вручную через /mock/status`;
    state.statusOverride = { ...state.status, mode: body.mode, reason };
  }
  broadcast(envelope("system.status", state.statusOverride ?? state.status));
  return state.statusOverride ?? state.status;
}

const HELP = {
  about: "Mock-бэкенд Transit Pulse: REST /api/v1/* и WS /ws/v1/stream по контракту",
  rest: [
    "GET /api/v1/health", "GET /api/v1/config", "GET /api/v1/network", "GET /api/v1/vehicles",
    "GET /api/v1/vehicles/{vehicle_id}", "GET /api/v1/incidents?status=open,ack",
    "GET /api/v1/incidents/{id}", "POST /api/v1/incidents/{id}/ack", "GET /api/v1/segments/risk",
    "GET /api/v1/metrics/summary", "GET /api/v1/metrics/quality", "GET /api/v1/ingest/stats",
    "GET /api/v1/sim/clock", "POST /api/v1/replay/control",
  ],
  ws: WS_PATH,
  mock_only: {
    "POST /mock/status": "{ mode: 'OFFLINE' | 'DEGRADED' | 'LIVE' | 'WARMING_UP' | 'PAUSED' | 'AUTO' } — принудительный режим",
  },
  session: { frames: frames.length, frame_seconds: 10, ...clock() },
};

async function route(req, res) {
  const url = new URL(req.url, "http://localhost");
  const path = url.pathname.replace(/\/+$/, "");
  const m = (re) => path.match(re);
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (req.method === "GET") {
    if (path === "/mock" || path === "") return send(res, 200, { ...HELP, session: { frames: frames.length, ...clock() } });
    if (path === "/api/v1/health") return send(res, 200, fixtures.health);
    if (path === "/api/v1/config") return send(res, 200, { ...fixtures.config, session_id: state.sessionId, sim_speed: baseSimSpeed * state.speed });
    if (path === "/api/v1/network") return send(res, 200, fixtures.network);
    if (path === "/api/v1/vehicles") return send(res, 200, [...state.vehicles.values()]);
    if (path === "/api/v1/segments/risk") return send(res, 200, fixtures.segments);
    if (path === "/api/v1/metrics/summary") return send(res, 200, { ...fixtures.summary, kpis: state.kpis ?? fixtures.summary.kpis });
    if (path === "/api/v1/metrics/quality") return send(res, 200, fixtures.quality);
    if (path === "/api/v1/ingest/stats") return send(res, 200, fixtures.ingest);
    if (path === "/api/v1/sim/clock") return send(res, 200, clock());
    if (path === "/api/v1/incidents") {
      const wanted = url.searchParams.get("status")?.split(",");
      const list = [...state.incidents.values()].filter((i) => !wanted || wanted.includes(i.status));
      return send(res, 200, list);
    }
    let hit = m(/^\/api\/v1\/vehicles\/([^/]+)$/);
    if (hit) return vehicleDetail(decodeURIComponent(hit[1])) ? send(res, 200, vehicleDetail(decodeURIComponent(hit[1]))) : notFound(res, "ТС");
    hit = m(/^\/api\/v1\/incidents\/([^/]+)$/);
    if (hit) return state.incidents.has(hit[1]) ? send(res, 200, state.incidents.get(hit[1])) : notFound(res, "Инцидент");
  }
  if (req.method === "POST") {
    const body = await readJson(req);
    const hit = m(/^\/api\/v1\/incidents\/([^/]+)\/ack$/);
    if (hit) {
      const updated = ackIncident(hit[1], body);
      return updated ? send(res, 200, updated) : notFound(res, "Инцидент");
    }
    if (path === "/api/v1/replay/control") return send(res, 200, replayControl(body));
    if (path === "/mock/status") return send(res, 200, setMockStatus(body));
  }
  return send(res, 404, { detail: `Нет маршрута ${req.method} ${path}` });
}

const server = createServer((req, res) => {
  route(req, res).catch((err) => send(res, 400, { detail: String(err) }));
});
server.on("upgrade", upgrade);
server.listen(PORT, () => {
  console.log(`Transit Pulse mock: http://localhost:${PORT}/mock  ws://localhost:${PORT}${WS_PATH}`);
  console.log(`Сессия: ${frames.length} кадров по 10 сим-секунд, скорость ×${state.speed}`);
});
