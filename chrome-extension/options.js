/* =====================================================
   LinuxDoScanner – Options Page JS (重构版)
   保留全部原始逻辑 + 新增侧边栏导航切换
   ===================================================== */

// ─── 通用工具函数 ──────────────────────────────────────

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

let feedbackTimer = null;
let preservedBridgeToken = "";

function setCardState(id, state) {
  const element = document.getElementById(id);
  if (element) {
    element.dataset.state = state;
  }
}

// ─── 反馈横幅 ──────────────────────────────────────────

function showFeedback(message, tone = "success") {
  const banner = document.getElementById("feedbackBanner");
  if (!banner) {
    return;
  }

  if (feedbackTimer) {
    clearTimeout(feedbackTimer);
  }

  if (!message) {
    banner.hidden = true;
    banner.textContent = "";
    delete banner.dataset.tone;
    return;
  }

  banner.hidden = false;
  banner.dataset.tone = tone;
  banner.textContent = message;
  feedbackTimer = window.setTimeout(() => {
    banner.hidden = true;
    banner.textContent = "";
  }, 3200);
}

// ─── 按钮繁忙状态 ──────────────────────────────────────

async function withButtonBusy(buttonId, busyText, action) {
  const button = document.getElementById(buttonId);
  if (!button) {
    return action();
  }

  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

// ─── 错误处理 ──────────────────────────────────────────

function handleActionError(error) {
  const message = error instanceof Error ? error.message : String(error);
  showFeedback(message, "danger");
  setText("lastError", message);
  setText("lastModelSyncError", message);
  setText("feishuStatus", message);

  const errorSpotlight = document.getElementById("errorSpotlight");
  if (errorSpotlight) {
    errorSpotlight.dataset.tone = "danger";
  }
}

// ─── 数据填充：桥接配置 ──────────────────────────────────

function fillBridgeConfig(config) {
  document.getElementById("serverUrl").value = config.serverUrl || "";
  preservedBridgeToken = config.bridgeToken || "";
  document.getElementById("intervalMinutes").value = String(config.intervalMinutes || 5);
  document.getElementById("pageRequestIntervalSeconds").value = String(config.pageRequestIntervalSeconds || 10);
  setCardState("bridgeCard", "armed");
}

// ─── 关键词标准化 ──────────────────────────────────────

function normalizeKeywordItems(value) {
  return String(value || "")
    .split(/[,\n，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

// ─── 数据填充：AI 配置 ──────────────────────────────────

function fillAiConfig(config) {
  const focusKeywords = Array.isArray(config.focus_keywords)
    ? config.focus_keywords
    : normalizeKeywordItems(config.focus_keywords || "");

  document.getElementById("baseUrl").value = config.base_url || "";
  document.getElementById("apiKey").value = config.api_key || "";
  document.getElementById("selectedModel").value = config.selected_model || "";
  document.getElementById("focusKeywords").value = focusKeywords.join("，");
  document.getElementById("focusPrompt").value = config.focus_prompt || "";
  document.getElementById("notificationPrompt").value = config.notification_prompt || "";

  const datalist = document.getElementById("availableModels");
  datalist.innerHTML = "";
  for (const model of config.available_models || []) {
    const option = document.createElement("option");
    option.value = model.id || "";
    option.label = model.owned_by || "";
    datalist.appendChild(option);
  }

  const hasModel = Boolean(config.selected_model);
  const hasFocus = Boolean((config.focus_prompt || "").trim() || focusKeywords.length);
  setCardState("aiCard", hasModel || hasFocus ? "ready" : "draft");
}

// ─── 数据填充：状态信息 ──────────────────────────────────

function fillStatus(status) {
  const loggedIn = status.loggedIn == null ? null : Boolean(status.loggedIn);

  setText("loggedIn", loggedIn == null ? "未知" : (loggedIn ? "已登录" : "未登录"));
  setText("lastSyncAt", formatTimestamp(status.lastSyncAt));
  setText("lastSyncCount", String(status.lastSyncCount || 0));
  setText("lastError", status.lastError || "无");

  const loginMetric = document.getElementById("loginMetric");
  if (loginMetric) {
    loginMetric.dataset.state = loggedIn == null ? "unknown" : (loggedIn ? "online" : "offline");
  }

  const errorSpotlight = document.getElementById("errorSpotlight");
  if (errorSpotlight) {
    errorSpotlight.dataset.tone = status.lastError ? "danger" : "neutral";
  }
}

// ─── 数据填充：通知配置 ──────────────────────────────────

function fillNotificationConfig(config) {
  document.getElementById("feishuEnabled").checked = Boolean(config.feishu_enabled);
  document.getElementById("larkCliPath").value = config.lark_cli_path || "";
  document.getElementById("feishuChatId").value = config.feishu_chat_id || "";
  document.getElementById("feishuUserId").value = config.feishu_user_id || "";

  let status = "未配置";
  if (config.feishu_enabled && (config.feishu_chat_id || config.feishu_user_id) && config.lark_cli_path) {
    status = config.feishu_chat_id ? "已配置为群通知" : "已配置为私聊通知";
    setCardState("notificationCard", "ready");
  } else if (config.feishu_enabled) {
    status = "已启用，但配置不完整";
    setCardState("notificationCard", "warning");
  } else {
    setCardState("notificationCard", "idle");
  }
  setText("feishuStatus", status);
}

// ─── 数据收集：桥接配置 ──────────────────────────────────

function collectBridgePayload() {
  return {
    serverUrl: document.getElementById("serverUrl").value.trim(),
    bridgeToken: preservedBridgeToken,
    intervalMinutes: Number(document.getElementById("intervalMinutes").value),
    pageRequestIntervalSeconds: Number(document.getElementById("pageRequestIntervalSeconds").value),
    syncEnabled: true,
  };
}

// ─── 数据收集：AI 配置 ──────────────────────────────────

function collectAiPayload() {
  return {
    providerType: "openai_compatible",
    baseUrl: document.getElementById("baseUrl").value.trim(),
    apiKey: document.getElementById("apiKey").value.trim(),
    selectedModel: document.getElementById("selectedModel").value.trim(),
    focusKeywords: normalizeKeywordItems(document.getElementById("focusKeywords").value).join(", "),
    focusPrompt: document.getElementById("focusPrompt").value.trim(),
    notificationPrompt: document.getElementById("notificationPrompt").value.trim(),
  };
}

// ─── 数据收集：通知配置 ──────────────────────────────────

function collectNotificationPayload() {
  return {
    feishuEnabled: document.getElementById("feishuEnabled").checked,
    larkCliPath: document.getElementById("larkCliPath").value.trim(),
    feishuChatId: document.getElementById("feishuChatId").value.trim(),
    feishuUserId: document.getElementById("feishuUserId").value.trim(),
  };
}

// ─── 刷新：从后台拉取所有配置 ──────────────────────────────

async function refresh() {
  const [extensionResponse, stateResponse, aiResponse, notificationResponse] = await Promise.all([
    sendMessage({ type: "get-extension-config" }),
    sendMessage({ type: "get-state" }),
    sendMessage({ type: "get-ai-config" }).catch(() => ({ config: null })),
    sendMessage({ type: "get-notification-config" }).catch(() => ({ config: null })),
  ]);
  fillBridgeConfig(extensionResponse.config || {});
  fillStatus(stateResponse.status || {});
  fillAiConfig(aiResponse.config || {});
  fillNotificationConfig(notificationResponse.config || {});
}

// ─── 操作：保存桥接配置 ──────────────────────────────────

async function saveBridgeConfig() {
  const response = await withButtonBusy("saveBridgeButton", "保存中...", () => sendMessage({
    type: "save-extension-config",
    ...collectBridgePayload(),
  }));
  fillBridgeConfig(response.config || {});
  showFeedback("桥接设置已保存");
}

// ─── 操作：保存 AI 配置 ──────────────────────────────────

async function saveAiConfig() {
  const response = await withButtonBusy("saveAiButton", "保存中...", () => sendMessage({
    type: "save-ai-config",
    ...collectAiPayload(),
  }));
  fillAiConfig(response.config || {});
  showFeedback("AI 配置已保存");
}

// ─── 操作：同步模型列表 ──────────────────────────────────

async function syncModels() {
  const response = await withButtonBusy("syncModelsButton", "同步中...", () => sendMessage({
    type: "sync-ai-models",
    ...collectAiPayload(),
  }));
  fillAiConfig(response.config || {});
  showFeedback("模型列表已同步");
}

// ─── 操作：清空爬取数据 ──────────────────────────────────

async function clearCrawlData() {
  const confirmed = window.confirm("这会清空本地已爬取主题、通知状态和抓取游标，但不会删除 AI 配置。确定继续吗？");
  if (!confirmed) {
    return;
  }
  await withButtonBusy("clearCrawlDataButton", "清空中...", () => sendMessage({ type: "clear-crawl-data" }));
  showFeedback("抓取数据库已清空");
}

// ─── 操作：保存飞书通知配置 ──────────────────────────────

async function saveNotificationConfig() {
  const response = await withButtonBusy("saveFeishuButton", "保存中...", () => sendMessage({
    type: "save-notification-config",
    ...collectNotificationPayload(),
  }));
  fillNotificationConfig(response.config || {});
  showFeedback("飞书配置已保存");
}

// ─── 操作：测试飞书通知 ──────────────────────────────────

async function testNotificationConfig() {
  const response = await withButtonBusy("testFeishuButton", "发送中...", () => sendMessage({
    type: "test-notification-config",
    ...collectNotificationPayload(),
  }));
  setText("feishuStatus", response.message || "飞书测试消息已发送");
  showFeedback(response.message || "飞书测试消息已发送");
}

// =====================================================
//  侧边栏导航切换逻辑
// =====================================================

function initNavigation() {
  const navItems = document.querySelectorAll(".nav-item");
  const sections = document.querySelectorAll(".content-section");

  navItems.forEach((item) => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const targetId = item.dataset.section;

      // 切换导航高亮
      navItems.forEach((nav) => nav.classList.remove("active"));
      item.classList.add("active");

      // 切换内容区域
      sections.forEach((sec) => sec.classList.remove("active"));
      const target = document.getElementById(targetId);
      if (target) {
        target.classList.add("active");
      }
    });
  });
}

// =====================================================
//  事件绑定
// =====================================================

document.getElementById("saveBridgeButton").addEventListener("click", async () => {
  try {
    await saveBridgeConfig();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("saveAiButton").addEventListener("click", async () => {
  try {
    await saveAiConfig();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("syncModelsButton").addEventListener("click", async () => {
  try {
    await syncModels();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("clearCrawlDataButton").addEventListener("click", async () => {
  try {
    await clearCrawlData();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("saveFeishuButton").addEventListener("click", async () => {
  try {
    await saveNotificationConfig();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("testFeishuButton").addEventListener("click", async () => {
  try {
    await testNotificationConfig();
  } catch (error) {
    handleActionError(error);
  }
});

// ─── 初始化 ──────────────────────────────────────────

initNavigation();

refresh().catch((error) => {
  handleActionError(error);
});
