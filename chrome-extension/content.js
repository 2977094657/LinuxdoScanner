async function linuxFetchJson(path) {
  const response = await fetch(path, {
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

function sleep(ms) {
  if (ms <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pageLoggedIn() {
  return Boolean(
    document.querySelector("#current-user") ||
    document.querySelector(".user-menu.revamped") ||
    document.querySelector("a[href^='/u/']")
  );
}

async function fetchSiteState() {
  const site = await linuxFetchJson("/site.json");
  return {
    loggedIn: Boolean(site.current_user) || pageLoggedIn(),
    categories: site.categories || [],
    currentUser: site.current_user || null,
  };
}

function buildPageRequestPacing(message) {
  return {
    intervalMs: Math.max(1, Number(message.pageRequestIntervalSeconds) || 10) * 1000,
    burstCount: Math.max(1, Number(message.pageRequestBurstCount) || 10),
    cooldownMs: Math.max(0, Number(message.pageRequestCooldownSeconds) || 180) * 1000,
  };
}

function clampProgressPercent(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
}

function computeProgressPercent(base, span, completed, total) {
  const safeTotal = Math.max(1, Number(total) || 1);
  return clampProgressPercent(base + (span * completed) / safeTotal);
}

async function reportSyncProgress(syncRunId, progress) {
  if (!syncRunId) {
    return;
  }
  try {
    await chrome.runtime.sendMessage({
      type: "sync-progress",
      syncRunId,
      percent: progress.percent,
      stage: progress.stage,
      label: progress.label,
      detail: progress.detail,
    });
  } catch (_error) {
    // 忽略弹窗关闭或后台切换导致的短暂消息失败。
  }
}

async function waitForNextPageSlot(scheduleState, pacing) {
  if (scheduleState.requestsStarted === 0) {
    const now = Date.now();
    scheduleState.lastScheduledAt = now;
    return now;
  }

  let nextScheduledAt = scheduleState.lastScheduledAt + pacing.intervalMs;
  while (nextScheduledAt <= Date.now()) {
    nextScheduledAt += pacing.intervalMs;
  }

  if (scheduleState.cooldownUntil && nextScheduledAt < scheduleState.cooldownUntil) {
    nextScheduledAt = scheduleState.cooldownUntil;
  }

  scheduleState.lastScheduledAt = nextScheduledAt;
  await sleep(Math.max(0, nextScheduledAt - Date.now()));
  return nextScheduledAt;
}

function markPageRequestStarted(scheduleState, pacing, scheduledAt) {
  scheduleState.requestsStarted += 1;
  if (scheduleState.requestsStarted % pacing.burstCount === 0 && pacing.cooldownMs > 0) {
    scheduleState.cooldownUntil = scheduledAt + pacing.cooldownMs;
    return;
  }
  scheduleState.cooldownUntil = 0;
}

async function collectTopicDocuments(lastSeenTopicId, bootstrapLimit, maxPagesPerRun, pacing, syncRunId) {
  const selected = [];
  const seen = new Set();
  const scheduleState = {
    requestsStarted: 0,
    lastScheduledAt: 0,
    cooldownUntil: 0,
  };

  await reportSyncProgress(syncRunId, {
    percent: 20,
    stage: "list",
    label: "抓取主题列表",
    detail: `准备扫描最新主题，最多 ${maxPagesPerRun} 页`,
  });

  for (let page = 0; page < maxPagesPerRun; page += 1) {
    const currentPage = page + 1;
    await reportSyncProgress(syncRunId, {
      percent: computeProgressPercent(20, 30, page, maxPagesPerRun),
      stage: "list",
      label: "抓取主题列表",
      detail: `正在请求第 ${currentPage}/${maxPagesPerRun} 页`,
    });
    const scheduledAt = await waitForNextPageSlot(scheduleState, pacing);
    markPageRequestStarted(scheduleState, pacing, scheduledAt);

    const suffix = page === 0 ? "/latest.json?order=created" : `/latest.json?order=created&page=${page}`;
    const data = await linuxFetchJson(suffix);
    const topics = (((data || {}).topic_list || {}).topics) || [];
    if (!topics.length) {
      await reportSyncProgress(syncRunId, {
        percent: computeProgressPercent(20, 30, currentPage, maxPagesPerRun),
        stage: "list",
        label: "主题列表抓取完成",
        detail: `第 ${currentPage} 页已经没有更多主题`,
      });
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
          await reportSyncProgress(syncRunId, {
            percent: 55,
            stage: "detail",
            label: "抓取主题详情",
            detail: `已选出 ${selected.length} 个主题，开始补全详情`,
          });
          return fetchTopicDetails(selected, syncRunId);
        }
        continue;
      }

      if (topicId <= lastSeenTopicId) {
        await reportSyncProgress(syncRunId, {
          percent: 55,
          stage: "detail",
          label: "抓取主题详情",
          detail: `扫描到已同步边界，开始处理 ${selected.length} 个新主题`,
        });
        return fetchTopicDetails(selected, syncRunId);
      }

      selected.push(topic);
    }

    await reportSyncProgress(syncRunId, {
      percent: computeProgressPercent(20, 30, currentPage, maxPagesPerRun),
      stage: "list",
      label: "分析主题列表",
      detail: `已扫描第 ${currentPage}/${maxPagesPerRun} 页，暂存 ${selected.length} 个主题`,
    });
  }

  return fetchTopicDetails(selected, syncRunId);
}

async function fetchTopicDetails(summaries, syncRunId) {
  if (!summaries.length) {
    await reportSyncProgress(syncRunId, {
      percent: 82,
      stage: "detail",
      label: "没有发现新主题",
      detail: "本轮无需抓取主题详情，准备推送结果",
    });
    return [];
  }

  await reportSyncProgress(syncRunId, {
    percent: 55,
    stage: "detail",
    label: "抓取主题详情",
    detail: `共需处理 ${summaries.length} 个主题`,
  });
  const documents = [];
  for (let index = 0; index < summaries.length; index += 1) {
    const summary = summaries[index];
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
    await reportSyncProgress(syncRunId, {
      percent: computeProgressPercent(55, 27, index + 1, summaries.length),
      stage: "detail",
      label: "抓取主题详情",
      detail: `已完成 ${index + 1}/${summaries.length} 个主题`,
    });
  }
  return documents;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "fetch-linuxdo-state") {
      sendResponse(await fetchSiteState());
      return;
    }

    if (message?.type === "fetch-linuxdo-topics") {
      const siteState = await fetchSiteState();
      const pacing = buildPageRequestPacing(message);
      const topics = await collectTopicDocuments(
        message.lastSeenTopicId ?? null,
        Number(message.bootstrapLimit) || 30,
        Number(message.maxPagesPerRun) || 10,
        pacing,
        message.syncRunId || "",
      );
      sendResponse({
        ok: true,
        loggedIn: siteState.loggedIn,
        categories: siteState.categories,
        topics,
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
