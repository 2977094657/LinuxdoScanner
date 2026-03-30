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

function fillBridgeConfig(config) {
  document.getElementById("serverUrl").value = config.serverUrl || "";
  document.getElementById("bridgeToken").value = config.bridgeToken || "";
  document.getElementById("intervalMinutes").value = String(config.intervalMinutes || 5);
  document.getElementById("syncEnabled").checked = Boolean(config.syncEnabled);
}

function fillAiConfig(config) {
  document.getElementById("providerType").value = config.provider_type || "openai_compatible";
  document.getElementById("baseUrl").value = config.base_url || "";
  document.getElementById("apiKey").value = config.api_key || "";
  document.getElementById("selectedModel").value = config.selected_model || "";
  document.getElementById("focusKeywords").value = (config.focus_keywords || []).join("\n");
  document.getElementById("focusPrompt").value = config.focus_prompt || "";
  document.getElementById("notificationPrompt").value = config.notification_prompt || "";
  document.getElementById("modelCount").value = String((config.available_models || []).length);
  setText("lastModelSyncAt", formatTimestamp(config.last_model_sync_at));
  setText("lastModelSyncError", config.last_model_sync_error || "无");

  const datalist = document.getElementById("availableModels");
  datalist.innerHTML = "";
  for (const model of config.available_models || []) {
    const option = document.createElement("option");
    option.value = model.id || "";
    option.label = model.owned_by || "";
    datalist.appendChild(option);
  }
}

function fillStatus(status) {
  setText("loggedIn", status.loggedIn ? "已登录" : "未登录");
  setText("lastSyncAt", formatTimestamp(status.lastSyncAt));
  setText("lastSyncCount", String(status.lastSyncCount || 0));
  setText("lastError", status.lastError || "无");
}

function fillNotificationConfig(config) {
  document.getElementById("feishuEnabled").checked = Boolean(config.feishu_enabled);
  document.getElementById("larkCliPath").value = config.lark_cli_path || "";
  document.getElementById("feishuChatId").value = config.feishu_chat_id || "";
  document.getElementById("feishuUserId").value = config.feishu_user_id || "";

  let status = "未配置";
  if (config.feishu_enabled && (config.feishu_chat_id || config.feishu_user_id) && config.lark_cli_path) {
    status = config.feishu_chat_id ? "已配置为群通知" : "已配置为私聊通知";
  } else if (config.feishu_enabled) {
    status = "已启用，但配置不完整";
  }
  setText("feishuStatus", status);
}

function collectBridgePayload() {
  return {
    serverUrl: document.getElementById("serverUrl").value.trim(),
    bridgeToken: document.getElementById("bridgeToken").value.trim(),
    intervalMinutes: Number(document.getElementById("intervalMinutes").value),
    syncEnabled: document.getElementById("syncEnabled").checked,
  };
}

function collectAiPayload() {
  return {
    providerType: document.getElementById("providerType").value,
    baseUrl: document.getElementById("baseUrl").value.trim(),
    apiKey: document.getElementById("apiKey").value.trim(),
    selectedModel: document.getElementById("selectedModel").value.trim(),
    focusKeywords: document.getElementById("focusKeywords").value,
    focusPrompt: document.getElementById("focusPrompt").value.trim(),
    notificationPrompt: document.getElementById("notificationPrompt").value.trim(),
  };
}

function collectNotificationPayload() {
  return {
    feishuEnabled: document.getElementById("feishuEnabled").checked,
    larkCliPath: document.getElementById("larkCliPath").value.trim(),
    feishuChatId: document.getElementById("feishuChatId").value.trim(),
    feishuUserId: document.getElementById("feishuUserId").value.trim(),
  };
}

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

async function saveBridgeConfig() {
  const response = await sendMessage({
    type: "save-extension-config",
    ...collectBridgePayload(),
  });
  fillBridgeConfig(response.config || {});
}

async function saveAiConfig() {
  const response = await sendMessage({
    type: "save-ai-config",
    ...collectAiPayload(),
  });
  fillAiConfig(response.config || {});
}

async function syncModels() {
  const response = await sendMessage({
    type: "sync-ai-models",
    ...collectAiPayload(),
  });
  fillAiConfig(response.config || {});
}

async function clearCrawlData() {
  const confirmed = window.confirm("这会清空本地已爬取主题、通知状态和抓取游标，但不会删除 AI 配置。确定继续吗？");
  if (!confirmed) {
    return;
  }
  await sendMessage({ type: "clear-crawl-data" });
}

async function saveNotificationConfig() {
  const response = await sendMessage({
    type: "save-notification-config",
    ...collectNotificationPayload(),
  });
  fillNotificationConfig(response.config || {});
}

async function testNotificationConfig() {
  const response = await sendMessage({
    type: "test-notification-config",
    ...collectNotificationPayload(),
  });
  setText("feishuStatus", response.message || "飞书测试消息已发送");
}

document.getElementById("saveBridgeButton").addEventListener("click", async () => {
  await saveBridgeConfig();
  await refresh();
});

document.getElementById("saveAiButton").addEventListener("click", async () => {
  await saveAiConfig();
  await refresh();
});

document.getElementById("syncModelsButton").addEventListener("click", async () => {
  await syncModels();
  await refresh();
});

document.getElementById("clearCrawlDataButton").addEventListener("click", async () => {
  await clearCrawlData();
  await refresh();
});

document.getElementById("saveFeishuButton").addEventListener("click", async () => {
  await saveNotificationConfig();
  await refresh();
});

document.getElementById("testFeishuButton").addEventListener("click", async () => {
  await testNotificationConfig();
});

refresh().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  setText("lastError", message);
  setText("lastModelSyncError", message);
  setText("feishuStatus", message);
});
