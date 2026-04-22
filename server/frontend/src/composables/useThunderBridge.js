import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

export const bridgeKey = Symbol("thunder-bridge");

const PENDING_DIALOG_BASE_TOP = 108;
const PENDING_DIALOG_TOP_STEP = 26;
const PENDING_DIALOG_BASE_RIGHT = 24;
const PENDING_DIALOG_RIGHT_STEP = 12;
const PENDING_DIALOG_BASE_Z = 60;
const PENDING_DIALOG_WIDTH = 560;
const PENDING_DIALOG_MIN_LEFT = 12;
const PENDING_DIALOG_MIN_TOP = 18;
const PENDING_DIALOG_VIEWPORT_GUTTER = 12;

const COMMAND_SUCCESS_STATUSES = new Set(["accepted", "ready", "cancelled", "done"]);
const COMMAND_FAILURE_STATUSES = new Set(["failed", "error"]);
const GLOBAL_LOADING_MIN_MS = 420;
const DEFAULT_CLEANUP_MESSAGE = "危险操作会清空 Mac 当前迅雷下载目录，需要二次确认。";

function createEmptyState() {
  return {
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
}

function getPendingDialogsFromState(state) {
  if (Array.isArray(state?.pendingDialogs)) return state.pendingDialogs;
  return state?.pendingDialog?.id ? [state.pendingDialog] : [];
}

function trimMessage(value, maxLength = 220) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1))}…`;
}

function normalizeCommandStatus(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function isCommandEventSuccess(status) {
  return COMMAND_SUCCESS_STATUSES.has(normalizeCommandStatus(status));
}

function isCommandEventFailure(status) {
  return COMMAND_FAILURE_STATUSES.has(normalizeCommandStatus(status));
}

function buildEventMessage(event, fallback = "") {
  const title = trimMessage(event?.title || "");
  const message = trimMessage(event?.message || "");
  if (title && message) return trimMessage(`${title}：${message}`);
  return title || message || fallback;
}

function buildCommandError(error, fallback = "操作失败") {
  const message = buildEventMessage(error?.event, error?.message || fallback) || fallback;
  const wrapped = error instanceof Error ? error : new Error(message);
  wrapped.message = message;
  if (error?.event && !wrapped.event) {
    wrapped.event = error.event;
  }
  return wrapped;
}

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

function formatLogTime(value) {
  if (!value) return "--:--:--";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleString("zh-CN", { hour12: false });
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

function statusTagType(status) {
  const map = {
    downloading: "success",
    completed: "success",
    waiting: "warning",
    paused: "info",
    failed: "danger",
    unknown: "info",
  };
  return map[status] || "info";
}

function createModeText(mode, fallbackToUi) {
  if (mode === "direct") return fallbackToUi ? "直连创建 / 可回退" : "直连创建";
  if (mode === "open-url") return "打开链接";
  return fallbackToUi ? `${mode || "未知"} / 可回退` : mode || "未知";
}

function isPreviewableUrl(value) {
  return /^(magnet:\?|ed2k:\/\/|thunder:\/\/)/i.test(String(value || "").trim());
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function waitForNextPaint() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

function buildLogLines(state) {
  const stats = state?.stats || {};
  const mac = state?.mac || {};
  const tasks = state?.tasks || [];
  const events = state?.events || [];
  const migrations = state?.migrations || [];
  const pendingDialogs = getPendingDialogsFromState(state);
  const lines = [];

  lines.push("Thunder Bridge Console");
  lines.push("=".repeat(72));
  lines.push(`[agent] online=${Boolean(state?.agentOnline)} thunder=${Boolean(mac.thunderRunning)} host=${mac.host || "unknown"}`);
  lines.push(`[create] mode=${mac.taskCreateMode || "unknown"} fallback=${Boolean(mac.taskCreateFallbackToUi)}`);
  lines.push(`[stats] speed=${formatSpeed(stats.totalSpeed)} active=${stats.activeTasks ?? 0} completed=${stats.completedTasks ?? 0} total=${stats.totalTasks ?? 0}`);
  lines.push(`[time ] updated=${formatLogTime(state?.updatedAt || mac.collectedAt)}`);
  if (pendingDialogs.length) {
    lines.push(`[preview] dialogs=${pendingDialogs.length}`);
    for (const pending of pendingDialogs) {
      lines.push(`         #${pending.windowIndex || "?"} ${pending.name || pending.id} ${pending.selectedCount ?? 0}/${pending.fileCount ?? 0}`);
    }
  }

  lines.push("");
  lines.push("# Tasks");
  if (!tasks.length) {
    lines.push("  (empty)");
  } else {
    for (const task of tasks) {
      lines.push(`  [${statusText(task.status)}] ${task.name || `任务 ${task.id}`}`);
      lines.push(`      progress=${Number(task.progress || 0).toFixed(1)}% speed=${formatSpeed(task.speed)} path=${task.path || "-"}`);
      const files = Array.isArray(task.files) ? task.files.filter((file) => file.exists !== false) : [];
      for (const file of files) {
        lines.push(`      file ${formatBytes(file.size).padStart(9, " ")}  ${file.name}`);
      }
    }
  }

  lines.push("");
  lines.push("# Events");
  if (!events.length) {
    lines.push("  (empty)");
  } else {
    for (const event of events.slice(0, 30)) {
      lines.push(`  ${formatLogTime(event.time)}  [${event.status || event.kind || "event"}] ${event.title || "系统事件"}`);
      if (event.message) lines.push(`      ${event.message}`);
    }
  }

  lines.push("");
  lines.push("# Migrations");
  if (!migrations.length) {
    lines.push("  (empty)");
  } else {
    for (const item of migrations.slice(0, 30)) {
      lines.push(`  ${formatLogTime(item.time)}  [${item.status || "pending"}] ${item.name || "未命名资源"}`);
      if (item.message) lines.push(`      ${item.message}`);
    }
  }

  return lines.join("\n");
}

export function useThunderBridge() {
  const latestState = ref(createEmptyState());
  const pageChannelOpen = ref(false);
  const taskUrl = ref("");
  const addBusy = ref(false);
  const cleanupBusy = ref(false);
  const logDialogVisible = ref(false);
  const addMessage = reactive({ text: "", type: "" });
  const cleanupMessage = reactive({ text: DEFAULT_CLEANUP_MESSAGE, type: "" });
  const globalLoading = reactive({ visible: false, text: "正在处理，请稍候..." });
  const pendingSelections = reactive({});
  const pendingUi = reactive({});
  const pendingLayouts = reactive({});
  const taskUi = reactive({});
  const fileUi = reactive({});
  const progressGradient = ["#1688ff", "#43dcff", "#33e6a1"];
  const pendingDragState = reactive({
    active: false,
    pendingId: "",
    pointerId: null,
    originLeft: 0,
    originTop: 0,
    startClientX: 0,
    startClientY: 0,
  });

  const commandWaiters = new Map();
  const globalOperations = new Map();
  let nextOperationId = 1;
  let nextPendingZ = PENDING_DIALOG_BASE_Z;
  let refreshStatePromise = null;
  let socket = null;
  let reconnectTimer = null;

  const stats = computed(() => latestState.value.stats || {});
  const mac = computed(() => latestState.value.mac || {});
  const tasks = computed(() => latestState.value.tasks || []);
  const pendingDialogs = computed(() => getPendingDialogsFromState(latestState.value));
  const speedText = computed(() => formatSpeed(stats.value.totalSpeed));
  const macStateText = computed(() => (mac.value.thunderRunning ? "迅雷运行中" : "迅雷未运行"));
  const taskCreateModeText = computed(() => createModeText(mac.value.taskCreateMode, mac.value.taskCreateFallbackToUi));
  const lastUpdateText = computed(() => formatTime(latestState.value.updatedAt || mac.value.collectedAt));
  const connectionTone = computed(() => {
    if (pageChannelOpen.value && latestState.value.agentOnline) return "success";
    if (pageChannelOpen.value) return "warning";
    return "danger";
  });
  const connectionLabel = computed(() => {
    if (!pageChannelOpen.value) return "页面通道断开，重连中";
    return latestState.value.agentOnline ? "Mac 代理在线" : "Mac 代理离线";
  });
  const logText = computed(() => buildLogLines(latestState.value));

  function setInlineMessage(target, text, type = "") {
    target.text = text;
    target.type = type;
  }

  function ensurePendingState(pending) {
    if (!pending?.id) return;
    if (!pendingSelections[pending.id]) {
      pendingSelections[pending.id] = (pending.files || [])
        .filter((file) => file.checked)
        .map((file) => String(file.id));
    }
    if (!pendingUi[pending.id]) {
      pendingUi[pending.id] = { message: "", type: "", busy: false };
    }
  }

  function getViewportWidth() {
    return typeof window === "undefined" ? 1440 : Math.max(360, window.innerWidth || 1440);
  }

  function getViewportHeight() {
    return typeof window === "undefined" ? 900 : Math.max(320, window.innerHeight || 900);
  }

  function getPendingDialogWidth(viewportWidth = getViewportWidth()) {
    return Math.min(PENDING_DIALOG_WIDTH, Math.max(320, viewportWidth - PENDING_DIALOG_VIEWPORT_GUTTER * 2));
  }

  function clampPendingLayout(layout) {
    const viewportWidth = getViewportWidth();
    const viewportHeight = getViewportHeight();
    const dialogWidth = getPendingDialogWidth(viewportWidth);
    const maxLeft = Math.max(PENDING_DIALOG_MIN_LEFT, viewportWidth - dialogWidth - PENDING_DIALOG_VIEWPORT_GUTTER);
    const maxTop = Math.max(PENDING_DIALOG_MIN_TOP, viewportHeight - 120);

    return {
      left: Math.min(maxLeft, Math.max(PENDING_DIALOG_MIN_LEFT, Number(layout?.left ?? 0))),
      top: Math.min(maxTop, Math.max(PENDING_DIALOG_MIN_TOP, Number(layout?.top ?? 0))),
      zIndex: Number(layout?.zIndex || PENDING_DIALOG_BASE_Z),
    };
  }

  function createDefaultPendingLayout(index = 0) {
    const viewportWidth = getViewportWidth();
    const dialogWidth = getPendingDialogWidth(viewportWidth);
    const left = viewportWidth - dialogWidth - PENDING_DIALOG_BASE_RIGHT - index * PENDING_DIALOG_RIGHT_STEP;
    const top = PENDING_DIALOG_BASE_TOP + index * PENDING_DIALOG_TOP_STEP;

    nextPendingZ += 1;
    return clampPendingLayout({
      left,
      top,
      zIndex: nextPendingZ,
    });
  }

  function ensurePendingLayout(pending, index = 0) {
    if (!pending?.id) {
      return createDefaultPendingLayout(index);
    }
    if (!pendingLayouts[pending.id]) {
      pendingLayouts[pending.id] = createDefaultPendingLayout(index);
    } else {
      pendingLayouts[pending.id] = clampPendingLayout(pendingLayouts[pending.id]);
    }
    return pendingLayouts[pending.id];
  }

  function prunePendingState(dialogs) {
    const ids = new Set((dialogs || []).map((item) => item.id));
    for (const id of Object.keys(pendingSelections)) {
      if (!ids.has(id)) delete pendingSelections[id];
    }
    for (const id of Object.keys(pendingUi)) {
      if (!ids.has(id)) delete pendingUi[id];
    }
    for (const id of Object.keys(pendingLayouts)) {
      if (!ids.has(id)) delete pendingLayouts[id];
    }
    if (pendingDragState.pendingId && !ids.has(pendingDragState.pendingId)) {
      pendingDragState.active = false;
      pendingDragState.pendingId = "";
      pendingDragState.pointerId = null;
    }
  }

  function pruneTaskState(nextTasks) {
    const ids = new Set((nextTasks || []).map((item) => String(item.id || "")));
    for (const id of Object.keys(taskUi)) {
      if (!ids.has(id)) delete taskUi[id];
    }

    const filePaths = new Set();
    for (const task of nextTasks || []) {
      for (const file of Array.isArray(task.files) ? task.files : []) {
        if (file.exists !== false && file.path) {
          filePaths.add(file.path);
        }
      }
    }
    for (const path of Object.keys(fileUi)) {
      if (!filePaths.has(path)) delete fileUi[path];
    }
  }

  watch(
    pendingDialogs,
    (dialogs) => {
      dialogs.forEach((pending, index) => {
        ensurePendingState(pending);
        ensurePendingLayout(pending, index);
      });
      prunePendingState(dialogs);
    },
    { immediate: true },
  );

  watch(
    tasks,
    (nextTasks) => {
      pruneTaskState(nextTasks);
    },
    { immediate: true },
  );

  function syncGlobalLoading() {
    const operations = [...globalOperations.values()];
    if (!operations.length) {
      globalLoading.visible = false;
      globalLoading.text = "正在处理，请稍候...";
      return;
    }

    const current = operations[operations.length - 1];
    const extraCount = Math.max(0, operations.length - 1);
    globalLoading.visible = true;
    globalLoading.text = `${current.text || "正在等待反馈..."}${extraCount ? `（另有 ${extraCount} 项操作进行中）` : ""}`;
  }

  function beginGlobalOperation(text) {
    const id = `global-op-${Date.now()}-${nextOperationId++}`;
    globalOperations.set(id, { text: text || "正在处理...", startedAt: Date.now() });
    syncGlobalLoading();
    return id;
  }

  function updateGlobalOperation(id, text) {
    if (!globalOperations.has(id)) return;
    const current = globalOperations.get(id);
    globalOperations.set(id, { ...current, text: text || "正在等待反馈..." });
    syncGlobalLoading();
  }

  async function finishGlobalOperation(id) {
    if (!globalOperations.has(id)) return;
    const current = globalOperations.get(id);
    const elapsed = Date.now() - Number(current?.startedAt || Date.now());
    if (elapsed < GLOBAL_LOADING_MIN_MS) {
      await wait(GLOBAL_LOADING_MIN_MS - elapsed);
    }
    globalOperations.delete(id);
    syncGlobalLoading();
  }

  function showSuccess(message) {
    ElMessage({
      message,
      type: "success",
      duration: 2400,
      grouping: true,
    });
  }

  function showError(message) {
    ElMessage({
      message,
      type: "error",
      duration: 4200,
      showClose: true,
      grouping: true,
    });
  }

  function findFinalCommandEvent(commandId, events = latestState.value.events || []) {
    return (events || []).find((event) => {
      if (String(event?.commandId || "") !== String(commandId || "")) return false;
      return isCommandEventSuccess(event.status) || isCommandEventFailure(event.status);
    });
  }

  function settleCommandWaiters(events = latestState.value.events || []) {
    if (!commandWaiters.size) return;

    for (const [commandId, waiter] of commandWaiters.entries()) {
      const event = findFinalCommandEvent(commandId, events);
      if (!event) continue;

      window.clearTimeout(waiter.timeoutId);
      window.clearInterval(waiter.intervalId);
      commandWaiters.delete(commandId);

      if (isCommandEventFailure(event.status)) {
        const error = new Error(buildEventMessage(event, "命令执行失败"));
        error.event = event;
        waiter.reject(error);
        continue;
      }

      waiter.resolve(event);
    }
  }

  function applyState(state) {
    latestState.value = state;
    settleCommandWaiters(state.events || []);
  }

  async function refreshState() {
    if (refreshStatePromise) return refreshStatePromise;

    refreshStatePromise = fetch("/api/state")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("获取状态失败");
        }
        return response.json();
      })
      .then((state) => {
        applyState(state);
        return state;
      })
      .finally(() => {
        refreshStatePromise = null;
      });

    return refreshStatePromise;
  }

  function waitForCommandFeedback(commandId, { timeoutMs = 30000, pollIntervalMs = 500 } = {}) {
    const existing = findFinalCommandEvent(commandId);
    if (existing) {
      if (isCommandEventFailure(existing.status)) {
        const error = new Error(buildEventMessage(existing, "命令执行失败"));
        error.event = existing;
        return Promise.reject(error);
      }
      return Promise.resolve(existing);
    }

    return new Promise((resolve, reject) => {
      const normalizedId = String(commandId || "");
      const timeoutId = window.setTimeout(() => {
        window.clearInterval(intervalId);
        commandWaiters.delete(normalizedId);
        reject(new Error("等待 Mac 反馈超时"));
      }, timeoutMs);
      const intervalId = window.setInterval(() => {
        refreshState().catch(() => {});
      }, pollIntervalMs);

      commandWaiters.set(normalizedId, {
        resolve,
        reject,
        timeoutId,
        intervalId,
      });
    });
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

  async function dispatchCommandWithFeedback({
    request,
    loadingText,
    waitingText,
    timeoutMs = 30000,
    onSuccess,
    onError,
    successMessage,
    errorMessage,
  }) {
    const operationId = beginGlobalOperation(loadingText);
    try {
      await waitForNextPaint();
      const result = await request();
      const commandId = result?.data?.id;
      if (!commandId) {
        throw new Error("命令已下发，但未返回 commandId");
      }

      updateGlobalOperation(operationId, waitingText || "正在等待 Mac 反馈...");
      const finalEvent = await waitForCommandFeedback(commandId, { timeoutMs });
      await onSuccess?.(finalEvent, result);
      showSuccess(successMessage || buildEventMessage(finalEvent, "操作已完成"));
      refreshState().catch(() => {});
      return finalEvent;
    } catch (error) {
      const wrapped = buildCommandError(error, errorMessage || "操作失败");
      await onError?.(wrapped);
      showError(errorMessage || wrapped.message);
      throw wrapped;
    } finally {
      await finishGlobalOperation(operationId);
    }
  }

  function getPendingUi(id) {
    return pendingUi[id] || { message: "", type: "", busy: false };
  }

  function setPendingUi(id, nextState) {
    pendingUi[id] = {
      message: "",
      type: "",
      busy: false,
      ...(pendingUi[id] || {}),
      ...nextState,
    };
  }

  function getTaskUi(id) {
    return taskUi[String(id || "")] || { busy: false, action: "", message: "", type: "" };
  }

  function setTaskUi(id, nextState) {
    taskUi[String(id || "")] = {
      busy: false,
      action: "",
      message: "",
      type: "",
      ...(taskUi[String(id || "")] || {}),
      ...nextState,
    };
  }

  function getFileUi(path) {
    return fileUi[path] || { busy: false, action: "", label: "", done: false };
  }

  function setFileUi(path, nextState) {
    fileUi[path] = {
      busy: false,
      action: "",
      label: "",
      done: false,
      ...(fileUi[path] || {}),
      ...nextState,
    };
  }

  function selectedPendingIds(id) {
    return [...(pendingSelections[id] || [])];
  }

  function pendingSelectedCount(pending) {
    return selectedPendingIds(pending.id).length;
  }

  function isPendingSelected(pendingId, fileId) {
    return selectedPendingIds(pendingId).includes(String(fileId));
  }

  function togglePendingSelection(pendingId, fileId, checked) {
    const current = new Set(selectedPendingIds(pendingId));
    const normalizedId = String(fileId);
    if (checked) current.add(normalizedId);
    else current.delete(normalizedId);
    pendingSelections[pendingId] = [...current];
  }

  function selectAllPending(pending) {
    pendingSelections[pending.id] = (pending.files || []).map((file) => String(file.id));
  }

  function clearPendingSelection(pendingId) {
    pendingSelections[pendingId] = [];
  }

  function pendingDialogStyle(pending, index) {
    const layout = pendingLayouts[pending?.id] || clampPendingLayout(createDefaultPendingLayout(index));
    return {
      left: `${layout.left}px`,
      top: `${layout.top}px`,
      zIndex: `${layout.zIndex}`,
    };
  }

  function bringPendingToFront(pendingId) {
    if (!pendingId || !pendingLayouts[pendingId]) return;
    nextPendingZ += 1;
    pendingLayouts[pendingId] = {
      ...pendingLayouts[pendingId],
      zIndex: nextPendingZ,
    };
  }

  function startPendingDrag(pending, index, event) {
    if (!pending?.id || event?.button !== 0) return;

    const layout = ensurePendingLayout(pending, index);
    bringPendingToFront(pending.id);

    pendingDragState.active = true;
    pendingDragState.pendingId = pending.id;
    pendingDragState.pointerId = event.pointerId;
    pendingDragState.originLeft = layout.left;
    pendingDragState.originTop = layout.top;
    pendingDragState.startClientX = event.clientX;
    pendingDragState.startClientY = event.clientY;

    event.preventDefault();
  }

  function updatePendingDrag(event) {
    if (!pendingDragState.active) return;
    if (pendingDragState.pointerId !== null && event.pointerId !== pendingDragState.pointerId) return;

    const pendingId = pendingDragState.pendingId;
    if (!pendingId || !pendingLayouts[pendingId]) return;

    const nextLeft = pendingDragState.originLeft + (event.clientX - pendingDragState.startClientX);
    const nextTop = pendingDragState.originTop + (event.clientY - pendingDragState.startClientY);

    pendingLayouts[pendingId] = clampPendingLayout({
      ...pendingLayouts[pendingId],
      left: nextLeft,
      top: nextTop,
    });
  }

  function stopPendingDrag(event) {
    if (!pendingDragState.active) return;
    if (event && pendingDragState.pointerId !== null && event.pointerId !== pendingDragState.pointerId) return;

    pendingDragState.active = false;
    pendingDragState.pendingId = "";
    pendingDragState.pointerId = null;
  }

  function handleViewportResize() {
    for (const id of Object.keys(pendingLayouts)) {
      pendingLayouts[id] = clampPendingLayout(pendingLayouts[id]);
    }
  }

  function removePendingDialogLocally(pendingId, windowIndex = 0) {
    const dialogs = getPendingDialogsFromState(latestState.value).filter(
      (item) => item.id !== pendingId && Number(item.windowIndex || 0) !== Number(windowIndex || 0),
    );
    latestState.value = {
      ...latestState.value,
      pendingDialogs: dialogs,
      pendingDialog: dialogs[0] || null,
    };
    delete pendingSelections[pendingId];
    delete pendingUi[pendingId];
  }

  function removeTaskLocally(taskId) {
    const nextTasks = tasks.value.filter((task) => String(task.id || "") !== String(taskId || ""));
    latestState.value = {
      ...latestState.value,
      tasks: nextTasks,
      stats: {
        ...(latestState.value.stats || {}),
        totalSpeed: nextTasks.reduce((sum, task) => sum + Number(task?.speed || 0), 0),
        activeTasks: nextTasks.filter((task) => task?.status === "downloading").length,
        completedTasks: nextTasks.filter((task) => task?.status === "completed").length,
        totalTasks: nextTasks.length,
      },
    };
  }

  function removeFileLocally(filePath) {
    const nextTasks = tasks.value.map((task) => ({
      ...task,
      files: Array.isArray(task.files) ? task.files.filter((file) => file.path !== filePath) : task.files,
    }));
    latestState.value = {
      ...latestState.value,
      tasks: nextTasks,
    };
  }

  function taskProgress(task) {
    return Math.max(0, Math.min(100, Number(task?.progress || 0)));
  }

  function visibleTaskFiles(task) {
    return Array.isArray(task?.files) ? task.files.filter((file) => file.exists !== false) : [];
  }

  function manualTaskFiles(task) {
    if (task?.status !== "completed" || !task?.needsManualMigration) return [];
    return visibleTaskFiles(task);
  }

  function taskActionLabel(action, busy = false) {
    const map = {
      start: busy ? "启动中" : "开始",
      pause: busy ? "暂停中" : "暂停",
      delete: busy ? "删除中" : "删除",
    };
    return map[action] || action;
  }

  function canStartTask(task) {
    return ["paused", "failed"].includes(task?.status);
  }

  function canPauseTask(task) {
    return ["downloading", "waiting"].includes(task?.status);
  }

  function canDeleteTask(task) {
    return Boolean(task?.id);
  }

  async function submitTask() {
    const url = String(taskUrl.value || "").trim();
    if (!url) {
      setInlineMessage(addMessage, "请先粘贴下载链接。", "error");
      return;
    }

    addBusy.value = true;
    const previewMode = isPreviewableUrl(url);
    setInlineMessage(addMessage, previewMode ? "正在下发文件预览请求..." : "正在下发创建下载任务...");
    try {
      const successText = previewMode
        ? "待下载文件列表已返回，请在右上角弹窗中勾选后再下载。"
        : "下载任务已由 Mac 迅雷接管。";

      await dispatchCommandWithFeedback({
        request: () => postJson(previewMode ? "/api/tasks/preview" : "/api/tasks", { url }),
        loadingText: previewMode ? "正在下发文件预览请求..." : "正在下发创建下载任务...",
        waitingText: previewMode ? "等待 Mac 返回文件列表..." : "等待 Mac 迅雷接管任务...",
        timeoutMs: previewMode ? 40000 : 30000,
        onSuccess: (finalEvent) => {
          setInlineMessage(addMessage, buildEventMessage(finalEvent, successText), "success");
          taskUrl.value = "";
        },
        onError: (error) => {
          setInlineMessage(addMessage, error.message, "error");
        },
        successMessage: successText,
      });
    } catch (error) {
      setInlineMessage(addMessage, buildCommandError(error).message, "error");
    } finally {
      addBusy.value = false;
    }
  }

  async function confirmPending(pending) {
    setPendingUi(pending.id, { message: "正在按当前勾选开始下载...", type: "", busy: true });
    try {
      await dispatchCommandWithFeedback({
        request: () =>
          postJson("/api/tasks/preview/confirm", {
            pendingId: pending.id,
            selectedFileIds: selectedPendingIds(pending.id),
            windowIndex: pending.windowIndex || 0,
          }),
        loadingText: "正在下发所选文件的下载命令...",
        waitingText: "等待 Mac 迅雷确认勾选后的下载任务...",
        timeoutMs: 30000,
        onSuccess: (finalEvent) => {
          setPendingUi(pending.id, {
            message: buildEventMessage(finalEvent, "所选文件已开始下载。"),
            type: "success",
            busy: true,
          });
          removePendingDialogLocally(pending.id, pending.windowIndex || 0);
        },
        onError: (error) => {
          setPendingUi(pending.id, { message: error.message, type: "error", busy: false });
        },
        successMessage: "所选文件已开始下载。",
      });
    } catch (error) {
      setPendingUi(pending.id, { message: buildCommandError(error).message, type: "error", busy: false });
    }
  }

  async function cancelPending(pending) {
    setPendingUi(pending.id, { message: "正在取消待下载任务...", type: "", busy: true });
    try {
      await dispatchCommandWithFeedback({
        request: () =>
          postJson("/api/tasks/preview/cancel", {
            pendingId: pending.id,
            windowIndex: pending.windowIndex || 0,
          }),
        loadingText: "正在下发取消预览任务命令...",
        waitingText: "等待 Mac 关闭当前待下载弹窗...",
        timeoutMs: 30000,
        onSuccess: (finalEvent) => {
          setPendingUi(pending.id, {
            message: buildEventMessage(finalEvent, "待下载任务已取消。"),
            type: "success",
            busy: true,
          });
          removePendingDialogLocally(pending.id, pending.windowIndex || 0);
        },
        onError: (error) => {
          setPendingUi(pending.id, { message: error.message, type: "error", busy: false });
        },
        successMessage: "待下载任务已取消。",
      });
    } catch (error) {
      setPendingUi(pending.id, { message: buildCommandError(error).message, type: "error", busy: false });
    }
  }

  async function controlTask(task, action) {
    const taskId = String(task?.id || "");
    if (!taskId || !action) return;

    let deleteFiles = false;
    if (action === "delete") {
      try {
        await ElMessageBox.confirm(`确认删除迅雷任务？\n\n${task?.name || taskId}`, "删除任务", {
          type: "warning",
          confirmButtonText: "继续删除",
          cancelButtonText: "取消",
        });
      } catch {
        return;
      }

      if (visibleTaskFiles(task).length) {
        try {
          await ElMessageBox.confirm(
            "是否同时删除 Mac 本地已下载文件？",
            "本地文件处理",
            {
              type: "warning",
              confirmButtonText: "删除任务和文件",
              cancelButtonText: "仅删除任务",
            },
          );
          deleteFiles = true;
        } catch {
          deleteFiles = false;
        }
      }
    }

    const pendingMessages = {
      start: "正在下发开始任务命令...",
      pause: "正在暂停任务...",
      delete: "正在下发删除任务命令...",
    };
    const successMessages = {
      start: "任务已开始。",
      pause: "任务已暂停。",
      delete: deleteFiles ? "任务和本地文件已删除。" : "任务已删除。",
    };
    const waitingMessages = {
      start: "等待 Mac 迅雷反馈开始结果...",
      pause: "等待 Mac 迅雷反馈暂停结果...",
      delete: "等待 Mac 迅雷反馈删除结果...",
    };

    setTaskUi(taskId, {
      busy: true,
      action,
      message: pendingMessages[action] || "正在处理任务...",
      type: "",
    });

    try {
      await dispatchCommandWithFeedback({
        request: () =>
          postJson("/api/tasks/control", {
            taskId,
            action,
            name: task?.name || "",
            deleteFiles,
          }),
        loadingText: pendingMessages[action] || "正在处理任务...",
        waitingText: waitingMessages[action] || "等待 Mac 迅雷反馈任务状态...",
        timeoutMs: 30000,
        onSuccess: (finalEvent) => {
          if (action === "delete") {
            delete taskUi[taskId];
            removeTaskLocally(taskId);
          } else {
            setTaskUi(taskId, {
              busy: false,
              action: "",
              message: buildEventMessage(finalEvent, successMessages[action] || "任务操作已完成。"),
              type: "success",
            });
          }
        },
        onError: (error) => {
          setTaskUi(taskId, {
            busy: false,
            action: "",
            message: error.message,
            type: "error",
          });
        },
        successMessage: successMessages[action] || "任务操作已完成。",
      });
    } catch (error) {
      setTaskUi(taskId, {
        busy: false,
        action: "",
        message: buildCommandError(error).message,
        type: "error",
      });
    }
  }

  async function fileAction(file, action) {
    if (!file?.path) return;

    try {
      await ElMessageBox.confirm(
        action === "migrate"
          ? `确认迁移这个文件到 18 服务器？\n\n${file.name || file.path}`
          : `确认删除 Mac 本地这个文件？\n\n${file.name || file.path}`,
        action === "migrate" ? "迁移文件" : "删除文件",
        {
          type: action === "migrate" ? "info" : "warning",
          confirmButtonText: "确认",
          cancelButtonText: "取消",
        },
      );
    } catch {
      return;
    }

    setFileUi(file.path, {
      busy: true,
      action,
      label: action === "migrate" ? "迁移中..." : "删除中...",
      done: false,
    });

    try {
      await dispatchCommandWithFeedback({
        request: () => postJson(action === "migrate" ? "/api/migrate-file" : "/api/delete-file", { filePath: file.path, name: file.name || "" }),
        loadingText: action === "migrate" ? "正在下发迁移文件命令..." : "正在下发删除文件命令...",
        waitingText: action === "migrate" ? "等待 Mac 完成文件迁移..." : "等待 Mac 完成本地文件删除...",
        timeoutMs: action === "migrate" ? 120000 : 30000,
        onSuccess: (finalEvent) => {
          setFileUi(file.path, {
            busy: false,
            action,
            done: true,
            label: action === "migrate" ? "已迁移" : "已删除",
          });
          removeFileLocally(file.path);
          if (action === "migrate") {
            setInlineMessage(addMessage, buildEventMessage(finalEvent, "文件迁移已完成。"), "success");
          }
        },
        onError: (error) => {
          setFileUi(file.path, {
            busy: false,
            action,
            done: false,
            label: action === "migrate" ? "迁移" : "删除",
          });
          throw error;
        },
        successMessage: action === "migrate" ? "文件迁移已完成。" : "文件已删除。",
      });
    } catch (error) {
      setFileUi(file.path, {
        busy: false,
        action,
        done: false,
        label: action === "migrate" ? "迁移" : "删除",
      });
    }
  }

  async function migrateFile(file) {
    return fileAction(file, "migrate");
  }

  async function deleteFile(file) {
    return fileAction(file, "delete");
  }

  async function cleanupDownloads() {
    try {
      await ElMessageBox.confirm(
        "这个操作会清空 Mac 当前迅雷下载目录下的所有文件，可能包括正在下载和已下载内容。确定继续吗？",
        "清空下载目录",
        {
          type: "warning",
          confirmButtonText: "继续",
          cancelButtonText: "取消",
        },
      );
    } catch {
      return;
    }

    let value = "";
    try {
      const result = await ElMessageBox.prompt("请输入“清空”两个字确认删除：", "二次确认", {
        inputPattern: /^清空$/,
        inputErrorMessage: "确认词必须是“清空”",
        confirmButtonText: "确认清空",
        cancelButtonText: "取消",
      });
      value = result.value;
    } catch {
      setInlineMessage(cleanupMessage, DEFAULT_CLEANUP_MESSAGE, "");
      return;
    }

    cleanupBusy.value = true;
    setInlineMessage(cleanupMessage, "正在下发清空下载目录命令...", "");
    try {
      await dispatchCommandWithFeedback({
        request: () => postJson("/api/cleanup-downloads", { confirm: value }),
        loadingText: "正在下发清空下载目录命令...",
        waitingText: "等待 Mac 执行清空下载目录...",
        timeoutMs: 120000,
        onSuccess: (finalEvent) => {
          setInlineMessage(cleanupMessage, buildEventMessage(finalEvent, "下载目录已清空。"), "success");
        },
        onError: (error) => {
          setInlineMessage(cleanupMessage, error.message, "error");
        },
        successMessage: "下载目录已清空。",
      });
    } catch (error) {
      setInlineMessage(cleanupMessage, buildCommandError(error).message, "error");
    } finally {
      cleanupBusy.value = false;
    }
  }

  function connect() {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/ws`);

    socket.addEventListener("open", () => {
      pageChannelOpen.value = true;
    });

    socket.addEventListener("message", (event) => {
      try {
        applyState(JSON.parse(event.data));
      } catch (error) {
        console.warn(error);
      }
    });

    socket.addEventListener("close", () => {
      pageChannelOpen.value = false;
      socket = null;
      reconnectTimer = window.setTimeout(connect, 1500);
    });
  }

  onMounted(() => {
    refreshState().catch(() => {});
    connect();
    window.addEventListener("pointermove", updatePendingDrag);
    window.addEventListener("pointerup", stopPendingDrag);
    window.addEventListener("pointercancel", stopPendingDrag);
    window.addEventListener("resize", handleViewportResize);
  });

  onBeforeUnmount(() => {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
    }
    if (socket) {
      socket.close();
    }
    for (const waiter of commandWaiters.values()) {
      window.clearTimeout(waiter.timeoutId);
      window.clearInterval(waiter.intervalId);
    }
    commandWaiters.clear();
    window.removeEventListener("pointermove", updatePendingDrag);
    window.removeEventListener("pointerup", stopPendingDrag);
    window.removeEventListener("pointercancel", stopPendingDrag);
    window.removeEventListener("resize", handleViewportResize);
  });

  return reactive({
    latestState,
    stats,
    mac,
    tasks,
    pendingDialogs,
    speedText,
    macStateText,
    taskCreateModeText,
    lastUpdateText,
    connectionTone,
    connectionLabel,
    taskUrl,
    addBusy,
    cleanupBusy,
    addMessage,
    cleanupMessage,
    globalLoading,
    logDialogVisible,
    logText,
    progressGradient,
    formatBytes,
    formatSpeed,
    statusText,
    statusTagType,
    taskProgress,
    visibleTaskFiles,
    manualTaskFiles,
    taskActionLabel,
    canStartTask,
    canPauseTask,
    canDeleteTask,
    getPendingUi,
    getTaskUi,
    getFileUi,
    pendingSelectedCount,
    isPendingSelected,
    togglePendingSelection,
    selectAllPending,
    clearPendingSelection,
    pendingDialogStyle,
    bringPendingToFront,
    startPendingDrag,
    submitTask,
    confirmPending,
    cancelPending,
    controlTask,
    migrateFile,
    deleteFile,
    cleanupDownloads,
    refreshState,
  });
}
