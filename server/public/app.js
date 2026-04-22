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

function renderTasks(tasks = []) {
  if (!tasks.length) {
    elements.tasks.className = "task-list empty";
    elements.tasks.textContent = "暂无任务数据";
    return;
  }
  elements.tasks.className = "task-list";
  elements.tasks.innerHTML = tasks
    .map((task) => {
      const progress = Math.max(0, Math.min(100, Number(task.progress || 0)));
      const files = Array.isArray(task.files) ? task.files : [];
      const fileList =
        task.status === "completed" && files.length > 1
          ? `
          <div class="file-picker">
            <strong>此任务包含 ${files.length} 个文件，已暂停自动迁移，请手动选择：</strong>
            ${files
              .map(
                (file) => `
                <div class="file-row">
                  <span title="${escapeHtml(file.path || "")}">${escapeHtml(file.name || "未命名文件")}</span>
                  <small>${formatBytes(file.size)}</small>
                  <button
                    type="button"
                    class="migrate-file"
                    data-file-path="${escapeHtml(file.path || "")}"
                    data-file-name="${escapeHtml(file.name || "")}"
                  >迁移此文件</button>
                </div>
              `,
              )
              .join("")}
          </div>
        `
          : "";
      const manualHint =
        task.status === "completed" && task.needsManualMigration
          ? '<span class="manual-hint">多文件任务不会自动迁移</span>'
          : "";
      return `
        <article class="task-card">
          <div class="task-title-row">
            <div>
              <h3>${escapeHtml(task.name || `任务 ${task.id}`)}</h3>
              <p>${escapeHtml(task.path || "")}</p>
            </div>
            <span class="pill ${task.status || "unknown"}">${statusText(task.status)}</span>
          </div>
          <div class="progress-track"><span style="width:${progress}%"></span></div>
          <div class="task-meta">
            <span>${progress.toFixed(1)}%</span>
            <span>${formatBytes(task.downloaded)} / ${formatBytes(task.totalSize)}</span>
            <span>${formatSpeed(task.speed)}</span>
          </div>
          ${manualHint}
          ${fileList}
        </article>
      `;
    })
    .join("");
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
    elements.migrations.className = "migration-list empty";
    elements.migrations.textContent = "暂无迁移记录";
    return;
  }
  elements.migrations.className = "migration-list";
  elements.migrations.innerHTML = records
    .slice(0, 8)
    .map(
      (item) => `
      <article class="migration-item">
        <strong>${escapeHtml(item.name || "未命名资源")}</strong>
        <span>${escapeHtml(item.status || "pending")}</span>
        <small>${escapeHtml(item.message || "")}</small>
      </article>
    `,
    )
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

async function addTask(url) {
  return postJson("/api/tasks", { url });
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

async function migrateFile(filePath, name) {
  return postJson("/api/migrate-file", { filePath, name });
}

async function cleanupDownloads() {
  return postJson("/api/cleanup-downloads", { confirm: "清空" });
}

elements.addForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = elements.taskUrl.value.trim();
  if (!url) {
    elements.addMessage.textContent = "请先粘贴下载链接。";
    elements.addMessage.className = "form-message error";
    return;
  }
  const button = elements.addForm.querySelector("button");
  button.disabled = true;
  elements.addMessage.textContent = "正在下发到 Mac 迅雷...";
  elements.addMessage.className = "form-message";
  try {
    await addTask(url);
    elements.addMessage.textContent = "任务已下发，Mac 迅雷会自动接管。";
    elements.addMessage.className = "form-message success";
    elements.taskUrl.value = "";
  } catch (error) {
    elements.addMessage.textContent = error.message;
    elements.addMessage.className = "form-message error";
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
  button.textContent = "迁移中...";
  try {
    await migrateFile(filePath, fileName);
    button.textContent = "已下发";
  } catch (error) {
    button.disabled = false;
    button.textContent = "迁移此文件";
    alert(error.message);
  }
});

elements.cleanupButton?.addEventListener("click", async () => {
  const first = confirm("这个操作会清空 Mac 当前迅雷下载目录下的所有文件，可能包括正在下载和已下载内容。确定继续吗？");
  if (!first) return;
  const phrase = prompt("请输入“清空”两个字确认删除：");
  if (phrase !== "清空") {
    elements.cleanupMessage.textContent = "确认词不匹配，已取消。";
    elements.cleanupMessage.className = "danger-message error";
    return;
  }
  elements.cleanupButton.disabled = true;
  elements.cleanupMessage.textContent = "清空命令已下发，等待 Mac 执行...";
  elements.cleanupMessage.className = "danger-message";
  try {
    await cleanupDownloads();
    elements.cleanupMessage.textContent = "清空命令已下发，请查看右侧事件结果。";
    elements.cleanupMessage.className = "danger-message success";
  } catch (error) {
    elements.cleanupMessage.textContent = error.message;
    elements.cleanupMessage.className = "danger-message error";
  } finally {
    elements.cleanupButton.disabled = false;
  }
});

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.addEventListener("open", () => {
    elements.connection.textContent = "已连接页面通道";
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
