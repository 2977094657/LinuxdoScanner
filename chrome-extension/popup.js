async function sendMessage(message) {
  return chrome.runtime.sendMessage(message);
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatTimestamp(value) {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function fillStatus(status) {
  setText("loggedIn", status.loggedIn ? "已登录" : "未登录");
  setText("lastSyncAt", formatTimestamp(status.lastSyncAt));
  setText("lastSyncCount", String(status.lastSyncCount || 0));
  setText("lastSyncTrigger", status.lastSyncTrigger || "暂无");
  setText("lastError", status.lastError || "无");
}

function fillAiSummary(summary) {
  setText("providerType", summary?.providerType || "未配置");
  setText("baseUrl", summary?.baseUrl || "未配置");
  setText("selectedModel", summary?.selectedModel || "未配置");
  setText("focusKeywords", (summary?.focusKeywords || []).join(" / ") || "未配置");
  setText("modelCount", String(summary?.availableModelCount || 0));
  setText("lastModelSyncAt", formatTimestamp(summary?.lastModelSyncAt));
  setText("lastModelSyncError", `模型同步错误：${summary?.lastModelSyncError || "无"}`);
}

async function refresh() {
  const response = await sendMessage({ type: "get-state" });
  fillStatus(response.status || {});
  fillAiSummary(response.aiConfigSummary || null);
}

async function syncNow() {
  await sendMessage({ type: "sync-now" });
  await refresh();
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

refresh().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  setText("lastError", message);
});
