let latestResult = null;

const statusEl = document.getElementById("status");
const summaryEl = document.getElementById("summary");
const reviewListEl = document.getElementById("review-list");

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

async function activeWorkdayTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https:\/\/[^/]+\.myworkdayjobs\.com\//i.test(tab.url || "")) {
    throw new Error("Open a Workday application page first.");
  }
  return tab;
}

async function send(action) {
  const tab = await activeWorkdayTab();
  const profile = await getWorkdayProfile();
  return chrome.tabs.sendMessage(tab.id, { action, profile });
}

async function runAI() {
  try {
    setStatus("Sending unresolved visible questions to the local AI…");
    const tab = await activeWorkdayTab();
    const profile = await getWorkdayProfile();
    const scan = await chrome.tabs.sendMessage(tab.id, { action: "aiQuestions", profile });
    if (!scan?.questions?.length) throw new Error("No unsupported non-sensitive fields are visible.");

    const response = await fetch("http://127.0.0.1:8766/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions: scan.questions })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Local AI returned ${response.status}`);

    const result = await chrome.tabs.sendMessage(tab.id, { action: "aiFill", profile, answers: payload.answers || [] });
    render(result);
    setStatus(`AI filled ${result.filled?.length || 0} grounded fields. Review everything before continuing.`, "success");
  } catch (error) {
    setStatus(`${error.message || String(error)} Is ai_server.py running?`, "error");
  }
}

async function startAccountSetup() {
  try {
    const tab = await activeWorkdayTab();
    setStatus("Starting profile, résumé, and AI autofill…");
    const result = await chrome.tabs.sendMessage(tab.id, { action: "copilotStart" });
    if (result?.error) throw new Error(result.error);
    setStatus("Autofill started. Follow the on-page Copilot panel.", "success");
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
}

function render(result) {
  latestResult = result;
  const filled = result.filled?.length || 0;
  const ready = result.ready?.length || 0;
  const review = result.review?.length || 0;
  const ignored = result.ignored?.length || 0;

  summaryEl.classList.remove("muted");
  summaryEl.textContent = `${filled} filled · ${ready} safe matches · ${review} need review · ${ignored} intentionally skipped`;
  reviewListEl.replaceChildren();

  for (const item of result.review || []) {
    const li = document.createElement("li");
    const label = document.createElement("strong");
    label.textContent = item.label || "Unlabeled field";
    const reason = document.createElement("span");
    reason.textContent = ` — ${item.reason}`;
    li.append(label, reason);
    reviewListEl.appendChild(li);
  }
}

async function run(action) {
  try {
    setStatus(action === "fill" ? "Filling visible safe fields…" : "Scanning visible fields…");
    const result = await send(action);
    if (!result) throw new Error("The Workday page did not respond. Reload it and try again.");
    render(result);
    setStatus(action === "fill" ? "Filled. Review the page before continuing." : "Scan complete.", "success");
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
}

document.getElementById("scan").addEventListener("click", () => run("scan"));
document.getElementById("fill").addEventListener("click", () => run("fill"));
document.getElementById("ai-fill").addEventListener("click", runAI);
document.getElementById("autofill-all").addEventListener("click", startAccountSetup);
document.getElementById("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("copy-review").addEventListener("click", async () => {
  if (!latestResult) return setStatus("Scan the page first.", "error");
  const lines = (latestResult.review || []).map((item) => `- ${item.label}: ${item.reason}`);
  await navigator.clipboard.writeText(lines.join("\n") || "No unresolved fields on this step.");
  setStatus("Review list copied.", "success");
});

activeWorkdayTab().then(async (tab) => {
  const context = await chrome.tabs.sendMessage(tab.id, { action: "context" });
  document.getElementById("job-context").textContent = context?.title || "Workday application";
  const setup = await chrome.runtime.sendMessage({ action: "setup:status", tabId: tab.id });
  if (setup?.status) setStatus(setup.status, setup.enabled ? "" : "success");
  return run("scan");
}).catch((error) => setStatus(error.message || String(error), "error"));
