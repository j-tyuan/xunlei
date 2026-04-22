const elements = {
  dot: document.querySelector("#connectionDot"),
  connection: document.querySelector("#connectionText"),
  speed: document.querySelector("#speedValue"),
  active: document.querySelector("#activeTasks"),
  completed: document.querySelector("#completedTasks"),
  total: document.querySelector("#totalTasks"),
  macState: document.querySelector("#macState"),
  lastUpdate: document.querySelector("#lastUpdate"),
  tasks: document.querySelector("#tasks"),
  migrations: document.querySelector("#migrations"),
  addForm: document.querySelector("#addTaskForm"),
  taskUrl: document.querySelector("#taskUrl"),
  addMessage: document.querySelector("#addTaskMessage"),
  cleanupButton: document.querySelector("#cleanupDownloads"),
  cleanupMessage: document.querySelector("#cleanupMessage"),
};

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value.toFixed(0)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unit]}`;
}

function formatSpeed(bytes) {
  return `${formatBytes(bytes)}/s`;
}

function formatTime(value) {
  if (!value) return "等待数据";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "等待数据";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function statusText(status) {
  const map = {
    downloading: "下载中",
    completed: "已完成",
    waiting: "等待中",
    paused: "已暂停",
    failed: "失败",
    unknown: "未知",
  };
  return map[status] || status || "未知";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTasks(tasks = []) {
  if (!tasks.length) {
    elements.tasks.className = "task-list is-empty";
    elements.tasks.textContent = "暂无任务数据";
    return;
  }

  elements.tasks.className = "task-list";
  elements.tasks.innerHTML = tasks.map(renderTaskRow).join("");
}

function renderTaskRow(task) {
  const progress = Math.max(0, Math.min(100, Number(task.progress || 0)));
  const files = Array.isArray(task.files) ? task.files : [];
  const shouldChooseFile = task.status === "completed" && files.length > 1;
  const fileList = shouldChooseFile ? renderFileList(files) : "";
  const manualHint = shouldChooseFile
    ? '<span class="inline-warning">多文件任务已暂停自动迁移</span>'
    : "";

  return `
    <article class="task-row">
      <div class="task-main">
        <div class="task-name">
          <h3>${escapeHtml(task.name || `任务 ${task.id}`)}</h3>
          <p>${escapeHtml(task.path || "")}</p>
        </div>
        <span class="status-pill ${task.status || "unknown"}">${statusText(task.status)}</span>
      </div>
      <div class="progress-track" aria-label="下载进度">
        <span style="width:${progress}%"></span>
      </div>
      <div class="task-meta">
        <span>${progress.toFixed(1)}%</span>
        <span>${formatBytes(task.downloaded)} / ${formatBytes(task.totalSize)}</span>
        <span>${formatSpeed(task.speed)}</span>
        ${manualHint}
      </div>
      ${fileList}
    </article>
  `;
}

function renderFileList(files) {
  return `
    <div class="file-list">
      <div class="file-list-head">
        <span>选择要迁移的文件</span>
        <small>${files.length} 个文件</small>
      </div>
      ${files.map(renderFileLine).join("")}
    </div>
  `;
}

function renderFileLine(file) {
  return `
    <div class="file-line">
      <span title="${escapeHtml(file.path || "")}">${escapeHtml(file.name || "未命名文件")}</span>
      <small>${formatBytes(file.size)}</small>
      <button
        type="button"
        class="migrate-file"
        data-file-path="${escapeHtml(file.path || "")}"
        data-file-name="${escapeHtml(file.name || "")}"
      >迁移</button>
    </div>
  `;
}

function renderMigrations(migrations = [], events = []) {
  const records = [
    ...events.map((event) => ({
      name: event.title || "系统事件",
      status: event.status || event.kind || "event",
      message: event.message || "",
    })),
    ...migrations,
  ];

  if (!records.length) {
    elements.migrations.className = "timeline is-empty";
    elements.migrations.textContent = "暂无迁移记录";
    return;
  }

  elements.migrations.className = "timeline";
  elements.migrations.innerHTML = records.slice(0, 10).map(renderTimelineItem).join("");
}

function renderTimelineItem(item) {
  return `
    <article class="timeline-item">
      <span class="timeline-state">${escapeHtml(item.status || "pending")}</span>
      <strong>${escapeHtml(item.name || "未命名事件")}</strong>
      <small>${escapeHtml(item.message || "")}</small>
    </article>
  `;
}

function render(state) {
  const stats = state.stats || {};
  const mac = state.mac || {};
  elements.dot.classList.toggle("online", Boolean(state.agentOnline));
  elements.connection.textContent = state.agentOnline ? "Mac 代理在线" : "Mac 代理离线";
  elements.speed.textContent = formatSpeed(stats.totalSpeed);
  elements.active.textContent = stats.activeTasks ?? 0;
  elements.completed.textContent = stats.completedTasks ?? 0;
  elements.total.textContent = stats.totalTasks ?? 0;
  elements.macState.textContent = mac.thunderRunning ? "迅雷运行中" : "迅雷未运行";
  elements.lastUpdate.textContent = `更新于 ${formatTime(state.updatedAt || mac.collectedAt)}`;
  renderTasks(state.tasks || []);
  renderMigrations(state.migrations || [], state.events || []);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok || result.code !== 0) {
    throw new Error(result.message || "操作失败");
  }
  return result;
}

function setMessage(element, text, type = "") {
  element.textContent = text;
  element.className = type ? `${element.className.split(" ")[0]} ${type}` : element.className.split(" ")[0];
}

elements.addForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = elements.taskUrl.value.trim();
  if (!url) {
    setMessage(elements.addMessage, "请先粘贴下载链接。", "error");
    return;
  }

  const button = elements.addForm.querySelector("button");
  button.disabled = true;
  setMessage(elements.addMessage, "正在下发到 Mac 迅雷...");
  try {
    await postJson("/api/tasks", { url });
    setMessage(elements.addMessage, "任务已下发，Mac 迅雷会自动接管。", "success");
    elements.taskUrl.value = "";
  } catch (error) {
    setMessage(elements.addMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

elements.tasks?.addEventListener("click", async (event) => {
  const button = event.target.closest(".migrate-file");
  if (!button) return;

  const filePath = button.dataset.filePath || "";
  const fileName = button.dataset.fileName || "";
  if (!filePath) return;
  if (!confirm(`确认迁移这个文件到 18 服务器？\n\n${fileName}`)) return;

  button.disabled = true;
  button.textContent = "迁移中";
  try {
    await postJson("/api/migrate-file", { filePath, name: fileName });
    button.textContent = "已下发";
  } catch (error) {
    button.disabled = false;
    button.textContent = "迁移";
    alert(error.message);
  }
});

elements.cleanupButton?.addEventListener("click", async () => {
  const first = confirm("这个操作会清空 Mac 当前迅雷下载目录下的所有文件，可能包括正在下载和已下载内容。确定继续吗？");
  if (!first) return;

  const phrase = prompt("请输入“清空”两个字确认删除：");
  if (phrase !== "清空") {
    setMessage(elements.cleanupMessage, "确认词不匹配，已取消。", "error");
    return;
  }

  elements.cleanupButton.disabled = true;
  setMessage(elements.cleanupMessage, "清空命令已下发，等待 Mac 执行...");
  try {
    await postJson("/api/cleanup-downloads", { confirm: "清空" });
    setMessage(elements.cleanupMessage, "清空命令已下发，请查看右侧事件结果。", "success");
  } catch (error) {
    setMessage(elements.cleanupMessage, error.message, "error");
  } finally {
    elements.cleanupButton.disabled = false;
  }
});

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.addEventListener("open", () => {
    elements.connection.textContent = "页面通道已连接";
  });
  socket.addEventListener("message", (event) => {
    try {
      render(JSON.parse(event.data));
    } catch (error) {
      console.warn(error);
    }
  });
  socket.addEventListener("close", () => {
    elements.dot.classList.remove("online");
    elements.connection.textContent = "页面通道断开，重连中";
    setTimeout(connect, 1500);
  });
}

fetch("/api/state")
  .then((res) => res.json())
  .then(render)
  .catch(() => {});
connect();
