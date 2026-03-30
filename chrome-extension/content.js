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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "fetch-linuxdo-state") {
      sendResponse(await fetchSiteState());
      return;
    }

    if (message?.type === "fetch-linuxdo-topics") {
      const siteState = await fetchSiteState();
      const topics = await collectTopicDocuments(
        message.lastSeenTopicId ?? null,
        Number(message.bootstrapLimit) || 30,
        Number(message.maxPagesPerRun) || 10,
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
