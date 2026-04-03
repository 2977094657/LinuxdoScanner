/* =====================================================
   LinuxDoScanner – Options Page JS
   ===================================================== */

async function sendMessage(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (response?.ok === false) {
    throw new Error(response.detail || response.error || "请求失败");
  }
  return response;
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stripHtmlToText(html) {
  if (!html) {
    return "";
  }
  const template = document.createElement("template");
  template.innerHTML = String(html);
  return (template.content.textContent || "")
    .replace(/\s+/g, " ")
    .trim();
}

function isSafeHttpUrl(value) {
  if (!value) {
    return false;
  }
  try {
    const parsed = new URL(String(value));
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (_error) {
    return false;
  }
}

function normalizeStringArray(value) {
  return Array.isArray(value)
    ? value.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function formatAccessLevelLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized || normalized === "public") {
    return "公开";
  }
  if (normalized.startsWith("lv")) {
    return normalized.toUpperCase();
  }
  return normalized;
}

function normalizeCrawlPageSize(value) {
  return Math.max(1, Math.min(100, Number(value) || 10));
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

function resolveBridgeStrategy(config, serverState = null) {
  const legacyPageDelaySeconds = toOptionalNonNegativeInteger(config.pageRequestIntervalSeconds);
  const maxPagesPerRound = Math.max(
    1,
    toOptionalNonNegativeInteger(config.maxPagesPerRound) ??
      toOptionalNonNegativeInteger(serverState?.max_pages_per_run) ??
      10
  );
  const pageDelayRange = normalizeDelayRange(
    config.pageRequestDelayMinSeconds ?? legacyPageDelaySeconds,
    config.pageRequestDelayMaxSeconds ?? legacyPageDelaySeconds,
    serverState?.page_request_delay_min_seconds ?? 1,
    serverState?.page_request_delay_max_seconds ?? 10
  );
  const roundDelayRange = normalizeDelayRange(
    config.roundDelayMinSeconds,
    config.roundDelayMaxSeconds,
    serverState?.round_delay_min_seconds ?? 1,
    serverState?.round_delay_max_seconds ?? 180
  );
  return {
    maxPagesPerRound,
    pageRequestDelayMinSeconds: pageDelayRange.minSeconds,
    pageRequestDelayMaxSeconds: pageDelayRange.maxSeconds,
    roundDelayMinSeconds: roundDelayRange.minSeconds,
    roundDelayMaxSeconds: roundDelayRange.maxSeconds,
  };
}

let feedbackTimer = null;
let preservedBridgeToken = "";
let crawlKeywordTimer = null;
let latestAutostartStatus = null;

const crawlDataState = {
  page: 1,
  pageSize: 10,
  keyword: "",
  tag: "",
  accessLevel: "",
  categoryName: "",
  author: "",
  notificationStatus: "",
  total: 0,
  totalPages: 1,
  hasLoaded: false,
  isLoading: false,
};

function setCardState(id, state) {
  const element = document.getElementById(id);
  if (element) {
    element.dataset.state = state;
  }
}

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

async function withButtonBusy(buttonId, busyText, action) {
  const button = document.getElementById(buttonId);
  if (!button) {
    return action();
  }

  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.textContent = busyText;
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.innerHTML = originalHtml;
  }
}

function handleActionError(error) {
  const message = error instanceof Error ? error.message : String(error);
  showFeedback(message, "danger");
  setText("lastError", message);
  setText("lastModelSyncError", message);
  setText("feishuStatus", message);
  setCrawlStatus(message, "danger");

  const errorSpotlight = document.getElementById("errorSpotlight");
  if (errorSpotlight) {
    errorSpotlight.dataset.tone = "danger";
  }
}

function fillBridgeConfig(config, serverState = null) {
  const strategy = resolveBridgeStrategy(config, serverState);
  document.getElementById("serverUrl").value = config.serverUrl || "";
  preservedBridgeToken = config.bridgeToken || "";
  document.getElementById("intervalMinutes").value = String(config.intervalMinutes || 5);
  document.getElementById("maxPagesPerRound").value = String(strategy.maxPagesPerRound);
  document.getElementById("pageRequestDelayMinSeconds").value = String(strategy.pageRequestDelayMinSeconds);
  document.getElementById("pageRequestDelayMaxSeconds").value = String(strategy.pageRequestDelayMaxSeconds);
  document.getElementById("roundDelayMinSeconds").value = String(strategy.roundDelayMinSeconds);
  document.getElementById("roundDelayMaxSeconds").value = String(strategy.roundDelayMaxSeconds);
  setCardState("bridgeCard", "armed");
}

function updateAutostartControlAvailability() {
  const status = latestAutostartStatus || {};
  const supported = status.supported !== false;
  const enabled = Boolean(document.getElementById("autostartEnabled").checked);
  const useTrayToggle = document.getElementById("autostartUseTray");
  const launchBrowserToggle = document.getElementById("autostartLaunchBrowser");
  const browserUrlInput = document.getElementById("autostartBrowserUrl");
  const saveButton = document.getElementById("saveAutostartButton");

  if (useTrayToggle) {
    useTrayToggle.disabled = !supported || !enabled;
  }
  if (launchBrowserToggle) {
    launchBrowserToggle.disabled = !supported || !enabled;
  }
  if (browserUrlInput) {
    browserUrlInput.disabled = !supported || !enabled || !Boolean(launchBrowserToggle?.checked);
  }
  if (saveButton) {
    saveButton.disabled = !supported;
  }
}

function describeAutostartStatus(status) {
  if (!status) {
    return "无法读取开机启动状态";
  }
  if (status.supported === false) {
    return status.reason || "当前环境不支持自动写入开机启动";
  }
  if (!status.enabled) {
    return "未启用，下次登录系统时不会自动拉起本地服务";
  }
  const trayText = status.use_tray === false ? "以前台服务方式运行，不显示托盘" : "会显示系统托盘";
  if (status.launch_browser) {
    return `已启用，下次登录会自动拉起本地服务，${trayText}，并在需要时唤醒浏览器`;
  }
  return `已启用，下次登录会自动拉起本地服务，${trayText}`;
}

function fillAutostartConfig(status) {
  latestAutostartStatus = status || {
    supported: true,
    enabled: false,
    use_tray: true,
    launch_browser: false,
    browser_url: "https://linux.do/latest?order=created",
    browser_executable: "",
    reason: "",
  };

  const supported = latestAutostartStatus.supported !== false;
  const enabled = Boolean(latestAutostartStatus.enabled);
  const useTray = latestAutostartStatus.use_tray !== false;
  const launchBrowser = Boolean(latestAutostartStatus.launch_browser);
  const browserUrl = latestAutostartStatus.browser_url || "https://linux.do/latest?order=created";
  const browserExecutable = latestAutostartStatus.browser_executable || "";

  document.getElementById("autostartEnabled").checked = enabled;
  document.getElementById("autostartUseTray").checked = useTray;
  document.getElementById("autostartLaunchBrowser").checked = launchBrowser;
  document.getElementById("autostartBrowserUrl").value = browserUrl;
  document.getElementById("autostartBrowserExecutable").value =
    browserExecutable || "未检测到，可在 config/settings.toml 的 [browser].executable 中手动指定";
  document.getElementById("autostartStatusText").textContent = describeAutostartStatus(latestAutostartStatus);

  const unsupportedNotice = document.getElementById("autostartUnsupportedNotice");
  if (unsupportedNotice) {
    unsupportedNotice.hidden = supported;
    unsupportedNotice.textContent =
      latestAutostartStatus.reason || "当前环境不支持自动写入开机启动。";
  }

  setCardState("autostartCard", supported ? (enabled ? "ready" : "idle") : "warning");
  updateAutostartControlAvailability();
}

function collectAutostartPayload() {
  return {
    enabled: document.getElementById("autostartEnabled").checked,
    useTray: document.getElementById("autostartUseTray").checked,
    launchBrowser: document.getElementById("autostartLaunchBrowser").checked,
    browserUrl: document.getElementById("autostartBrowserUrl").value.trim(),
  };
}

function normalizeKeywordItems(value) {
  return String(value || "")
    .split(/[,\n，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

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

function populateSelect(selectId, values, placeholder, selectedValue, formatLabel = null) {
  const select = document.getElementById(selectId);
  if (!select) {
    return;
  }

  select.innerHTML = "";
  const blankOption = document.createElement("option");
  blankOption.value = "";
  blankOption.textContent = placeholder;
  select.appendChild(blankOption);

  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = formatLabel ? formatLabel(value) : value;
    select.appendChild(option);
  }

  const shouldKeepSelection = values.includes(selectedValue);
  select.value = shouldKeepSelection ? selectedValue : "";
}

function syncCrawlStateFromControls() {
  const pageSizeInput = document.getElementById("crawlPageSize");
  const normalizedPageSize = normalizeCrawlPageSize(pageSizeInput.value);
  crawlDataState.keyword = document.getElementById("crawlKeyword").value.trim();
  crawlDataState.tag = document.getElementById("crawlTag").value.trim();
  crawlDataState.accessLevel = document.getElementById("crawlAccessLevel").value.trim();
  crawlDataState.categoryName = document.getElementById("crawlCategory").value.trim();
  crawlDataState.author = document.getElementById("crawlAuthor").value.trim();
  crawlDataState.notificationStatus = document.getElementById("crawlNotificationStatus").value.trim();
  crawlDataState.pageSize = normalizedPageSize;
  pageSizeInput.value = String(normalizedPageSize);
}

function fillCrawlFilterOptions(filters) {
  const tagValues = normalizeStringArray(filters.tags);
  const accessLevelValues = normalizeStringArray(filters.access_levels);
  const categoryValues = normalizeStringArray(filters.categories);

  populateSelect("crawlTag", tagValues, "全部标签", crawlDataState.tag);
  populateSelect(
    "crawlAccessLevel",
    accessLevelValues,
    "全部等级",
    crawlDataState.accessLevel,
    formatAccessLevelLabel
  );
  populateSelect("crawlCategory", categoryValues, "全部分区", crawlDataState.categoryName);
  document.getElementById("crawlPageSize").value = String(normalizeCrawlPageSize(crawlDataState.pageSize));
  document.getElementById("crawlAuthor").value = crawlDataState.author;
  document.getElementById("crawlNotificationStatus").value = crawlDataState.notificationStatus;
}

function setCrawlStatus(message, tone = "neutral") {
  const badge = document.getElementById("crawlListStatus");
  if (!badge) {
    return;
  }
  badge.textContent = message;
  badge.dataset.tone = tone;
}

function renderFieldBlock(label, contentHtml, extraClass = "") {
  const className = extraClass ? `field-block ${extraClass}` : "field-block";
  return `
    <section class="${className}">
      <span class="field-label">${escapeHtml(label)}</span>
      <div class="field-value">${contentHtml}</div>
    </section>
  `;
}

function renderTextValue(value, emptyText = "未填写") {
  const text = String(value ?? "").trim();
  return text
    ? escapeHtml(text)
    : `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
}

function renderParagraphValue(value, emptyText = "未填写") {
  const text = String(value ?? "").trim();
  return text
    ? `<p class="field-paragraph">${escapeHtml(text)}</p>`
    : `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
}

function renderLinkValue(url, label) {
  const normalizedUrl = String(url || "").trim();
  if (!normalizedUrl) {
    return '<span class="field-empty">未填写</span>';
  }
  if (!isSafeHttpUrl(normalizedUrl)) {
    return escapeHtml(normalizedUrl);
  }
  return `
    <a class="inline-link" href="${escapeHtml(normalizedUrl)}" target="_blank" rel="noreferrer">
      ${escapeHtml(label || normalizedUrl)}
    </a>
  `;
}

function renderChipListValue(value, emptyText = "无") {
  const items = normalizeStringArray(value);
  if (!items.length) {
    return `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
  }
  return `
    <div class="tag-chip-row">
      ${items.map((item) => `<span class="tag-chip">${escapeHtml(item)}</span>`).join("")}
    </div>
  `;
}

function renderReasonListValue(value, emptyText = "无") {
  const items = normalizeStringArray(value);
  if (!items.length) {
    return `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
  }
  return `
    <ul class="reason-list">
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function renderLinkListValue(value, emptyText = "无") {
  const items = normalizeStringArray(value);
  if (!items.length) {
    return `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
  }
  return `
    <div class="link-list">
      ${items.map((item) => `<div>${renderLinkValue(item, item)}</div>`).join("")}
    </div>
  `;
}

function renderContentValue(value, emptyText = "暂无正文内容") {
  const text = String(value || "").trim();
  return text
    ? `<div class="rich-text-preview">${escapeHtml(text)}</div>`
    : `<span class="field-empty">${escapeHtml(emptyText)}</span>`;
}

function renderNotificationHeader(item) {
  const chips = [
    item.requires_notification
      ? '<span class="status-chip" data-tone="warning">命中通知条件</span>'
      : '<span class="status-chip" data-tone="neutral">仅保存，不通知</span>',
    item.notification_sent_at
      ? '<span class="status-chip" data-tone="success">已发送通知</span>'
      : '<span class="status-chip" data-tone="neutral">尚未发送</span>',
  ];
  return chips.join("");
}

function renderCrawlCard(item) {
  const title = String(item.title || "未命名主题").trim();
  const displayName = String(item.author_display_name || "").trim();
  const username = String(item.author_username || "").trim();
  const author = displayName || username || "未知作者";
  const rawHtml = String(item.first_post_html || "").trim();
  const topicUrl = isSafeHttpUrl(item.url) ? escapeHtml(item.url) : "";

  // 构建头像 HTML
  const avatarSize = 40;
  let avatarHtml;
  if (isSafeHttpUrl(item.author_avatar_url)) {
    avatarHtml = `<img class="post-avatar" src="${escapeHtml(item.author_avatar_url)}" alt="${escapeHtml(author)}" width="${avatarSize}" height="${avatarSize}" loading="lazy">`;
  } else {
    // 无头像时用首字母占位
    const initial = (author.charAt(0) || "?").toUpperCase();
    avatarHtml = `<span class="post-avatar post-avatar-fallback">${escapeHtml(initial)}</span>`;
  }

  // 分区标签
  const categoryHtml = item.category_name
    ? `<span class="post-category">${escapeHtml(item.category_name)}</span>`
    : "";

  // 标签列表
  const tags = normalizeStringArray(item.tags_json);
  const tagsHtml = tags.length
    ? tags.map((t) => `<span class="post-tag">${escapeHtml(t)}</span>`).join("")
    : "";

  // 时间显示
  const timeText = item.created_at ? formatTimestamp(item.created_at) : "";
  const lastActiveText = item.last_posted_at ? formatTimestamp(item.last_posted_at) : "";

  // 统计数据
  const replyCount = item.reply_count ?? 0;
  const likeCount = item.like_count ?? 0;
  const viewCount = item.view_count ?? 0;

  // AI 标签
  const aiLabel = String(item.ai_label || "").trim();
  const aiLabels = normalizeStringArray(item.ai_labels_json);
  const aiSummary = String(item.ai_summary || "").trim();
  const aiReasons = normalizeStringArray(item.ai_reasons_json);
  const externalLinks = normalizeStringArray(item.external_links_json);
  const hasAiContent = aiLabel || aiLabels.length || aiSummary || aiReasons.length;

  // 通知状态
  const notifChips = [];
  if (item.requires_notification) {
    notifChips.push('<span class="status-chip" data-tone="warning">命中通知</span>');
  }
  if (item.notification_sent_at) {
    notifChips.push('<span class="status-chip" data-tone="success">已发送</span>');
  }

  // 正文：直接渲染 HTML 内容，放入沙箱容器
  const contentHtml = rawHtml
    ? `<div class="post-content-html">${rawHtml}</div>`
    : '<p class="post-preview post-preview-empty">暂无正文内容</p>';

  return `
    <article class="post-card"${topicUrl ? ` data-href="${topicUrl}"` : ""}>
      <div class="post-header">
        <div class="post-author-bar">
          ${avatarHtml}
          <div class="post-author-info">
            <span class="post-author-name">${escapeHtml(author)}</span>
            ${username && displayName && username !== displayName
              ? `<span class="post-author-username">@${escapeHtml(username)}</span>`
              : ""}
          </div>
          ${timeText ? `<time class="post-time" title="${escapeHtml(timeText)}">${escapeHtml(timeText)}</time>` : ""}
        </div>
        ${notifChips.length ? `<div class="post-notif-chips">${notifChips.join("")}</div>` : ""}
      </div>

      <div class="post-body">
        ${topicUrl
          ? `<a class="post-title" href="${topicUrl}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
          : `<div class="post-title">${escapeHtml(title)}</div>`}

        <div class="post-tags-row">
          ${categoryHtml}${tagsHtml}
        </div>

        ${contentHtml}

        ${externalLinks.length
          ? `<div class="post-links-row">
              <svg class="post-links-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
              <span class="post-links-count">${externalLinks.length} external link${externalLinks.length > 1 ? "s" : ""}</span>
            </div>`
          : ""}
      </div>

      <div class="post-footer">
        <div class="post-stats">
          <span class="post-stat" title="回复">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            ${escapeHtml(String(replyCount))}
          </span>
          <span class="post-stat" title="点赞">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
            ${escapeHtml(String(likeCount))}
          </span>
          <span class="post-stat" title="浏览">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            ${escapeHtml(String(viewCount))}
          </span>
        </div>

        ${lastActiveText
          ? `<span class="post-last-active" title="最后活跃">最后活跃 ${escapeHtml(lastActiveText)}</span>`
          : ""}
      </div>

      ${hasAiContent
        ? `<details class="post-ai-section">
            <summary class="post-ai-toggle">
              <svg class="post-ai-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
              AI 分析
              ${aiLabel ? `<span class="post-ai-main-label">${escapeHtml(aiLabel)}</span>` : ""}
            </summary>
            <div class="post-ai-content">
              ${aiLabels.length
                ? `<div class="post-ai-field">
                    <span class="post-ai-field-label">标签</span>
                    <div class="post-ai-chips">${aiLabels.map((l) => `<span class="post-ai-chip">${escapeHtml(l)}</span>`).join("")}</div>
                  </div>`
                : ""}
              ${aiSummary
                ? `<div class="post-ai-field">
                    <span class="post-ai-field-label">摘要</span>
                    <p class="post-ai-summary">${escapeHtml(aiSummary)}</p>
                  </div>`
                : ""}
              ${aiReasons.length
                ? `<div class="post-ai-field">
                    <span class="post-ai-field-label">理由</span>
                    <ul class="post-ai-reasons">${aiReasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>
                  </div>`
                : ""}
              ${externalLinks.length
                ? `<div class="post-ai-field">
                    <span class="post-ai-field-label">外部链接</span>
                    <div class="post-ai-links">${externalLinks.map((link) => {
                      if (isSafeHttpUrl(link)) {
                        return `<a class="post-ai-link" href="${escapeHtml(link)}" target="_blank" rel="noreferrer">${escapeHtml(link)}</a>`;
                      }
                      return `<span>${escapeHtml(link)}</span>`;
                    }).join("")}</div>
                  </div>`
                : ""}
              ${item.ai_provider
                ? `<div class="post-ai-provider">Provider: ${escapeHtml(item.ai_provider)}</div>`
                : ""}
            </div>
          </details>`
        : ""}
    </article>
  `;
}

function updateCrawlPaginationControls() {
  const prevButton = document.getElementById("crawlPrevPageButton");
  const nextButton = document.getElementById("crawlNextPageButton");
  const pageInput = document.getElementById("crawlPageInput");
  if (!prevButton || !nextButton) {
    return;
  }
  prevButton.disabled = crawlDataState.isLoading || crawlDataState.page <= 1;
  nextButton.disabled = crawlDataState.isLoading || crawlDataState.page >= crawlDataState.totalPages;
  if (pageInput) {
    pageInput.value = String(crawlDataState.page);
    pageInput.max = String(crawlDataState.totalPages);
    pageInput.disabled = crawlDataState.isLoading;
  }
}

function renderCrawlData(items) {
  const list = document.getElementById("crawlDataList");
  const emptyState = document.getElementById("crawlEmptyState");
  const hasActiveFilters = Boolean(
    crawlDataState.keyword || crawlDataState.tag || crawlDataState.accessLevel ||
    crawlDataState.categoryName || crawlDataState.author || crawlDataState.notificationStatus
  );

  setText("crawlTotalPages", String(crawlDataState.totalPages));

  if (!items.length) {
    list.innerHTML = "";
    emptyState.hidden = false;
    setText("crawlEmptyTitle", hasActiveFilters ? "没有匹配结果" : "暂无爬取数据");
    setText(
      "crawlEmptyHint",
      hasActiveFilters
        ? "换个关键词，或者放宽标签、等级、分区条件后再试一次。"
        : "当前还没有入库的主题，先执行一次同步再回来查看。"
    );
    setCrawlStatus(crawlDataState.total ? "当前页没有结果" : "暂无数据", "neutral");
    updateCrawlPaginationControls();
    return;
  }

  emptyState.hidden = true;
  list.innerHTML = items.map((item) => renderCrawlCard(item)).join("");

  setCrawlStatus(`已加载 ${items.length} 条结果`, "success");
  updateCrawlPaginationControls();
}

function setCrawlLoading(isLoading) {
  crawlDataState.isLoading = isLoading;
  const controlIds = [
    "crawlKeyword",
    "crawlAuthor",
    "crawlTag",
    "crawlAccessLevel",
    "crawlCategory",
    "crawlNotificationStatus",
    "crawlPageSize",
    "crawlPageInput",
    "applyCrawlFiltersButton",
  ];
  for (const id of controlIds) {
    const element = document.getElementById(id);
    if (element) {
      element.disabled = isLoading;
    }
  }
  updateCrawlPaginationControls();
}

async function loadCrawlData({ page = crawlDataState.page } = {}) {
  syncCrawlStateFromControls();
  const requestedPage = Math.max(1, Number(page) || 1);

  setCrawlLoading(true);
  setCrawlStatus("正在读取爬取数据…", "neutral");

  try {
    const response = await sendMessage({
      type: "get-crawl-data",
      page: requestedPage,
      pageSize: crawlDataState.pageSize,
      keyword: crawlDataState.keyword,
      tag: crawlDataState.tag,
      accessLevel: crawlDataState.accessLevel,
      categoryName: crawlDataState.categoryName,
      author: crawlDataState.author,
      notificationStatus: crawlDataState.notificationStatus,
    });

    crawlDataState.hasLoaded = true;
    crawlDataState.page = Math.max(1, Number(response.pagination?.page) || requestedPage);
    crawlDataState.pageSize = Math.max(1, Number(response.pagination?.page_size) || crawlDataState.pageSize);
    crawlDataState.total = Math.max(0, Number(response.pagination?.total) || 0);
    crawlDataState.totalPages = Math.max(1, Number(response.pagination?.total_pages) || 1);

    fillCrawlFilterOptions(response.filters || {});
    renderCrawlData(Array.isArray(response.items) ? response.items : []);
  } catch (error) {
    const list = document.getElementById("crawlDataList");
    if (list) {
      list.innerHTML = "";
    }
    const emptyState = document.getElementById("crawlEmptyState");
    if (emptyState) {
      emptyState.hidden = false;
    }
    setText("crawlEmptyTitle", "读取失败");
    setText("crawlEmptyHint", error instanceof Error ? error.message : String(error));
    throw error;
  } finally {
    setCrawlLoading(false);
  }
}

function collectBridgePayload() {
  return {
    serverUrl: document.getElementById("serverUrl").value.trim(),
    bridgeToken: preservedBridgeToken,
    intervalMinutes: Number(document.getElementById("intervalMinutes").value),
    maxPagesPerRound: Number(document.getElementById("maxPagesPerRound").value),
    pageRequestDelayMinSeconds: Number(document.getElementById("pageRequestDelayMinSeconds").value),
    pageRequestDelayMaxSeconds: Number(document.getElementById("pageRequestDelayMaxSeconds").value),
    roundDelayMinSeconds: Number(document.getElementById("roundDelayMinSeconds").value),
    roundDelayMaxSeconds: Number(document.getElementById("roundDelayMaxSeconds").value),
    syncEnabled: true,
  };
}

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

function collectNotificationPayload() {
  return {
    feishuEnabled: document.getElementById("feishuEnabled").checked,
    larkCliPath: document.getElementById("larkCliPath").value.trim(),
    feishuChatId: document.getElementById("feishuChatId").value.trim(),
    feishuUserId: document.getElementById("feishuUserId").value.trim(),
  };
}

async function refresh() {
  const [extensionResponse, stateResponse, aiResponse, notificationResponse, autostartResponse] = await Promise.all([
    sendMessage({ type: "get-extension-config" }),
    sendMessage({ type: "get-state" }),
    sendMessage({ type: "get-ai-config" }).catch(() => ({ config: null })),
    sendMessage({ type: "get-notification-config" }).catch(() => ({ config: null })),
    sendMessage({ type: "get-autostart-config" }).catch(() => ({ status: null })),
  ]);

  fillBridgeConfig(extensionResponse.config || {}, stateResponse.serverState || null);
  fillStatus(stateResponse.status || {});
  fillAiConfig(aiResponse.config || {});
  fillNotificationConfig(notificationResponse.config || {});
  fillAutostartConfig(autostartResponse.status || null);

  const crawlSection = document.getElementById("section-crawl-data");
  if ((crawlDataState.hasLoaded || crawlSection?.classList.contains("active")) && !crawlDataState.isLoading) {
    await loadCrawlData({ page: crawlDataState.page });
  }
}

async function saveBridgeConfig() {
  const response = await withButtonBusy("saveBridgeButton", "保存中...", () => sendMessage({
    type: "save-extension-config",
    ...collectBridgePayload(),
  }));
  fillBridgeConfig(response.config || {}, null);
  showFeedback("桥接设置已保存");
}

async function saveAiConfig() {
  const response = await withButtonBusy("saveAiButton", "保存中...", () => sendMessage({
    type: "save-ai-config",
    ...collectAiPayload(),
  }));
  fillAiConfig(response.config || {});
  showFeedback("AI 配置已保存");
}

async function syncModels() {
  const response = await withButtonBusy("syncModelsButton", "同步中...", () => sendMessage({
    type: "sync-ai-models",
    ...collectAiPayload(),
  }));
  fillAiConfig(response.config || {});
  showFeedback("模型列表已同步");
}

async function clearCrawlData() {
  const confirmed = window.confirm("这会清空本地已爬取主题、通知状态和抓取游标，但不会删除 AI 配置。确定继续吗？");
  if (!confirmed) {
    return;
  }
  await withButtonBusy("clearCrawlDataButton", "清空中...", () => sendMessage({ type: "clear-crawl-data" }));
  crawlDataState.page = 1;
  showFeedback("抓取数据库已清空");
}

async function saveNotificationConfig() {
  const response = await withButtonBusy("saveFeishuButton", "保存中...", () => sendMessage({
    type: "save-notification-config",
    ...collectNotificationPayload(),
  }));
  fillNotificationConfig(response.config || {});
  showFeedback("飞书配置已保存");
}

async function testNotificationConfig() {
  const response = await withButtonBusy("testFeishuButton", "发送中...", () => sendMessage({
    type: "test-notification-config",
    ...collectNotificationPayload(),
  }));
  setText("feishuStatus", response.message || "飞书测试消息已发送");
  showFeedback(response.message || "飞书测试消息已发送");
}

async function saveAutostartConfig() {
  const response = await withButtonBusy("saveAutostartButton", "保存中...", () => sendMessage({
    type: "save-autostart-config",
    ...collectAutostartPayload(),
  }));
  fillAutostartConfig(response.status || null);
  showFeedback(response.status?.enabled ? "开机启动设置已保存" : "开机启动已关闭");
}

async function activateSection(targetId, { updateHash = true } = {}) {
  const navItems = document.querySelectorAll(".nav-item");
  const sections = document.querySelectorAll(".content-section");

  navItems.forEach((nav) => {
    nav.classList.toggle("active", nav.dataset.section === targetId);
  });
  sections.forEach((section) => {
    section.classList.toggle("active", section.id === targetId);
  });

  if (updateHash) {
    window.history.replaceState(null, "", `#${targetId}`);
  }

  if (targetId === "section-crawl-data" && !crawlDataState.hasLoaded) {
    await loadCrawlData({ page: 1 });
  }
}

function initNavigation() {
  const navItems = document.querySelectorAll(".nav-item");

  navItems.forEach((item) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      activateSection(item.dataset.section).catch(handleActionError);
    });
  });

  const hashTarget = window.location.hash.replace(/^#/, "");
  const initialTarget = document.getElementById(hashTarget) ? hashTarget : "section-overview";
  activateSection(initialTarget, { updateHash: false }).catch(handleActionError);
}

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

document.getElementById("saveAutostartButton").addEventListener("click", async () => {
  try {
    await saveAutostartConfig();
    await refresh();
  } catch (error) {
    handleActionError(error);
  }
});

["autostartEnabled", "autostartUseTray", "autostartLaunchBrowser"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    updateAutostartControlAvailability();
  });
});

document.getElementById("applyCrawlFiltersButton").addEventListener("click", async () => {
  try {
    crawlDataState.page = 1;
    await loadCrawlData({ page: 1 });
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("crawlKeyword").addEventListener("input", () => {
  if (crawlKeywordTimer) {
    clearTimeout(crawlKeywordTimer);
  }
  crawlKeywordTimer = window.setTimeout(() => {
    crawlDataState.page = 1;
    loadCrawlData({ page: 1 }).catch(handleActionError);
  }, 360);
});

document.getElementById("crawlKeyword").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  if (crawlKeywordTimer) {
    clearTimeout(crawlKeywordTimer);
  }
  crawlDataState.page = 1;
  loadCrawlData({ page: 1 }).catch(handleActionError);
});

["crawlTag", "crawlAccessLevel", "crawlCategory", "crawlNotificationStatus"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    crawlDataState.page = 1;
    loadCrawlData({ page: 1 }).catch(handleActionError);
  });
});

// 作者筛选 — 防抖输入
let crawlAuthorTimer = null;
document.getElementById("crawlAuthor").addEventListener("input", () => {
  if (crawlAuthorTimer) {
    clearTimeout(crawlAuthorTimer);
  }
  crawlAuthorTimer = window.setTimeout(() => {
    crawlDataState.page = 1;
    loadCrawlData({ page: 1 }).catch(handleActionError);
  }, 360);
});

document.getElementById("crawlAuthor").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  if (crawlAuthorTimer) {
    clearTimeout(crawlAuthorTimer);
  }
  crawlDataState.page = 1;
  loadCrawlData({ page: 1 }).catch(handleActionError);
});

function applyCrawlPageSize() {
  const pageSizeInput = document.getElementById("crawlPageSize");
  const normalizedPageSize = normalizeCrawlPageSize(pageSizeInput.value);
  pageSizeInput.value = String(normalizedPageSize);
  if (normalizedPageSize === crawlDataState.pageSize) {
    return;
  }
  crawlDataState.page = 1;
  crawlDataState.pageSize = normalizedPageSize;
  loadCrawlData({ page: 1 }).catch(handleActionError);
}

document.getElementById("crawlPageSize").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  applyCrawlPageSize();
});

document.getElementById("crawlPageSize").addEventListener("blur", () => {
  applyCrawlPageSize();
});

document.getElementById("crawlPageSize").addEventListener("change", () => {
  applyCrawlPageSize();
});

// 页码输入 — 回车或失焦时跳转
document.getElementById("crawlPageInput").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  const targetPage = Math.max(1, Math.min(Number(event.target.value) || 1, crawlDataState.totalPages));
  loadCrawlData({ page: targetPage }).catch(handleActionError);
});

document.getElementById("crawlPageInput").addEventListener("blur", (event) => {
  const targetPage = Math.max(1, Math.min(Number(event.target.value) || 1, crawlDataState.totalPages));
  if (targetPage !== crawlDataState.page) {
    loadCrawlData({ page: targetPage }).catch(handleActionError);
  }
});

document.getElementById("crawlPrevPageButton").addEventListener("click", async () => {
  try {
    if (crawlDataState.page <= 1) {
      return;
    }
    await loadCrawlData({ page: crawlDataState.page - 1 });
  } catch (error) {
    handleActionError(error);
  }
});

document.getElementById("crawlNextPageButton").addEventListener("click", async () => {
  try {
    if (crawlDataState.page >= crawlDataState.totalPages) {
      return;
    }
    await loadCrawlData({ page: crawlDataState.page + 1 });
  } catch (error) {
    handleActionError(error);
  }
});

initNavigation();

refresh().catch((error) => {
  handleActionError(error);
});
