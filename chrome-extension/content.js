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
  const normalizeDelayRange = (minimum, maximum, fallbackMinimum, fallbackMaximum) => {
    let minSeconds = Math.max(0, Math.round(Number(minimum)));
    let maxSeconds = Math.max(0, Math.round(Number(maximum)));
    if (!Number.isFinite(minSeconds)) {
      minSeconds = fallbackMinimum;
    }
    if (!Number.isFinite(maxSeconds)) {
      maxSeconds = fallbackMaximum;
    }
    if (maxSeconds < minSeconds) {
      [minSeconds, maxSeconds] = [maxSeconds, minSeconds];
    }
    return {
      minSeconds,
      maxSeconds,
    };
  };

  return {
    pageDelayRange: normalizeDelayRange(
      message.pageRequestDelayMinSeconds,
      message.pageRequestDelayMaxSeconds,
      1,
      10
    ),
    roundDelayRange: normalizeDelayRange(
      message.roundDelayMinSeconds,
      message.roundDelayMaxSeconds,
      1,
      180
    ),
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

function ensureBridgePushResponse(response) {
  if (!response || response.ok === false) {
    throw new Error(response?.detail || response?.error || "本地服务批次处理失败");
  }
  return response;
}

function pickRandomDelaySeconds(range) {
  const minSeconds = Math.max(0, Number(range?.minSeconds) || 0);
  const maxSeconds = Math.max(0, Number(range?.maxSeconds) || 0);
  if (maxSeconds < minSeconds) {
    return minSeconds;
  }
  if (maxSeconds === 0) {
    return 0;
  }
  return minSeconds + Math.floor(Math.random() * (maxSeconds - minSeconds + 1));
}

async function sleepForRandomDelay(range) {
  const delaySeconds = pickRandomDelaySeconds(range);
  if (delaySeconds > 0) {
    await sleep(delaySeconds * 1000);
  }
  return delaySeconds;
}

async function fetchTopicDocument(summary, syncRunId, options = {}) {
  const topicIndex = Math.max(1, Number(options.topicIndex) || 1);
  const scannedPages = Math.max(0, Number(options.scannedPages) || 0);
  const pendingCount = Math.max(1, Number(options.pendingCount) || 1);
  const batchIndex = Math.max(1, Number(options.batchIndex) || 1);

  await reportSyncProgress(syncRunId, {
    percent: 58,
    stage: "detail",
    label: "立即抓取主题内容",
    detail: `已扫描 ${scannedPages} 页，发现第 ${topicIndex} 个新主题，正在抓取详情并准备放入第 ${batchIndex} 批`,
  });

  let detail = null;
  let detailError = "";
  try {
    detail = await linuxFetchJson(`/t/${encodeURIComponent(summary.slug)}/${summary.id}.json`);
  } catch (error) {
    detailError = error instanceof Error ? error.message : String(error);
  }

  await reportSyncProgress(syncRunId, {
    percent: 62,
    stage: "detail",
    label: "主题内容已就绪",
    detail: `第 ${topicIndex} 个主题内容已抓取，当前待提交队列 ${pendingCount} 个`,
  });

  return {
    summary,
    detail,
    detail_error: detailError,
  };
}

async function pushTopicBatchToBridge(syncRunId, payload) {
  const response = await chrome.runtime.sendMessage({
    type: "bridge-push-topic-batch",
    syncRunId,
    loggedIn: Boolean(payload.loggedIn),
    categories: Array.isArray(payload.categories) ? payload.categories : [],
    topics: Array.isArray(payload.topics) ? payload.topics : [],
    batchIndex: Math.max(1, Number(payload.batchIndex) || 1),
    finalBatch: Boolean(payload.finalBatch),
    trigger: payload.trigger || "manual",
  });
  return ensureBridgePushResponse(response);
}

async function collectTopicDocuments(
  lastSeenTopicId,
  bootstrapLimit,
  maxPagesPerRun,
  pacing,
  syncRunId,
  siteState,
  pushBatchSize
) {
  const pendingDocuments = [];
  const seen = new Set();
  const normalizedPushBatchSize = Math.max(1, Number(pushBatchSize) || 1);
  const normalizedBootstrapLimit = Math.max(1, Number(bootstrapLimit) || 30);
  const normalizedSiteState = {
    loggedIn: Boolean(siteState?.loggedIn),
    categories: Array.isArray(siteState?.categories) ? siteState.categories : [],
  };
  let totalSelected = 0;
  let totalStored = 0;
  let scannedPages = 0;
  let pushedBatchCount = 0;

  async function flushPendingDocuments(finalBatch, reason) {
    const documents = pendingDocuments.splice(0, pendingDocuments.length);
    const hasDocuments = documents.length > 0;
    const batchIndex = hasDocuments ? pushedBatchCount + 1 : Math.max(1, pushedBatchCount);
    if (!hasDocuments && !finalBatch) {
      return;
    }

    if (!hasDocuments) {
      const finalizeLabel = totalSelected > 0 ? "同步收尾" : "没有发现新主题";
      await reportSyncProgress(syncRunId, {
        percent: 84,
        stage: "push",
        label: finalizeLabel,
        detail:
          totalSelected > 0
            ? "所有主题批次已提交，正在通知本地服务收尾并更新同步游标"
            : "本轮没有发现新主题，正在通知本地服务结束同步",
      });
    }

    const pushLabel = hasDocuments
      ? (finalBatch ? "提交最后一批给本地服务" : `提交第 ${batchIndex} 批给本地服务`)
      : "结束本轮同步";
    const pushDetail = hasDocuments
      ? `原因：${reason}；本批 ${documents.length} 个主题，累计已发现 ${totalSelected} 个`
      : `原因：${reason}；累计已发现 ${totalSelected} 个主题`;
    await reportSyncProgress(syncRunId, {
      percent: 88,
      stage: "push",
      label: pushLabel,
      detail: pushDetail,
    });

    const response = await pushTopicBatchToBridge(syncRunId, {
      loggedIn: normalizedSiteState.loggedIn,
      categories: normalizedSiteState.categories,
      topics: documents,
      batchIndex,
      finalBatch,
    });
    pushedBatchCount += hasDocuments ? 1 : 0;
    totalStored = Math.max(totalStored, Number(response.storedCountTotal) || 0);

    await reportSyncProgress(syncRunId, {
      percent: finalBatch ? 96 : 90,
      stage: "push",
      label: finalBatch ? "本地服务已完成收尾" : "当前批次处理完成",
      detail: finalBatch
        ? `本轮累计入库 ${totalStored} 个主题`
        : `第 ${batchIndex} 批已处理完成，本轮累计入库 ${totalStored} 个主题`,
    });
  }

  await reportSyncProgress(syncRunId, {
    percent: 20,
    stage: "list",
    label: "抓取主题列表",
    detail: `准备开始多轮扫描，每轮最多 ${maxPagesPerRun} 页，AI 每 ${normalizedPushBatchSize} 个主题触发一次`,
  });

  let roundIndex = 0;
  let nextPageIndex = 0;
  while (true) {
    roundIndex += 1;
    const roundStartPage = nextPageIndex + 1;
    const roundEndPage = nextPageIndex + maxPagesPerRun;
    await reportSyncProgress(syncRunId, {
      percent: 20,
      stage: "list",
      label: "抓取主题列表",
      detail: `第 ${roundIndex} 轮开始，准备扫描第 ${roundStartPage}-${roundEndPage} 页`,
    });

    let roundFoundAnyTopics = false;
    for (let roundPageIndex = 0; roundPageIndex < maxPagesPerRun; roundPageIndex += 1) {
      const absolutePageIndex = nextPageIndex;
      const absolutePageNumber = absolutePageIndex + 1;
      nextPageIndex += 1;

      if (roundPageIndex > 0) {
        const pageDelaySeconds = pickRandomDelaySeconds(pacing.pageDelayRange);
        if (pageDelaySeconds > 0) {
          await reportSyncProgress(syncRunId, {
            percent: computeProgressPercent(20, 28, roundPageIndex, maxPagesPerRun),
            stage: "list",
            label: "分页等待",
            detail: `等待 ${pageDelaySeconds} 秒后继续请求第 ${absolutePageNumber} 页`,
          });
          await sleep(pageDelaySeconds * 1000);
        }
      }

      await reportSyncProgress(syncRunId, {
        percent: computeProgressPercent(20, 28, roundPageIndex, maxPagesPerRun),
        stage: "list",
        label: "抓取主题列表",
        detail: `第 ${roundIndex} 轮：正在请求第 ${absolutePageNumber} 页（本轮 ${roundPageIndex + 1}/${maxPagesPerRun}）`,
      });

      const suffix = absolutePageIndex === 0 ? "/latest.json?order=created" : `/latest.json?order=created&page=${absolutePageIndex}`;
      const data = await linuxFetchJson(suffix);
      const topics = (((data || {}).topic_list || {}).topics) || [];
      scannedPages += 1;
      if (!topics.length) {
        await flushPendingDocuments(true, `第 ${absolutePageNumber} 页已经没有更多主题`);
        return {
          ok: true,
          storedCount: totalStored,
          selectedCount: totalSelected,
          pushedBatchCount,
          scannedPages,
        };
      }

      roundFoundAnyTopics = true;
      for (const topic of topics) {
        const topicId = Number(topic.id);
        if (!topicId || seen.has(topicId)) {
          continue;
        }
        seen.add(topicId);

        if (lastSeenTopicId != null && topicId <= lastSeenTopicId) {
          await flushPendingDocuments(true, `第 ${roundIndex} 轮已碰到同步边界`);
          return {
            ok: true,
            storedCount: totalStored,
            selectedCount: totalSelected,
            pushedBatchCount,
            scannedPages,
          };
        }

        totalSelected += 1;
        const document = await fetchTopicDocument(topic, syncRunId, {
          topicIndex: totalSelected,
          scannedPages,
          pendingCount: pendingDocuments.length + 1,
          batchIndex: pushedBatchCount + 1,
        });
        pendingDocuments.push(document);

        if (pendingDocuments.length >= normalizedPushBatchSize) {
          await reportSyncProgress(syncRunId, {
            percent: 54,
            stage: "detail",
            label: "达到 AI 批次阈值",
            detail: `已扫描 ${scannedPages} 页，准备提交第 ${pushedBatchCount + 1} 批（${pendingDocuments.length} 个主题内容）`,
          });
          await flushPendingDocuments(false, "达到 AI 批次阈值");
        }

        if (lastSeenTopicId == null && totalSelected >= normalizedBootstrapLimit) {
          await flushPendingDocuments(true, `已达到初始化上限 ${normalizedBootstrapLimit}`);
          return {
            ok: true,
            storedCount: totalStored,
            selectedCount: totalSelected,
            pushedBatchCount,
            scannedPages,
          };
        }
      }

      await reportSyncProgress(syncRunId, {
        percent: computeProgressPercent(22, 28, roundPageIndex + 1, maxPagesPerRun),
        stage: "list",
        label: "分析主题列表",
        detail: `已扫描到第 ${absolutePageNumber} 页，累计发现 ${totalSelected} 个新主题，待提交 ${pendingDocuments.length} 个`,
      });
    }

    if (!roundFoundAnyTopics) {
      await flushPendingDocuments(true, "当前轮次没有新内容");
      return {
        ok: true,
        storedCount: totalStored,
        selectedCount: totalSelected,
        pushedBatchCount,
        scannedPages,
      };
    }

    const roundDelaySeconds = pickRandomDelaySeconds(pacing.roundDelayRange);
    if (roundDelaySeconds > 0) {
      await reportSyncProgress(syncRunId, {
        percent: 50,
        stage: "list",
        label: "轮次间等待",
        detail: `第 ${roundIndex} 轮已完成，等待 ${roundDelaySeconds} 秒后继续下一轮`,
      });
      await sleep(roundDelaySeconds * 1000);
    } else {
      await reportSyncProgress(syncRunId, {
        percent: 50,
        stage: "list",
        label: "继续下一轮",
        detail: `第 ${roundIndex} 轮已完成，继续扫描后续页面`,
      });
    }
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "fetch-linuxdo-state") {
      sendResponse(await fetchSiteState());
      return;
    }

    if (message?.type === "fetch-linuxdo-topics") {
      const siteState = {
        loggedIn: Boolean(message.loggedIn),
        categories: Array.isArray(message.categories) ? message.categories : [],
      };
      const pacing = buildPageRequestPacing(message);
      const result = await collectTopicDocuments(
        message.lastSeenTopicId ?? null,
        Number(message.bootstrapLimit) || 30,
        Number(message.maxPagesPerRun) || 10,
        pacing,
        message.syncRunId || "",
        siteState,
        Number(message.pushBatchSize) || 1
      );
      sendResponse({
        ok: true,
        loggedIn: siteState.loggedIn,
        categories: siteState.categories,
        storedCount: Number(result?.storedCount) || 0,
        selectedCount: Number(result?.selectedCount) || 0,
        pushedBatchCount: Number(result?.pushedBatchCount) || 0,
        scannedPages: Number(result?.scannedPages) || 0,
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
