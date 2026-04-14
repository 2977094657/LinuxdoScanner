const DEFAULT_CONFIG = {
  serverUrl: "http://127.0.0.1:8765",
  bridgeToken: "",
  syncEnabled: true,
  intervalMinutes: 5,
  maxPagesPerRound: null,
  pageRequestDelayMinSeconds: null,
  pageRequestDelayMaxSeconds: null,
  roundDelayMinSeconds: null,
  roundDelayMaxSeconds: null,
  pageRequestIntervalSeconds: null,
  backwardFetchMaxDays: 1,
  pushBatchSize: 5,
};

const DEFAULT_STATUS = {
  lastSyncAt: "",
  lastSyncTrigger: "",
  lastSyncCount: 0,
  lastError: "",
  loggedIn: false,
  lastServerState: null,
  syncInProgress: false,
  syncProgressPercent: 0,
  syncProgressStage: "",
  syncProgressLabel: "",
  syncProgressDetail: "",
  syncRunId: "",
  syncProgressUpdatedAt: "",
};

const DEFAULT_CRAWL_STRATEGY = {
  maxPagesPerRound: 10,
  pageRequestDelayMinSeconds: 1,
  pageRequestDelayMaxSeconds: 10,
  roundDelayMinSeconds: 1,
  roundDelayMaxSeconds: 180,
};
const SYNC_BADGE_COLOR = "#3367d6";

let activeSyncPromise = null;
let activeSyncRunId = "";

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

function toOptionalNonNegativeInteger(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const normalized = Number(value);
  if (!Number.isFinite(normalized)) {
    return null;
  }
  return Math.max(0, Math.round(normalized));
}

function normalizeDelayRange(minValue, maxValue, fallbackMin, fallbackMax) {
  let minimum = toOptionalNonNegativeInteger(minValue);
  let maximum = toOptionalNonNegativeInteger(maxValue);
  if (minimum == null) {
    minimum = Math.max(0, Math.round(Number(fallbackMin) || 0));
  }
  if (maximum == null) {
    maximum = Math.max(0, Math.round(Number(fallbackMax) || 0));
  }
  if (maximum < minimum) {
    [minimum, maximum] = [maximum, minimum];
  }
  return {
    minSeconds: minimum,
    maxSeconds: maximum,
  };
}

function resolveCrawlStrategyConfig(config, serverState = null) {
  const legacyPageDelaySeconds = toOptionalNonNegativeInteger(config.pageRequestIntervalSeconds);
  const maxPagesPerRound = Math.max(
    1,
    toOptionalNonNegativeInteger(config.maxPagesPerRound) ??
      toOptionalNonNegativeInteger(serverState?.max_pages_per_run) ??
      DEFAULT_CRAWL_STRATEGY.maxPagesPerRound
  );
  const pageDelayRange = normalizeDelayRange(
    config.pageRequestDelayMinSeconds ?? legacyPageDelaySeconds,
    config.pageRequestDelayMaxSeconds ?? legacyPageDelaySeconds,
    serverState?.page_request_delay_min_seconds ?? DEFAULT_CRAWL_STRATEGY.pageRequestDelayMinSeconds,
    serverState?.page_request_delay_max_seconds ?? DEFAULT_CRAWL_STRATEGY.pageRequestDelayMaxSeconds
  );
  const roundDelayRange = normalizeDelayRange(
    config.roundDelayMinSeconds,
    config.roundDelayMaxSeconds,
    serverState?.round_delay_min_seconds ?? DEFAULT_CRAWL_STRATEGY.roundDelayMinSeconds,
    serverState?.round_delay_max_seconds ?? DEFAULT_CRAWL_STRATEGY.roundDelayMaxSeconds
  );
  return {
    maxPagesPerRound,
    pageRequestDelayMinSeconds: pageDelayRange.minSeconds,
    pageRequestDelayMaxSeconds: pageDelayRange.maxSeconds,
    roundDelayMinSeconds: roundDelayRange.minSeconds,
    roundDelayMaxSeconds: roundDelayRange.maxSeconds,
  };
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

function createSyncRunId() {
  return `sync-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function clampProgressPercent(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
}

function clearSyncProgressPatch(overrides = {}) {
  return {
    syncInProgress: false,
    syncProgressPercent: 0,
    syncProgressStage: "",
    syncProgressLabel: "",
    syncProgressDetail: "",
    syncRunId: "",
    syncProgressUpdatedAt: "",
    ...overrides,
  };
}

async function updateSyncProgress(progress) {
  const syncProgressPercent = clampProgressPercent(progress.percent);
  await storageSet({
    syncInProgress: true,
    syncProgressPercent,
    syncProgressStage: progress.stage || "",
    syncProgressLabel: progress.label || "",
    syncProgressDetail: progress.detail || "",
    syncRunId: progress.syncRunId || activeSyncRunId,
    syncProgressUpdatedAt: new Date().toISOString(),
  });
  await setBadge(`${syncProgressPercent}%`, progress.color || SYNC_BADGE_COLOR);
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

function sleep(ms) {
  if (ms <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function bridgePushWithProgress(config, payload, syncRunId) {
  let polling = true;
  const pollProgress = (async () => {
    while (polling) {
      try {
        const progress = await bridgeFetch(
          config,
          `/api/bridge/progress?sync_run_id=${encodeURIComponent(syncRunId)}`
        );
        if (progress?.sync_run_id === syncRunId && (progress.in_progress || Number(progress.percent) > 0)) {
          await updateSyncProgress({
            syncRunId,
            percent: progress.percent,
            stage: progress.stage,
            label: progress.label,
            detail: progress.detail,
          });
        }
      } catch (_error) {
        // 进度轮询失败时不打断主同步流程。
      }
      if (!polling) {
        break;
      }
      await sleep(600);
    }
  })();

  try {
    return await bridgeFetch(config, "/api/bridge/push", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        sync_run_id: syncRunId,
      }),
    });
  } finally {
    polling = false;
    await pollProgress;
  }
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

async function getAutostartConfig(config = null) {
  const bridgeConfig = config || await getConfig();
  const response = await bridgeFetch(bridgeConfig, "/api/bridge/autostart");
  return response?.status || null;
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

async function saveAutostartConfig(payload) {
  const config = await getConfig();
  const response = await bridgeFetch(config, "/api/bridge/autostart", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      enabled: Boolean(payload.enabled),
      use_tray: Boolean(payload.useTray),
      launch_browser: Boolean(payload.launchBrowser),
      browser_url: payload.browserUrl || "",
    }),
  });
  return response?.status || null;
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
    ...clearSyncProgressPatch(),
  });
  await setBadge("", "#188038");
  return response;
}

async function getCrawlData(params = {}) {
  const config = await getConfig();
  const search = new URLSearchParams();
  const page = Math.max(1, Number(params.page) || 1);
  const pageSize = Math.max(1, Number(params.pageSize) || 10);
  search.set("page", String(page));
  search.set("page_size", String(pageSize));

  const optionalParams = {
    keyword: params.keyword,
    tag: params.tag,
    access_level: params.accessLevel,
    category_name: params.categoryName,
    author: params.author,
    notification_status: params.notificationStatus,
  };
  for (const [key, value] of Object.entries(optionalParams)) {
    const normalized = String(value || "").trim();
    if (normalized) {
      search.set(key, normalized);
    }
  }

  return bridgeFetch(config, `/api/bridge/crawl-data?${search.toString()}`);
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

async function executeSync(trigger = "manual") {
  const config = await getConfig();
  if (!config.syncEnabled) {
    await saveStatus({
      lastError: "",
      lastSyncTrigger: trigger,
      ...clearSyncProgressPatch(),
    });
    await setBadge("OFF", "#777777");
    return { ok: false, reason: "disabled" };
  }

  const syncRunId = createSyncRunId();
  activeSyncRunId = syncRunId;
  await updateSyncProgress({
    syncRunId,
    percent: 5,
    stage: "prepare",
    label: "准备同步",
    detail: "正在读取本地服务状态",
  });
  try {
    const serverState = await bridgeFetch(config, "/api/bridge/state");
    const effectiveCrawlStrategy = resolveCrawlStrategyConfig(config, serverState);
    await updateSyncProgress({
      syncRunId,
      percent: 12,
      stage: "site-state",
      label: "检查登录状态",
      detail: "正在确认当前浏览器是否已登录 linux.do",
    });
    const { siteState, syncResponse } = await withLinuxDoTab(async (tabId) => {
      const currentSiteState = await ensureSiteStateResponse(
        await sendMessageToLinuxTab(tabId, { type: "fetch-linuxdo-state" })
      );

      if (serverState.require_login && !currentSiteState.loggedIn) {
        return {
          siteState: currentSiteState,
          syncResponse: null,
        };
      }

      const currentSyncResponse = await ensureTopicsResponse(
        await sendMessageToLinuxTab(tabId, {
          type: "fetch-linuxdo-topics",
          syncRunId,
          loggedIn: Boolean(currentSiteState.loggedIn),
          categories: currentSiteState.categories || [],
          lastSeenTopicId: serverState.last_seen_topic_id ?? null,
          bootstrapLimit: Number(serverState.bootstrap_limit) || 30,
          maxPagesPerRun: effectiveCrawlStrategy.maxPagesPerRound,
          pushBatchSize: Math.max(1, Number(config.pushBatchSize) || 5),
          pageRequestDelayMinSeconds: effectiveCrawlStrategy.pageRequestDelayMinSeconds,
          pageRequestDelayMaxSeconds: effectiveCrawlStrategy.pageRequestDelayMaxSeconds,
          roundDelayMinSeconds: effectiveCrawlStrategy.roundDelayMinSeconds,
          roundDelayMaxSeconds: effectiveCrawlStrategy.roundDelayMaxSeconds,
          backwardFetchMaxDays: Math.max(0.01, Number(config.backwardFetchMaxDays) || 1),
        })
      );

      return {
        siteState: currentSiteState,
        syncResponse: currentSyncResponse,
      };
    });
    const loggedIn = siteState.loggedIn;

    if (serverState.require_login && !loggedIn) {
      const status = await saveStatus({
        loggedIn: false,
        lastError: "当前浏览器未登录 linux.do",
        lastSyncTrigger: trigger,
        lastServerState: serverState,
        ...clearSyncProgressPatch(),
      });
      await setBadge("LOG", "#d93025");
      return { ok: false, reason: "login_required", status };
    }

    const count = Number(syncResponse?.storedCount) || 0;
    await saveStatus({
      lastSyncAt: new Date().toISOString(),
      lastSyncTrigger: trigger,
      lastSyncCount: count,
      lastError: "",
      loggedIn,
      lastServerState: serverState,
      ...clearSyncProgressPatch(),
    });
    await setBadge(loggedIn ? (count > 0 ? String(Math.min(count, 99)) : "OK") : "LOG", loggedIn ? "#188038" : "#d93025");
    return { ok: true, storedCount: count };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await saveStatus({
      lastError: message,
      lastSyncTrigger: trigger,
      ...clearSyncProgressPatch(),
    });
    await setBadge("ERR", "#d93025");
    return { ok: false, error: message };
  } finally {
    activeSyncRunId = "";
  }
}

async function runSync(trigger = "manual") {
  if (activeSyncPromise) {
    return {
      ok: false,
      reason: "busy",
      deferred: trigger === "alarm",
      status: await getStatus(),
    };
  }

  activeSyncPromise = executeSync(trigger);
  try {
    return await activeSyncPromise;
  } finally {
    activeSyncPromise = null;
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
      const status = await getStatus();
      const serverState = await bridgeFetch(config, "/api/bridge/state").catch(() => null);
      let nextStatus = status;
      if (!status.syncInProgress) {
        const siteState = await fetchSiteStateFromTab().catch(() => ({ loggedIn: false }));
        if (status.loggedIn !== Boolean(siteState.loggedIn)) {
          nextStatus = await saveStatus({ loggedIn: Boolean(siteState.loggedIn) });
        }
      }
      const aiConfig = await getAiConfig(config).catch(() => null);
      sendResponse({
        config,
        status: nextStatus,
        aiConfigSummary: buildAiConfigSummary(aiConfig),
        serverState,
      });
      return;
    }

    if (message?.type === "sync-progress") {
      if (!activeSyncRunId || message.syncRunId !== activeSyncRunId) {
        sendResponse({ ok: false, ignored: true });
        return;
      }
      await updateSyncProgress({
        syncRunId: message.syncRunId,
        percent: message.percent,
        stage: message.stage,
        label: message.label,
        detail: message.detail,
      });
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "bridge-push-topic-batch") {
      if (!activeSyncRunId || message.syncRunId !== activeSyncRunId) {
        sendResponse({ ok: false, error: "sync_run_mismatch" });
        return;
      }

      const config = await getConfig();
      const response = await bridgePushWithProgress(
        config,
        {
          trigger: message.trigger || "manual",
          browser: "chrome-extension",
          extensionVersion: chrome.runtime.getManifest().version,
          fetchedAt: new Date().toISOString(),
          logged_in: Boolean(message.loggedIn),
          categories: Array.isArray(message.categories) ? message.categories : [],
          topics: Array.isArray(message.topics) ? message.topics : [],
          streaming: true,
          final_batch: Boolean(message.finalBatch),
          batch_index: Math.max(1, Number(message.batchIndex) || 1),
        },
        message.syncRunId
      );
      sendResponse({
        ok: true,
        storedCount: Number(response?.stored_count) || 0,
        storedCountTotal: Number(response?.stored_count_total) || 0,
        lastSeenTopicId: response?.last_seen_topic_id ?? null,
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
        maxPagesPerRound: Math.max(1, Number(message.maxPagesPerRound) || DEFAULT_CRAWL_STRATEGY.maxPagesPerRound),
        pageRequestDelayMinSeconds: Math.max(0, Number(message.pageRequestDelayMinSeconds) || 0),
        pageRequestDelayMaxSeconds: Math.max(0, Number(message.pageRequestDelayMaxSeconds) || 0),
        roundDelayMinSeconds: Math.max(0, Number(message.roundDelayMinSeconds) || 0),
        roundDelayMaxSeconds: Math.max(0, Number(message.roundDelayMaxSeconds) || 0),
        pageRequestIntervalSeconds: null,
        backwardFetchMaxDays: Math.max(0.01, Number(message.backwardFetchMaxDays) || 1),
        pushBatchSize: Math.max(1, Number(message.pushBatchSize) || 5),
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

    if (message?.type === "get-autostart-config") {
      sendResponse({
        ok: true,
        status: await getAutostartConfig(),
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

    if (message?.type === "save-autostart-config") {
      sendResponse({
        ok: true,
        status: await saveAutostartConfig({
          enabled: Boolean(message.enabled),
          launchBrowser: Boolean(message.launchBrowser),
          browserUrl: message.browserUrl,
        }),
      });
      return;
    }

    if (message?.type === "sync-now") {
      sendResponse(await runSync("manual"));
      return;
    }

    if (message?.type === "restart-backend") {
      const config = await getConfig();
      const response = await bridgeFetch(config, "/api/bridge/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      sendResponse({ ok: true, message: response?.message || "后端将在 1 秒后重启。" });
      return;
    }

    if (message?.type === "clear-crawl-data") {
      sendResponse(await clearCrawlData());
      return;
    }

    if (message?.type === "get-crawl-data") {
      sendResponse({
        ok: true,
        ...(await getCrawlData({
          page: message.page,
          pageSize: message.pageSize,
          keyword: message.keyword,
          tag: message.tag,
          accessLevel: message.accessLevel,
          categoryName: message.categoryName,
          author: message.author,
          notificationStatus: message.notificationStatus,
        })),
      });
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
