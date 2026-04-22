const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const PORT = Number(process.env.PORT || 8098);
const TOKEN = process.env.THUNDER_BRIDGE_TOKEN || "change-me";
const PUBLIC_DIR = path.join(__dirname, "public");

const clients = new Set();
const agentSockets = new Set();
let agentOnline = false;
let latestState = {
  type: "snapshot",
  updatedAt: null,
  agentOnline: false,
  mac: {},
  stats: {
    totalSpeed: 0,
    activeTasks: 0,
    completedTasks: 0,
    totalTasks: 0,
  },
  tasks: [],
  migrations: [],
  events: [],
  pendingDialogs: [],
  pendingDialog: null,
};

function appendEvent(event) {
  const item = {
    time: new Date().toISOString(),
    ...event,
  };
  latestState.events = [item, ...(latestState.events || [])].slice(0, 60);
  broadcast({ ...latestState, type: "snapshot", agentOnline });
}

function sendFrame(socket, data) {
  if (socket.destroyed) return;
  const payload = Buffer.from(data);
  let header;
  if (payload.length < 126) {
    header = Buffer.from([0x81, payload.length]);
  } else if (payload.length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  socket.write(Buffer.concat([header, payload]));
}

function sendJson(socket, message) {
  sendFrame(socket, JSON.stringify(message));
}

function broadcast(message) {
  const data = JSON.stringify(message);
  for (const client of clients) {
    sendFrame(client, data);
  }
}

function parseFrames(buffer) {
  const messages = [];
  let offset = 0;
  while (offset + 2 <= buffer.length) {
    const first = buffer[offset];
    const second = buffer[offset + 1];
    const opcode = first & 0x0f;
    const masked = (second & 0x80) !== 0;
    let length = second & 0x7f;
    let headerLength = 2;
    if (length === 126) {
      if (offset + 4 > buffer.length) break;
      length = buffer.readUInt16BE(offset + 2);
      headerLength = 4;
    } else if (length === 127) {
      if (offset + 10 > buffer.length) break;
      length = Number(buffer.readBigUInt64BE(offset + 2));
      headerLength = 10;
    }
    const maskLength = masked ? 4 : 0;
    const frameLength = headerLength + maskLength + length;
    if (offset + frameLength > buffer.length) break;

    let payload = buffer.subarray(offset + headerLength + maskLength, offset + frameLength);
    if (masked) {
      const mask = buffer.subarray(offset + headerLength, offset + headerLength + 4);
      payload = Buffer.from(payload.map((byte, index) => byte ^ mask[index % 4]));
    }
    if (opcode === 0x1) {
      messages.push(payload.toString("utf8"));
    }
    offset += frameLength;
  }
  return { messages, remaining: buffer.subarray(offset) };
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js") return "application/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".json") return "application/json; charset=utf-8";
  return "application/octet-stream";
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 1024 * 128) {
        reject(new Error("request body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

function isSupportedDownloadUrl(value) {
  return /^(magnet:\?|https?:\/\/|ftp:\/\/|ed2k:\/\/|thunder:\/\/)/i.test(String(value || "").trim());
}

function isPreviewableDownloadUrl(value) {
  return /^(magnet:\?|ed2k:\/\/|thunder:\/\/)/i.test(String(value || "").trim());
}

function isSupportedTaskAction(value) {
  return ["start", "pause", "delete"].includes(String(value || "").trim().toLowerCase());
}

function onlineAgents() {
  return Array.from(agentSockets).filter((socket) => !socket.destroyed);
}

function commandId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function dispatchAgentCommand(command) {
  const agents = onlineAgents();
  if (agents.length === 0) {
    return false;
  }
  for (const agent of agents) {
    sendJson(agent, command);
  }
  return true;
}

async function handleAddTask(req, res) {
  try {
    const body = await readJsonBody(req);
    const url = String(body.url || "").trim();
    if (!isSupportedDownloadUrl(url)) {
      writeJson(res, 400, { code: -1, message: "只支持 magnet/http/https/ftp/ed2k/thunder 链接" });
      return;
    }
    if (onlineAgents().length === 0) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法下发下载任务" });
      return;
    }
    const command = {
      type: "command",
      command: "addTask",
      id: commandId(),
      url,
      name: String(body.name || "").trim(),
    };
    dispatchAgentCommand(command);
    appendEvent({
      kind: "command",
      status: "sent",
      title: "下载任务已下发",
      message: url.length > 120 ? `${url.slice(0, 120)}...` : url,
    });
    writeJson(res, 200, { code: 0, message: "任务已下发到 Mac 迅雷", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handlePreviewTask(req, res) {
  try {
    const body = await readJsonBody(req);
    const url = String(body.url || "").trim();
    if (!isPreviewableDownloadUrl(url)) {
      writeJson(res, 400, { code: -1, message: "当前链接不需要文件选择预览" });
      return;
    }
    if (onlineAgents().length === 0) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法获取待下载文件" });
      return;
    }
    const command = {
      type: "command",
      command: "previewTask",
      id: commandId(),
      url,
      name: String(body.name || "").trim(),
    };
    dispatchAgentCommand(command);
    appendEvent({
      kind: "preview",
      status: "sent",
      title: "待下载文件请求已下发",
      message: url.length > 120 ? `${url.slice(0, 120)}...` : url,
    });
    writeJson(res, 200, { code: 0, message: "正在获取文件列表", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleConfirmPreviewTask(req, res) {
  try {
    const body = await readJsonBody(req);
    const pendingId = String(body.pendingId || "").trim();
    const selectedFileIds = Array.isArray(body.selectedFileIds) ? body.selectedFileIds : [];
    const windowIndex = Number(body.windowIndex || 0);
    if (!pendingId) {
      writeJson(res, 400, { code: -1, message: "缺少待确认任务标识" });
      return;
    }
    const command = {
      type: "command",
      command: "confirmPreviewTask",
      id: commandId(),
      pendingId,
      selectedFileIds,
      windowIndex,
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法开始下载" });
      return;
    }
    appendEvent({
      kind: "preview",
      status: "sent",
      title: "已下发所选文件下载命令",
      message: `${selectedFileIds.length || 0} 个文件`,
    });
    writeJson(res, 200, { code: 0, message: "已下发下载命令", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleCancelPreviewTask(req, res) {
  try {
    const body = await readJsonBody(req);
    const pendingId = String(body.pendingId || "").trim();
    const windowIndex = Number(body.windowIndex || 0);
    const command = {
      type: "command",
      command: "cancelPreviewTask",
      id: commandId(),
      pendingId,
      windowIndex,
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法取消待下载任务" });
      return;
    }
    appendEvent({
      kind: "preview",
      status: "sent",
      title: "待下载任务取消命令已下发",
      message: pendingId || "current-preview",
    });
    writeJson(res, 200, { code: 0, message: "取消命令已下发", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleMigrateFile(req, res) {
  try {
    const body = await readJsonBody(req);
    const filePath = String(body.filePath || "").trim();
    if (!filePath) {
      writeJson(res, 400, { code: -1, message: "缺少要迁移的文件路径" });
      return;
    }
    const command = {
      type: "command",
      command: "migrateFile",
      id: commandId(),
      filePath,
      name: String(body.name || "").trim(),
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法迁移文件" });
      return;
    }
    appendEvent({
      kind: "migration",
      status: "sent",
      title: "手动迁移已下发",
      message: body.name || filePath,
    });
    writeJson(res, 200, { code: 0, message: "迁移命令已下发", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleCleanupDownloadDir(req, res) {
  try {
    const body = await readJsonBody(req);
    if (body.confirm !== "清空") {
      writeJson(res, 400, { code: -1, message: "确认词错误，必须输入：清空" });
      return;
    }
    const command = {
      type: "command",
      command: "cleanupDownloadDir",
      id: commandId(),
      confirm: body.confirm,
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法清空下载目录" });
      return;
    }
    appendEvent({
      kind: "cleanup",
      status: "sent",
      title: "清空下载目录命令已下发",
      message: "等待 Mac 代理执行",
    });
    writeJson(res, 200, { code: 0, message: "清空命令已下发", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleDeleteFile(req, res) {
  try {
    const body = await readJsonBody(req);
    const filePath = String(body.filePath || "").trim();
    if (!filePath) {
      writeJson(res, 400, { code: -1, message: "缺少要删除的文件路径" });
      return;
    }
    const command = {
      type: "command",
      command: "deleteFile",
      id: commandId(),
      filePath,
      name: String(body.name || "").trim(),
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac 代理不在线，无法删除文件" });
      return;
    }
    appendEvent({
      kind: "delete",
      status: "sent",
      title: "删除文件命令已下发",
      message: body.name || filePath,
    });
    writeJson(res, 200, { code: 0, message: "删除命令已下发", data: { id: command.id } });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

async function handleTaskControl(req, res) {
  try {
    const body = await readJsonBody(req);
    const taskId = String(body.taskId || "").trim();
    const action = String(body.action || "").trim().toLowerCase();
    const name = String(body.name || "").trim();
    const deleteFiles = Boolean(body.deleteFiles);
    if (!taskId) {
      writeJson(res, 400, { code: -1, message: "missing task id" });
      return;
    }
    if (!isSupportedTaskAction(action)) {
      writeJson(res, 400, { code: -1, message: "unsupported task action" });
      return;
    }
    const command = {
      type: "command",
      command: "controlTask",
      id: commandId(),
      taskId,
      action,
      deleteFiles,
      name,
    };
    if (!dispatchAgentCommand(command)) {
      writeJson(res, 503, { code: -1, message: "Mac agent is offline, task command could not be delivered" });
      return;
    }
    appendEvent({
      kind: "task",
      status: "sent",
      title: `task ${action} command sent`,
      message: name || taskId,
    });
    writeJson(res, 200, {
      code: 0,
      message: `task ${action} command sent`,
      data: { id: command.id },
    });
  } catch (error) {
    writeJson(res, 500, { code: -1, message: error.message });
  }
}

function serveStatic(req, res) {
  if (req.method === "POST" && req.url.split("?")[0] === "/api/tasks") {
    handleAddTask(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/tasks/preview") {
    handlePreviewTask(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/tasks/preview/confirm") {
    handleConfirmPreviewTask(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/tasks/preview/cancel") {
    handleCancelPreviewTask(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/migrate-file") {
    handleMigrateFile(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/cleanup-downloads") {
    handleCleanupDownloadDir(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/delete-file") {
    handleDeleteFile(req, res);
    return;
  }
  if (req.method === "POST" && req.url.split("?")[0] === "/api/tasks/control") {
    handleTaskControl(req, res);
    return;
  }

  if (req.url === "/api/state") {
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ...latestState, agentOnline }));
    return;
  }

  const requestPath = req.url.split("?")[0] === "/" ? "/index.html" : req.url.split("?")[0];
  const safePath = path.normalize(decodeURIComponent(requestPath)).replace(/^(\.\.[/\\])+/, "");
  const filePath = path.join(PUBLIC_DIR, safePath);
  if (!filePath.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not Found");
      return;
    }
    res.writeHead(200, {
      "content-type": contentType(filePath),
      "cache-control": "no-store",
    });
    res.end(data);
  });
}

const server = http.createServer(serveStatic);

server.on("upgrade", (req, socket) => {
  if (req.url.split("?")[0] !== "/ws") {
    socket.destroy();
    return;
  }
  const key = req.headers["sec-websocket-key"];
  if (!key) {
    socket.destroy();
    return;
  }
  const accept = crypto
    .createHash("sha1")
    .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
    .digest("base64");
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
  );

  clients.add(socket);
  sendJson(socket, { ...latestState, type: "snapshot", agentOnline });

  let frameBuffer = Buffer.alloc(0);
  socket.on("data", (chunk) => {
    frameBuffer = Buffer.concat([frameBuffer, chunk]);
    const parsed = parseFrames(frameBuffer);
    frameBuffer = parsed.remaining;
    for (const raw of parsed.messages) {
      try {
        const message = JSON.parse(raw);
        if (message.role === "agent") {
          if (message.token !== TOKEN) {
            sendJson(socket, { type: "error", message: "invalid token" });
            socket.destroy();
            return;
          }
          socket.__role = "agent";
          agentSockets.add(socket);
          agentOnline = true;
          if (message.event) {
            appendEvent(message.event);
          }
          if (message.payload) {
            latestState = {
              ...message.payload,
              events: latestState.events || [],
              type: "snapshot",
              agentOnline: true,
              updatedAt: new Date().toISOString(),
            };
            broadcast(latestState);
          }
        }
      } catch (error) {
        sendJson(socket, { type: "error", message: error.message });
      }
    }
  });
  socket.on("close", () => {
    clients.delete(socket);
    if (socket.__role === "agent") {
      agentSockets.delete(socket);
      agentOnline = agentSockets.size > 0;
      broadcast({ ...latestState, type: "snapshot", agentOnline });
    }
  });
  socket.on("error", () => {
    clients.delete(socket);
    if (socket.__role === "agent") {
      agentSockets.delete(socket);
      agentOnline = agentSockets.size > 0;
    }
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`thunder-bridge listening on ${PORT}`);
});
