const STATUS_KEYS = new Set([
  "loggedIn",
  "lastSyncAt",
  "lastSyncCount",
  "lastSyncTrigger",
  "lastError",
  "syncInProgress",
  "syncProgressPercent",
  "syncProgressStage",
  "syncProgressLabel",
  "syncProgressDetail",
  "syncRunId",
  "syncProgressUpdatedAt",
]);

let currentStatus = {};

async function sendMessage(message) {
  return chrome.runtime.sendMessage(message);
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function formatTimestamp(value) {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function normalizeProgressPercent(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
}

function setSessionStatus(loggedIn) {
  const pill = document.getElementById("sessionStatePill");
  if (!pill) {
    return;
  }
  if (loggedIn == null) {
    pill.dataset.status = "unknown";
    return;
  }
  pill.dataset.status = loggedIn ? "online" : "offline";
}

function renderKeywordChips(keywords) {
  const container = document.getElementById("focusKeywords");
  if (!container) {
    return;
  }

  const items = Array.isArray(keywords)
    ? keywords.map((value) => String(value || "").trim()).filter(Boolean)
    : [];

  container.innerHTML = "";
  if (!items.length) {
    container.classList.add("chip-group-empty");
    container.textContent = "未配置";
    return;
  }

  container.classList.remove("chip-group-empty");
  for (const item of items) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    container.appendChild(chip);
  }
}

function fillSyncProgress(status) {
  const syncButton = document.getElementById("syncButton");
  const clearButton = document.getElementById("clearCrawlDataButton");
  const panel = document.getElementById("syncProgressPanel");
  const isRunning = Boolean(status.syncInProgress);
  const percent = normalizeProgressPercent(status.syncProgressPercent);

  panel.hidden = !isRunning;
  syncButton.disabled = isRunning;
  clearButton.disabled = isRunning;
  syncButton.textContent = isRunning ? `同步中 ${percent}%` : "立即同步";

  if (!isRunning) {
    document.getElementById("syncProgressFill").style.width = "0%";
    setText("syncProgressPercent", "0%");
    setText("syncProgressLabel", "准备同步");
    setText("syncProgressDetail", "正在等待后台响应");
    return;
  }

  document.getElementById("syncProgressFill").style.width = `${percent}%`;
  setText("syncProgressPercent", `${percent}%`);
  setText("syncProgressLabel", status.syncProgressLabel || "同步中");
  setText("syncProgressDetail", status.syncProgressDetail || "正在处理中");
}

function fillStatus(statusPatch) {
  currentStatus = { ...currentStatus, ...statusPatch };
  setText("loggedIn", currentStatus.loggedIn == null ? "未知" : (currentStatus.loggedIn ? "已登录" : "未登录"));
  setText("lastSyncAt", formatTimestamp(currentStatus.lastSyncAt));
  setText("lastSyncCount", String(currentStatus.lastSyncCount || 0));
  setText("lastSyncTrigger", currentStatus.lastSyncTrigger || "暂无");
  setText("lastError", currentStatus.lastError || "无");
  setSessionStatus(currentStatus.loggedIn == null ? null : Boolean(currentStatus.loggedIn));

  const alertCard = document.getElementById("alertCard");
  if (alertCard) {
    alertCard.dataset.tone = currentStatus.lastError ? "danger" : "neutral";
  }

  fillSyncProgress(currentStatus);
}

function fillAiSummary(summary) {
  setText("providerType", summary?.providerType || "未配置");
  setText("baseUrl", summary?.baseUrl || "未配置");
  setText("selectedModel", summary?.selectedModel || "未配置");
  setText("modelCount", String(summary?.availableModelCount || 0));
  setText("lastModelSyncAt", formatTimestamp(summary?.lastModelSyncAt));
  setText("lastModelSyncError", summary?.lastModelSyncError || "无");
  renderKeywordChips(summary?.focusKeywords || []);

  const aiSummaryCard = document.getElementById("aiSummaryCard");
  if (aiSummaryCard) {
    aiSummaryCard.dataset.ready = summary?.selectedModel ? "ready" : "draft";
  }
}

function handleStorageChanges(changes, areaName) {
  if (areaName !== "local") {
    return;
  }

  const statusPatch = {};
  for (const key of STATUS_KEYS) {
    if (!(key in changes)) {
      continue;
    }
    statusPatch[key] = changes[key].newValue;
  }

  if (Object.keys(statusPatch).length > 0) {
    fillStatus(statusPatch);
  }
}

async function refresh() {
  const response = await sendMessage({ type: "get-state" });
  fillStatus(response.status || {});
  fillAiSummary(response.aiConfigSummary || null);
}

async function syncNow() {
  fillStatus({
    syncInProgress: true,
    syncProgressPercent: currentStatus.syncInProgress ? currentStatus.syncProgressPercent : 0,
    syncProgressLabel: currentStatus.syncProgressLabel || "准备同步",
    syncProgressDetail: currentStatus.syncProgressDetail || "正在启动后台同步",
  });

  const response = await sendMessage({ type: "sync-now" });
  if (response?.status) {
    fillStatus(response.status);
  }
  if (!response?.ok && response?.reason !== "busy" && response?.error) {
    fillStatus({
      syncInProgress: false,
      syncProgressPercent: 0,
      syncProgressLabel: "",
      syncProgressDetail: "",
      lastError: response.error,
    });
  }
}

async function clearCrawlData() {
  const confirmed = window.confirm("这会清空本地已爬取主题、通知状态和抓取游标，但不会删除 AI 配置。确定继续吗？");
  if (!confirmed) {
    return;
  }
  await sendMessage({ type: "clear-crawl-data" });
  await refresh();
}

document.getElementById("syncButton").addEventListener("click", async () => {
  await syncNow();
});

document.getElementById("clearCrawlDataButton").addEventListener("click", async () => {
  await clearCrawlData();
});

document.getElementById("openOptionsButton").addEventListener("click", async () => {
  await chrome.runtime.openOptionsPage();
});

chrome.storage.onChanged.addListener(handleStorageChanges);

refresh().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  setText("lastError", message);
  setText("lastModelSyncError", message);
  setSessionStatus(null);

  const alertCard = document.getElementById("alertCard");
  if (alertCard) {
    alertCard.dataset.tone = "danger";
  }
});
