const DEFAULT_CONFIG = {
  serverUrl: "http://127.0.0.1:8765",
  bridgeToken: "",
  syncEnabled: true,
  intervalMinutes: 5,
};

const DEFAULT_STATUS = {
  lastSyncAt: "",
  lastSyncTrigger: "",
  lastSyncCount: 0,
  lastError: "",
  loggedIn: false,
  lastServerState: null,
};

function storageGet(keys) {
  return chrome.storage.local.get(keys);
}

function storageSet(values) {
  return chrome.storage.local.set(values);
}

async function getConfig() {
  const stored = await storageGet(DEFAULT_CONFIG);
  return { ...DEFAULT_CONFIG, ...stored };
}

async function getStatus() {
  const stored = await storageGet(DEFAULT_STATUS);
  return { ...DEFAULT_STATUS, ...stored };
}

async function saveStatus(patch) {
  await storageSet(patch);
  return getStatus();
}

async function setBadge(text, color) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
}

async function ensureDefaults() {
  const current = await storageGet({ ...DEFAULT_CONFIG, ...DEFAULT_STATUS });
  await storageSet(current);
}

async function updateAlarm() {
  const config = await getConfig();
  await chrome.alarms.clear("linuxdo-sync");
  if (!config.syncEnabled) {
    await setBadge("OFF", "#777777");
    return;
  }
  const minutes = Math.max(1, Number(config.intervalMinutes) || 5);
  await chrome.alarms.create("linuxdo-sync", {
    delayInMinutes: 0.2,
    periodInMinutes: minutes,
  });
}

async function buildBridgeHeaders(config) {
  const headers = {
    Accept: "application/json",
  };
  if (config.bridgeToken) {
    headers["X-LinuxDo-Bridge-Token"] = config.bridgeToken;
  }
  return headers;
}

async function bridgeFetch(config, path, options = {}) {
  const headers = {
    ...(await buildBridgeHeaders(config)),
    ...(options.headers || {}),
  };
  const response = await fetch(`${config.serverUrl.replace(/\/+$/, "")}${path}`, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`bridge ${path} failed: HTTP ${response.status} ${text}`);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function maskApiKey(apiKey) {
  if (!apiKey) {
    return "";
  }
  if (apiKey.length <= 8) {
    return `${apiKey.slice(0, 2)}***`;
  }
  return `${apiKey.slice(0, 4)}***${apiKey.slice(-4)}`;
}

function buildAiConfigSummary(aiConfig) {
  if (!aiConfig) {
    return null;
  }
  return {
    providerType: aiConfig.provider_type || "openai_compatible",
    baseUrl: aiConfig.base_url || "",
    apiKeyMasked: maskApiKey(aiConfig.api_key || ""),
    selectedModel: aiConfig.selected_model || "",
    availableModelCount: Array.isArray(aiConfig.available_models) ? aiConfig.available_models.length : 0,
    lastModelSyncAt: aiConfig.last_model_sync_at || "",
    lastModelSyncError: aiConfig.last_model_sync_error || "",
    focusKeywords: Array.isArray(aiConfig.focus_keywords) ? aiConfig.focus_keywords : [],
    focusPrompt: aiConfig.focus_prompt || "",
    notificationPrompt: aiConfig.notification_prompt || "",
  };
}

async function getAiConfig(config = null) {
  const bridgeConfig = config || await getConfig();
  const response = await bridgeFetch(bridgeConfig, "/api/bridge/ai-config");
  return response?.config || null;
}

async function getNotificationConfig(config = null) {
  const bridgeConfig = config || await getConfig();
  const response = await bridgeFetch(bridgeConfig, "/api/bridge/notification-config");
  return response?.config || null;
}

async function saveAiConfig(payload) {
  const config = await getConfig();
  const response = await bridgeFetch(config, "/api/bridge/ai-config", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return response?.config || null;
}

async function syncAiModels(payload) {
  const config = await getConfig();
  const response = await bridgeFetch(config, "/api/bridge/ai-config/sync-models", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload || {}),
  });
  return response?.config || null;
}

async function saveNotificationConfig(payload) {
  const config = await getConfig();
  const response = await bridgeFetch(config, "/api/bridge/notification-config", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return response?.config || null;
}

async function testNotificationConfig(payload) {
  const config = await getConfig();
  return bridgeFetch(config, "/api/bridge/notification-config/test", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload || {}),
  });
}

async function clearCrawlData() {
  const config = await getConfig();
  const response = await bridgeFetch(config, "/api/bridge/crawl-data/clear", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({}),
  });
  await saveStatus({
    lastSyncAt: "",
    lastSyncTrigger: "database-cleared",
    lastSyncCount: 0,
    lastError: "",
    lastServerState: null,
  });
  await setBadge("", "#188038");
  return response;
}

async function linuxFetchJson(path) {
  const response = await fetch(`https://linux.do${path}`, {
    credentials: "include",
    headers: {
      Accept: "application/json, text/plain, */*",
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`linux.do ${path} failed: HTTP ${response.status} ${text.slice(0, 160)}`);
  }
  return response.json();
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function isRecoverableLinuxTabMessagingError(error) {
  const text = errorMessage(error);
  return [
    "Receiving end does not exist",
    "message channel closed before a response was received",
    "The message port closed before a response was received",
    "No tab with id",
  ].some((pattern) => text.includes(pattern));
}

async function waitForTabComplete(tabId) {
  const currentTab = await chrome.tabs.get(tabId).catch(() => null);
  if (!currentTab) {
    throw new Error("linux.do 标签页已被关闭");
  }
  if (currentTab.status === "complete") {
    return;
  }

  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.tabs.onRemoved.removeListener(onRemoved);
    };

    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("等待 linux.do 标签页加载超时"));
    }, 30000);

    function onUpdated(updatedTabId, info) {
      if (updatedTabId === tabId && info.status === "complete") {
        cleanup();
        resolve();
      }
    }

    function onRemoved(removedTabId) {
      if (removedTabId === tabId) {
        cleanup();
        reject(new Error("linux.do 标签页已被关闭"));
      }
    }

    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.onRemoved.addListener(onRemoved);
  });
}

async function ensureLinuxDoTab(options = {}) {
  const allowActiveReuse = Boolean(options.allowActiveReuse);
  const preferTemporary = Boolean(options.preferTemporary);

  if (!preferTemporary) {
    const tabs = await chrome.tabs.query({
      url: ["https://linux.do/*"],
    });

    const reusableTab = tabs.find(
      (tab) => typeof tab.id === "number" && !tab.discarded && (allowActiveReuse || !tab.active)
    );
    if (reusableTab?.id) {
      await waitForTabComplete(reusableTab.id);
      return {
        tabId: reusableTab.id,
        temporary: false,
      };
    }
  }

  const tab = await chrome.tabs.create({
    url: "https://linux.do/latest?order=created",
    active: false,
  });

  await waitForTabComplete(tab.id);
  return {
    tabId: tab.id,
    temporary: true,
  };
}

async function sendMessageToLinuxTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (error) {
    const text = errorMessage(error);
    if (!text.includes("Receiving end does not exist")) {
      throw error;
    }
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

async function withLinuxDoTab(task, options = {}) {
  const attempt = Number(options.attempt) || 0;
  const allowActiveReuse = Boolean(options.allowActiveReuse);
  const preferTemporary = Boolean(options.preferTemporary);
  const { tabId, temporary } = await ensureLinuxDoTab({ allowActiveReuse, preferTemporary });
  try {
    return await task(tabId);
  } catch (error) {
    if (attempt >= 1 || !isRecoverableLinuxTabMessagingError(error)) {
      throw error;
    }
    console.warn("linux.do tab message channel closed, retrying with a dedicated tab", {
      message: errorMessage(error),
      tabId,
    });
    return withLinuxDoTab(task, {
      attempt: attempt + 1,
      allowActiveReuse,
      preferTemporary: true,
    });
  } finally {
    if (temporary) {
      await chrome.tabs.remove(tabId).catch(() => {});
    }
  }
}

function ensureSiteStateResponse(response) {
  if (!response || response.ok === false) {
    throw new Error(response?.error || "无法从 linux.do 标签页读取登录状态");
  }
  return response;
}

function ensureTopicsResponse(response) {
  if (!response || response.ok === false) {
    throw new Error(response?.error || "无法从 linux.do 标签页抓取主题数据");
  }
  return response;
}

async function fetchSiteStateFromTab() {
  return withLinuxDoTab(async (tabId) => {
    const response = await sendMessageToLinuxTab(tabId, { type: "fetch-linuxdo-state" });
    return ensureSiteStateResponse(response);
  }, { allowActiveReuse: true });
}

async function fetchTopicsFromTab(lastSeenTopicId, bootstrapLimit, maxPagesPerRun) {
  return withLinuxDoTab(async (tabId) => {
    const response = await sendMessageToLinuxTab(tabId, {
      type: "fetch-linuxdo-topics",
      lastSeenTopicId,
      bootstrapLimit,
      maxPagesPerRun,
    });
    return ensureTopicsResponse(response);
  });
}

async function collectTopicDocuments(lastSeenTopicId, bootstrapLimit, maxPagesPerRun) {
  const selected = [];
  const seen = new Set();

  for (let page = 0; page < maxPagesPerRun; page += 1) {
    const suffix = page === 0 ? "/latest.json?order=created" : `/latest.json?order=created&page=${page}`;
    const data = await linuxFetchJson(suffix);
    const topics = (((data || {}).topic_list || {}).topics) || [];
    if (!topics.length) {
      break;
    }

    for (const topic of topics) {
      const topicId = Number(topic.id);
      if (!topicId || seen.has(topicId)) {
        continue;
      }
      seen.add(topicId);

      if (lastSeenTopicId == null) {
        selected.push(topic);
        if (selected.length >= bootstrapLimit) {
          return fetchTopicDetails(selected);
        }
        continue;
      }

      if (topicId <= lastSeenTopicId) {
        return fetchTopicDetails(selected);
      }

      selected.push(topic);
    }
  }

  return fetchTopicDetails(selected);
}

async function fetchTopicDetails(summaries) {
  const documents = [];
  for (const summary of summaries) {
    let detail = null;
    let detailError = "";
    try {
      detail = await linuxFetchJson(`/t/${encodeURIComponent(summary.slug)}/${summary.id}.json`);
    } catch (error) {
      detailError = error instanceof Error ? error.message : String(error);
    }
    documents.push({
      summary,
      detail,
      detail_error: detailError,
    });
  }
  return documents;
}

async function runSync(trigger = "manual") {
  const config = await getConfig();
  if (!config.syncEnabled) {
    await saveStatus({
      lastError: "",
      lastSyncTrigger: trigger,
    });
    await setBadge("OFF", "#777777");
    return { ok: false, reason: "disabled" };
  }

  await setBadge("...", "#3367d6");
  try {
    const serverState = await bridgeFetch(config, "/api/bridge/state");
    const { siteState, topicResponse } = await withLinuxDoTab(async (tabId) => {
      const currentSiteState = await ensureSiteStateResponse(
        await sendMessageToLinuxTab(tabId, { type: "fetch-linuxdo-state" })
      );

      if (serverState.require_login && !currentSiteState.loggedIn) {
        return {
          siteState: currentSiteState,
          topicResponse: null,
        };
      }

      const currentTopicResponse = await ensureTopicsResponse(
        await sendMessageToLinuxTab(tabId, {
          type: "fetch-linuxdo-topics",
          lastSeenTopicId: serverState.last_seen_topic_id ?? null,
          bootstrapLimit: Number(serverState.bootstrap_limit) || 30,
          maxPagesPerRun: Number(serverState.max_pages_per_run) || 10,
        })
      );

      return {
        siteState: currentSiteState,
        topicResponse: currentTopicResponse,
      };
    });
    const loggedIn = siteState.loggedIn;

    if (serverState.require_login && !loggedIn) {
      const status = await saveStatus({
        loggedIn: false,
        lastError: "当前浏览器未登录 linux.do",
        lastSyncTrigger: trigger,
        lastServerState: serverState,
      });
      await setBadge("LOG", "#d93025");
      return { ok: false, reason: "login_required", status };
    }

    const response = await bridgeFetch(config, "/api/bridge/push", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        trigger,
        browser: "chrome-extension",
        extensionVersion: chrome.runtime.getManifest().version,
        fetchedAt: new Date().toISOString(),
        logged_in: Boolean(topicResponse.loggedIn),
        categories: topicResponse.categories || siteState.categories || [],
        topics: topicResponse.topics || [],
      }),
    });

    const count = Number(response.stored_count) || 0;
    await saveStatus({
      lastSyncAt: new Date().toISOString(),
      lastSyncTrigger: trigger,
      lastSyncCount: count,
      lastError: "",
      loggedIn,
      lastServerState: serverState,
    });
    await setBadge(loggedIn ? (count > 0 ? String(Math.min(count, 99)) : "OK") : "LOG", loggedIn ? "#188038" : "#d93025");
    return { ok: true, storedCount: count };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await saveStatus({
      lastError: message,
      lastSyncTrigger: trigger,
    });
    await setBadge("ERR", "#d93025");
    return { ok: false, error: message };
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  await ensureDefaults();
  await updateAlarm();
});

chrome.runtime.onStartup.addListener(async () => {
  await ensureDefaults();
  await updateAlarm();
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "linuxdo-sync") {
    await runSync("alarm");
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "get-state") {
      const config = await getConfig();
      const siteState = await fetchSiteStateFromTab().catch(() => ({ loggedIn: false }));
      const status = await getStatus();
      if (status.loggedIn !== Boolean(siteState.loggedIn)) {
        await saveStatus({ loggedIn: Boolean(siteState.loggedIn) });
      }
      const aiConfig = await getAiConfig(config).catch(() => null);
      sendResponse({
        config,
        status: await getStatus(),
        aiConfigSummary: buildAiConfigSummary(aiConfig),
      });
      return;
    }

    if (message?.type === "get-extension-config") {
      sendResponse({
        ok: true,
        config: await getConfig(),
      });
      return;
    }

    if (message?.type === "save-extension-config" || message?.type === "save-config") {
      await storageSet({
        serverUrl: message.serverUrl || DEFAULT_CONFIG.serverUrl,
        bridgeToken: message.bridgeToken || "",
        syncEnabled: Boolean(message.syncEnabled),
        intervalMinutes: Math.max(1, Number(message.intervalMinutes) || 5),
      });
      await updateAlarm();
      sendResponse({
        ok: true,
        config: await getConfig(),
      });
      return;
    }

    if (message?.type === "get-ai-config") {
      sendResponse({
        ok: true,
        config: await getAiConfig(),
      });
      return;
    }

    if (message?.type === "get-notification-config") {
      sendResponse({
        ok: true,
        config: await getNotificationConfig(),
      });
      return;
    }

    if (message?.type === "save-ai-config") {
      sendResponse({
        ok: true,
        config: await saveAiConfig({
          provider_type: message.providerType,
          base_url: message.baseUrl,
          api_key: message.apiKey,
          selected_model: message.selectedModel,
          focus_keywords: message.focusKeywords,
          focus_prompt: message.focusPrompt,
          notification_prompt: message.notificationPrompt,
        }),
      });
      return;
    }

    if (message?.type === "sync-ai-models") {
      sendResponse({
        ok: true,
        config: await syncAiModels({
          provider_type: message.providerType,
          base_url: message.baseUrl,
          api_key: message.apiKey,
          selected_model: message.selectedModel,
          focus_keywords: message.focusKeywords,
          focus_prompt: message.focusPrompt,
          notification_prompt: message.notificationPrompt,
        }),
      });
      return;
    }

    if (message?.type === "save-notification-config") {
      sendResponse({
        ok: true,
        config: await saveNotificationConfig({
          feishu_enabled: Boolean(message.feishuEnabled),
          lark_cli_path: message.larkCliPath,
          feishu_chat_id: message.feishuChatId,
          feishu_user_id: message.feishuUserId,
        }),
      });
      return;
    }

    if (message?.type === "test-notification-config") {
      sendResponse(
        await testNotificationConfig({
          feishu_enabled: Boolean(message.feishuEnabled),
          lark_cli_path: message.larkCliPath,
          feishu_chat_id: message.feishuChatId,
          feishu_user_id: message.feishuUserId,
        })
      );
      return;
    }

    if (message?.type === "sync-now") {
      sendResponse(await runSync("manual"));
      return;
    }

    if (message?.type === "clear-crawl-data") {
      sendResponse(await clearCrawlData());
      return;
    }

    sendResponse({ ok: false, error: "unknown_message" });
  })().catch((error) => {
    sendResponse({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  });

  return true;
});
