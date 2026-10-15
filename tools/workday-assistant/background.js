"use strict";

const SETUP_KEY = "workdayAccountSetup";
const MAX_AGE_MS = 10 * 60 * 1000;

function isWorkdayOrigin(origin) {
  try {
    return /^https:\/\/[^/]+\.myworkdayjobs\.com$/i.test(new URL(origin).origin);
  } catch {
    return false;
  }
}

async function currentRun() {
  const stored = await chrome.storage.session.get(SETUP_KEY);
  const run = stored[SETUP_KEY];
  if (!run) return null;
  if (Date.now() - run.createdAt > MAX_AGE_MS) {
    await chrome.storage.session.remove(SETUP_KEY);
    return null;
  }
  return run;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.action === "setup:start") {
      const tabId = Number.isInteger(message.tabId) ? message.tabId : sender.tab?.id;
      if (!Number.isInteger(tabId) || !isWorkdayOrigin(message.origin)) throw new Error("Open a Workday page first.");
      if (!message.email || !message.password) throw new Error("Enter the account email and one-run password.");
      await chrome.storage.session.set({
        [SETUP_KEY]: {
          enabled: true,
          tabId,
          origin: new URL(message.origin).origin,
          email: message.email,
          password: message.password,
          profile: message.profile || {},
          resumePath: message.resumePath || "",
          stage: "starting",
          status: "Looking for Apply…",
          createdAt: Date.now()
        }
      });
      return { ok: true };
    }

    if (message?.action === "resume:upload") {
      const run = await currentRun();
      if (!run || sender.tab?.id !== run.tabId || !run.resumePath) throw new Error("The backend résumé path is unavailable.");
      const target = { tabId: run.tabId };
      await chrome.debugger.attach(target, "1.3");
      try {
        const { root } = await chrome.debugger.sendCommand(target, "DOM.getDocument", { depth: -1, pierce: true });
        const { nodeId } = await chrome.debugger.sendCommand(target, "DOM.querySelector", {
          nodeId: root.nodeId,
          selector: 'input[data-workday-assistant-resume-target="true"]'
        });
        if (!nodeId) throw new Error("The Workday résumé input disappeared before upload.");
        await chrome.debugger.sendCommand(target, "DOM.setFileInputFiles", { nodeId, files: [run.resumePath] });
      } finally {
        await chrome.debugger.detach(target).catch(() => {});
      }
      return { ok: true };
    }

    if (message?.action === "ai:answer") {
      if (!sender.tab?.url || !/^https:\/\/[^/]+\.myworkdayjobs\.com\//i.test(sender.tab.url)) throw new Error("AI requests are limited to Workday tabs.");
      const response = await fetch("http://127.0.0.1:8766/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ questions: message.questions || [] })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Local AI returned ${response.status}`);
      return payload;
    }

    if (message?.action === "setup:get") {
      const run = await currentRun();
      if (!run || sender.tab?.id !== run.tabId || sender.url && new URL(sender.url).origin !== run.origin) return null;
      return run;
    }

    if (message?.action === "setup:update") {
      const run = await currentRun();
      if (!run || sender.tab?.id !== run.tabId) return null;
      const updated = { ...run, ...(message.patch || {}) };
      await chrome.storage.session.set({ [SETUP_KEY]: updated });
      return updated;
    }

    if (message?.action === "setup:status") {
      const run = await currentRun();
      if (!run || run.tabId !== message.tabId) return null;
      return { enabled: run.enabled, stage: run.stage, status: run.status };
    }

    if (message?.action === "setup:clear") {
      await chrome.storage.session.remove(SETUP_KEY);
      return { ok: true };
    }

    return null;
  })().then(sendResponse).catch((error) => sendResponse({ error: error.message || String(error) }));
  return true;
});
