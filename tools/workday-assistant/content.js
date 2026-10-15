(() => {
  "use strict";

  const SENSITIVE = /\b(gender|sex|race|ethnic|hispanic|veteran|disabil|pronoun|religion|marital|date of birth|birth date|social security|ssn|national id|salary|compensation|criminal|conviction|background check|drug test|age|signature|certif(?:y|ication)|terms|privacy consent)\b/i;
  const FILE_FIELD = /\b(resume|résumé|cv|cover letter|attachment|upload)\b/i;
  const AUTHORIZED = /(?:legally|currently).*authori[sz]ed.*(?:work|employment).*(?:united states|u\.?s\.?)|authori[sz]ed.*(?:united states|u\.?s\.?).*(?:work|employment)/i;
  const SPONSORSHIP = /(?:require|need).*(?:now|future|employment)?.*(?:sponsor|sponsorship)|(?:sponsor|sponsorship).*(?:require|need)/i;
  const PREVIOUS_EMPLOYER = /\b(?:previously|ever).*(?:worked|employed).*(?:for|by)\b/i;

  const TEXT_MAPPINGS = [
    [/\b(first|given) name\b/i, "firstName"],
    [/\b(last|family) name\b|\bsurname\b/i, "lastName"],
    [/\bemail(?: address)?\b/i, "email"],
    [/\b(country|international) (phone|calling) code\b/i, "phoneCountryCode"],
    [/\b(phone|mobile)(?: number)?\b/i, "phone"],
    [/\baddress (?:line )?1\b|\bstreet address\b/i, "address1"],
    [/\baddress (?:line )?2\b|\bapartment|suite\b/i, "address2"],
    [/\b(city|town)\b/i, "city"],
    [/\b(state|province|region)\b/i, "state"],
    [/\b(zip|postal)(?: code)?\b/i, "postalCode"],
    [/\bcountry\b/i, "country"],
    [/\blinked\s*in\b/i, "linkedin"],
    [/\bgithub\b/i, "github"],
    [/\b(personal )?(website|portfolio)\b/i, "website"],
    [/\bhow did you hear about us\b/i, "hearAboutUs"],
    [/\b(phone type|device type)\b/i, "phoneType"],
    [/\b(school|university|institution)\b/i, "education.school"],
    [/\bdegree\b/i, "education.degree"],
    [/\b(field|area) of study\b|\bmajor\b/i, "education.field"],
    [/\bstart month\b/i, "education.startMonth"],
    [/\bstart year\b/i, "education.startYear"],
    [/\b(?:graduation|completion|end) month\b/i, "education.graduationMonth"],
    [/\b(?:graduation|completion|end) year\b/i, "education.graduationYear"]
  ];

  function normalize(value) {
    return String(value || "").replace(/\s+/g, " ").replace(/\*+/g, "").trim();
  }

  function isVisible(element) {
    if (!(element instanceof HTMLElement)) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }

  function textFromIds(ids) {
    return normalize(String(ids || "").split(/\s+/).map((id) => document.getElementById(id)?.textContent).filter(Boolean).join(" "));
  }

  function fieldLabel(element) {
    if (element.type === "radio" || element.type === "checkbox") {
      const group = element.closest('fieldset, [role="radiogroup"], [role="group"], [data-automation-id="formField"], [data-automation-id*="question"]');
      if (group) {
        const question = group.querySelector('legend, [data-automation-id="formLabel"], [data-automation-id="richText"]');
        if (question?.textContent) return normalize(question.textContent);
      }
    }

    const direct = [
      element.getAttribute("aria-label"),
      textFromIds(element.getAttribute("aria-labelledby")),
      element.labels ? [...element.labels].map((label) => label.textContent).join(" ") : "",
      element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent : "",
      element.getAttribute("placeholder")
    ].map(normalize).find(Boolean);
    if (direct) return direct;

    const container = element.closest('[data-automation-id="formField"], fieldset, [role="group"], [data-automation-id*="question"]');
    if (container) {
      const label = container.querySelector('legend, [data-automation-id="formLabel"], label, [data-automation-id="richText"]');
      if (label?.textContent) return normalize(label.textContent);
    }
    return normalize(element.name || element.id || "Unlabeled field");
  }

  function optionLabel(element) {
    const label = element.labels ? [...element.labels].map((item) => item.textContent).join(" ") : "";
    if (label) return normalize(label);
    return normalize(element.closest("label")?.textContent || element.value);
  }

  function hasExistingValue(element) {
    if (element.type === "radio" || element.type === "checkbox") {
      const name = element.name;
      const group = name ? [...document.querySelectorAll(`input[name="${CSS.escape(name)}"]`)] : [element];
      return group.some((candidate) => candidate.checked);
    }
    if (element instanceof HTMLSelectElement) return Boolean(normalize(element.selectedOptions?.[0]?.textContent || element.value));
    if (element.matches('button[aria-haspopup="listbox"], [role="combobox"]')) {
      return Boolean(normalize(element.textContent || element.getAttribute("aria-valuetext")));
    }
    return Boolean(normalize(element.value));
  }

  function getPath(object, path) {
    return path.split(".").reduce((value, key) => value?.[key], object);
  }

  function decision(label, element, profile) {
    if (FILE_FIELD.test(label) || element.type === "file") {
      return { kind: "review", reason: profile.resumePath ? `Upload manually: ${profile.resumePath}` : "Select the tailored résumé manually" };
    }
    if (SENSITIVE.test(label)) return { kind: "ignored", reason: "Sensitive, legal, or demographic answer requires the user" };
    if (AUTHORIZED.test(label)) {
      return profile.workAuthorizedUS ? { kind: "answer", value: profile.workAuthorizedUS, source: "workAuthorizedUS" } : { kind: "review", reason: "Work authorization is blank" };
    }
    if (SPONSORSHIP.test(label)) {
      return profile.sponsorshipRequired ? { kind: "answer", value: profile.sponsorshipRequired, source: "sponsorshipRequired" } : { kind: "review", reason: "Sponsorship answer is blank" };
    }
    if (PREVIOUS_EMPLOYER.test(label)) {
      return profile.previouslyWorkedForEmployer
        ? { kind: "answer", value: profile.previouslyWorkedForEmployer, source: "previouslyWorkedForEmployer" }
        : { kind: "review", reason: "Prior employment answer is blank" };
    }

    for (const [pattern, path] of TEXT_MAPPINGS) {
      if (!pattern.test(label)) continue;
      const value = getPath(profile, path);
      return value ? { kind: "answer", value, source: path } : { kind: "review", reason: `${path} is blank in extension settings` };
    }
    return { kind: "review", reason: "No approved profile mapping" };
  }

  function setNativeValue(element, value) {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(element, value);
    else element.value = value;
    for (const event of ["input", "change", "blur"]) element.dispatchEvent(new Event(event, { bubbles: true }));
  }

  function selectNative(element, value) {
    const wanted = normalize(value).toLowerCase();
    const option = [...element.options].find((candidate) => {
      const text = normalize(candidate.textContent).toLowerCase();
      const optionValue = normalize(candidate.value).toLowerCase();
      return text === wanted || optionValue === wanted || text.includes(wanted) || wanted.includes(text);
    });
    if (!option) return false;
    element.value = option.value;
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function chooseRadio(element, value) {
    const name = element.name;
    const candidates = name ? [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)] : [element];
    const wanted = normalize(value).toLowerCase();
    const match = candidates.find((candidate) => optionLabel(candidate).toLowerCase() === wanted || normalize(candidate.value).toLowerCase() === wanted);
    if (!match) return false;
    match.click();
    match.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function discoverFields() {
    const selector = [
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"])',
      "textarea",
      "select",
      'button[aria-haspopup="listbox"]',
      '[role="combobox"]'
    ].join(",");
    return [...document.querySelectorAll(selector)].filter(isVisible);
  }

  async function chooseCustom(element, value) {
    element.click();
    await new Promise((resolve) => setTimeout(resolve, 180));
    const wanted = normalize(value).toLowerCase();
    const options = [...document.querySelectorAll('[role="option"], [data-automation-id="promptOption"]')].filter(isVisible);
    const exact = options.find((option) => normalize(option.textContent).toLowerCase() === wanted);
    const match = exact || options.find((option) => {
      const text = normalize(option.textContent).toLowerCase();
      return text.includes(wanted) || wanted.includes(text) || (/\(\+1\)/.test(wanted) && /united states.*\+1/i.test(text));
    });
    if (!match) {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return false;
    }
    match.click();
    return true;
  }

  async function inspect(profile, shouldFill) {
    const result = { filled: [], ready: [], review: [], ignored: [] };
    const seenRadioGroups = new Set();

    for (const element of discoverFields()) {
      if (element.type === "radio" && element.name && seenRadioGroups.has(element.name)) continue;
      if (element.type === "radio" && element.name) seenRadioGroups.add(element.name);

      const label = fieldLabel(element);
      const choice = decision(label, element, profile);
      if (choice.kind === "review" || choice.kind === "ignored") {
        result[choice.kind].push({ label, reason: choice.reason });
        continue;
      }

      const record = { label, value: choice.value, source: choice.source };
      if (!shouldFill) {
        result.ready.push(record);
        continue;
      }
      if (hasExistingValue(element)) continue;

      let success = false;
      if (element instanceof HTMLSelectElement) success = selectNative(element, choice.value);
      else if (element.type === "radio") success = chooseRadio(element, choice.value);
      else if (element.matches('button[aria-haspopup="listbox"], [role="combobox"]')) success = await chooseCustom(element, choice.value);
      else if (!element.disabled && !element.readOnly) {
        setNativeValue(element, choice.value);
        success = true;
      }

      if (success) result.filled.push(record);
      else result.review.push({ label, reason: `Could not set approved value “${choice.value}”` });
    }
    return result;
  }

  function choicesFor(element) {
    if (element instanceof HTMLSelectElement) return [...element.options].map((option) => normalize(option.textContent)).filter(Boolean);
    if (element.type === "radio") {
      const name = element.name;
      const radios = name ? [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)] : [element];
      return radios.map(optionLabel).filter(Boolean);
    }
    return [];
  }

  function collectAIQuestions(profile) {
    const questions = [];
    const seen = new Set();
    for (const element of discoverFields()) {
      const label = fieldLabel(element);
      const key = normalize(label).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      if (hasExistingValue(element)) continue;
      const choice = decision(label, element, profile);
      if (choice.kind !== "review" || FILE_FIELD.test(label) || SENSITIVE.test(label)) continue;
      questions.push({ label, field_type: element.type || element.getAttribute("role") || element.tagName.toLowerCase(), options: choicesFor(element) });
    }
    return questions;
  }

  async function fillAIAnswers(profile, answers) {
    const result = { filled: [], ready: [], review: [], ignored: [] };
    const byLabel = new Map((answers || []).map((answer) => [normalize(answer.label).toLowerCase(), answer]));
    const seen = new Set();

    for (const element of discoverFields()) {
      const label = fieldLabel(element);
      const key = normalize(label).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      if (hasExistingValue(element)) continue;
      if (FILE_FIELD.test(label) || SENSITIVE.test(label)) continue;
      const known = decision(label, element, profile);
      if (known.kind !== "review") continue;
      const answer = byLabel.get(key);
      if (!answer || answer.needs_review || !normalize(answer.answer)) {
        result.review.push({ label, reason: answer?.reason || "AI found no grounded answer" });
        continue;
      }

      let success = false;
      if (element instanceof HTMLSelectElement) success = selectNative(element, answer.answer);
      else if (element.type === "radio") success = chooseRadio(element, answer.answer);
      else if (element.matches('button[aria-haspopup="listbox"], [role="combobox"]')) success = await chooseCustom(element, answer.answer);
      else if (!element.disabled && !element.readOnly) {
        setNativeValue(element, answer.answer);
        success = true;
      }

      if (success) result.filled.push({ label, value: answer.answer, source: `AI (${answer.confidence})` });
      else result.review.push({ label, reason: `AI suggested “${answer.answer}” but the control could not be set` });
    }
    return result;
  }

  function mergeResults(...results) {
    return results.reduce((combined, result) => {
      for (const key of ["filled", "ready", "review", "ignored"]) combined[key].push(...(result?.[key] || []));
      return combined;
    }, { filled: [], ready: [], review: [], ignored: [] });
  }

  function currentContext() {
    const heading = [...document.querySelectorAll("h1, h2")].find(isVisible);
    return {
      title: normalize(document.title) || normalize(heading?.textContent) || "Workday application",
      step: normalize(heading?.textContent) || "Application"
    };
  }

  let copilotUI = null;

  function ensureCopilotPanel() {
    if (copilotUI) return copilotUI;
    const host = document.createElement("div");
    host.id = "job-ops-copilot-host";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .toggle { position: fixed; right: 20px; bottom: 22px; z-index: 2147483647; border: 0; border-radius: 999px; background: #5b2ca0; color: white; padding: 11px 16px; font: 700 14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; box-shadow: 0 8px 28px rgba(31,22,61,.28); cursor: pointer; }
        .panel { position: fixed; right: 20px; bottom: 74px; z-index: 2147483647; width: 330px; border: 1px solid #ded9ed; border-radius: 14px; background: #fff; color: #172033; padding: 15px; box-shadow: 0 18px 48px rgba(31,22,61,.25); font: 13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
        .hidden { display: none; }
        .eyebrow { color: #6542d7; font-size: 10px; font-weight: 800; letter-spacing: .12em; }
        h2 { font-size: 17px; margin: 4px 0; }
        .context,.status { color: #667085; line-height: 1.35; }
        .primary { width: 100%; margin-top: 12px; border: 0; border-radius: 8px; background: #5b2ca0; color: white; padding: 11px; font-weight: 750; cursor: pointer; }
        .primary:disabled { cursor: wait; opacity: .65; }
        .summary { margin-top: 11px; border-radius: 8px; background: #f4f1fc; padding: 9px; font-weight: 650; }
        ul { max-height: 150px; overflow: auto; padding-left: 18px; margin-bottom: 0; }
        li { margin: 5px 0; }
        .safe { margin-top: 9px; color: #126334; font-size: 11px; }
      </style>
      <button class="toggle" type="button">✦ Job Ops</button>
      <section class="panel hidden">
        <div class="eyebrow">APPLICATION COPILOT</div>
        <h2>One-click autofill</h2>
        <div class="context"></div>
        <button class="primary" type="button">Autofill application</button>
        <div class="status">Profile and SWE Backend résumé ready.</div>
        <div class="summary hidden"></div>
        <ul></ul>
        <div class="safe">Stops for review. Never clicks Next or Submit.</div>
      </section>`;
    document.documentElement.appendChild(host);

    const panel = root.querySelector(".panel");
    const toggle = root.querySelector(".toggle");
    const button = root.querySelector(".primary");
    const status = root.querySelector(".status");
    const summary = root.querySelector(".summary");
    const list = root.querySelector("ul");
    root.querySelector(".context").textContent = currentContext().title;
    toggle.addEventListener("click", () => panel.classList.toggle("hidden"));
    button.addEventListener("click", async () => {
      button.disabled = true;
      status.textContent = "Reading this step and filling verified fields…";
      panel.classList.remove("hidden");
      try {
        await startCopilotFlow();
      } catch (error) {
        status.textContent = `Stopped: ${error.message || String(error)}`;
      } finally {
        button.disabled = false;
      }
    });
    copilotUI = { panel, status, summary, list, button };
    return copilotUI;
  }

  function renderCopilotResult(result) {
    const ui = ensureCopilotPanel();
    const filled = result.filled?.length || 0;
    const review = result.review?.length || 0;
    const ignored = result.ignored?.length || 0;
    ui.panel.classList.remove("hidden");
    ui.status.textContent = "Autofill complete. Check every entry before continuing.";
    ui.summary.classList.remove("hidden");
    ui.summary.textContent = `${filled} filled · ${review} need review · ${ignored} protected`;
    ui.list.replaceChildren();
    for (const item of (result.review || []).slice(0, 12)) {
      const li = document.createElement("li");
      li.textContent = `${item.label}: ${item.reason}`;
      ui.list.appendChild(li);
    }
  }

  async function smartFill(profile) {
    const verified = await inspect(profile, true);
    const questions = collectAIQuestions(profile);
    let ai = { filled: [], ready: [], review: [], ignored: [] };
    if (questions.length) {
      const payload = await chrome.runtime.sendMessage({ action: "ai:answer", questions });
      if (payload?.error) throw new Error(payload.error);
      ai = await fillAIAnswers(profile, payload.answers || []);
    }
    const result = mergeResults(verified, ai);
    renderCopilotResult(result);
    return result;
  }

  async function startCopilotFlow() {
    const profile = await getWorkdayProfile();
    const setupScreen = exactControl("Apply") || exactControl("Autofill with Resume") || pageHasHeading("Create Account") || pageHasHeading("Sign In") || pageHasHeading("My Information");
    if (!setupScreen) return smartFill(profile);

    const setup = await chrome.runtime.sendMessage({
      action: "setup:start",
      origin: location.origin,
      email: profile.email,
      password: profile.accountPassword,
      profile,
      resumePath: profile.resumePath
    });
    if (setup?.error) throw new Error(setup.error);
    ensureCopilotPanel().status.textContent = "Starting the Workday flow…";
    await startSetupAutomation();
    return { filled: [], ready: [], review: [], ignored: [] };
  }

  function exactControl(text) {
    const wanted = normalize(text).toLowerCase();
    return [...document.querySelectorAll('button, a, [role="button"]')]
      .filter(isVisible)
      .find((element) => normalize(element.textContent || element.getAttribute("aria-label")).toLowerCase() === wanted);
  }

  function credentialField(pattern) {
    return [...document.querySelectorAll('input:not([type="hidden"])')]
      .filter(isVisible)
      .find((element) => pattern.test(fieldLabel(element)));
  }

  function pageHasHeading(text) {
    const wanted = normalize(text).toLowerCase();
    return [...document.querySelectorAll("h1, h2, h3")].some((heading) => isVisible(heading) && normalize(heading.textContent).toLowerCase() === wanted);
  }

  async function updateSetup(patch) {
    return chrome.runtime.sendMessage({ action: "setup:update", patch });
  }

  let setupBusy = false;
  let setupObserver = null;

  async function runSetupStep() {
    if (setupBusy) return;
    setupBusy = true;
    try {
      const run = await chrome.runtime.sendMessage({ action: "setup:get" });
      if (!run?.enabled || !run.email) return;

      if (pageHasHeading("My Information") && !run.myInfoFilled) {
        ensureCopilotPanel().status.textContent = "Filling verified fields, then asking AI about unresolved questions…";
        const result = await smartFill(run.profile || {});
        await updateSetup({
          myInfoFilled: true,
          stage: "my-information-filled",
          status: `My Information filled: ${result.filled.length} fields; ${result.review.length + result.ignored.length} left for review.`
        });
      }

      if (!run.resumeUploaded && run.resumePath) {
        const candidates = [...document.querySelectorAll('input[type="file"]')];
        const resumeInput = candidates.find((input) => FILE_FIELD.test(fieldLabel(input))) || (candidates.length === 1 ? candidates[0] : null);
        if (resumeInput) {
          resumeInput.setAttribute("data-workday-assistant-resume-target", "true");
          const uploaded = await chrome.runtime.sendMessage({ action: "resume:upload" });
          resumeInput.removeAttribute("data-workday-assistant-resume-target");
          if (uploaded?.error) throw new Error(uploaded.error);
          resumeInput.dispatchEvent(new Event("input", { bubbles: true }));
          resumeInput.dispatchEvent(new Event("change", { bubbles: true }));
          await updateSetup({
            resumeUploaded: true,
            stage: "resume-uploaded",
            status: "Backend résumé uploaded. Review My Information before continuing."
          });
          return;
        }
      }

      if (pageHasHeading("Create Account")) {
        if (run.accountSubmitted || !run.password) return;
        const email = credentialField(/^email address$/i);
        const password = credentialField(/^password$/i);
        const verify = credentialField(/^verify (?:new )?password$/i);
        const create = exactControl("Create Account");
        if (email && password && verify && create) {
          setNativeValue(email, run.email);
          setNativeValue(password, run.password);
          setNativeValue(verify, run.password);
          await updateSetup({ accountSubmitted: true, stage: "create-account-submitted", status: "Creating the Workday tenant account…" });
          create.click();
          return;
        }
      }

      if (pageHasHeading("Sign In")) {
        if (run.signInSubmitted || !run.password) return;
        const email = credentialField(/^email address$/i);
        const password = credentialField(/^password$/i);
        const signIn = exactControl("Sign In");
        if (email && password && signIn) {
          setNativeValue(email, run.email);
          setNativeValue(password, run.password);
          await updateSetup({
            signInSubmitted: true,
            stage: "sign-in-submitted",
            status: "Sign-in sent. Waiting for the résumé and My Information screens…"
          });
          signIn.click();
          return;
        }
      }

      const resume = exactControl("Autofill with Resume");
      if (resume && !run.resumeOptionClicked) {
        await updateSetup({ resumeOptionClicked: true, stage: "resume-option-clicked", status: "Apply with Resume selected; waiting for account setup…" });
        resume.click();
        return;
      }

      const apply = exactControl("Apply");
      if (apply && !run.applyClicked) {
        await updateSetup({ applyClicked: true, stage: "apply-clicked", status: "Apply opened; waiting for the résumé option…" });
        apply.click();
        return;
      }

      await updateSetup({ status: "Waiting for the next recognized Workday account screen…" });
    } catch (error) {
      await updateSetup({ enabled: false, password: "", stage: "error", status: `Stopped: ${error.message || String(error)}` });
      setupObserver?.disconnect();
    } finally {
      setupBusy = false;
    }
  }

  async function startSetupAutomation() {
    const run = await chrome.runtime.sendMessage({ action: "setup:get" });
    if (!run?.enabled) return;
    if (!setupObserver) {
      let timer = null;
      setupObserver = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(runSetupStep, 350);
      });
      setupObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
    await runSetupStep();
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.action === "setup") {
      startSetupAutomation().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ error: error.message || String(error) }));
      return true;
    }
    if (message?.action === "aiQuestions") {
      sendResponse({ questions: collectAIQuestions(message.profile || {}) });
      return false;
    }
    if (message?.action === "aiFill") {
      fillAIAnswers(message.profile || {}, message.answers || [])
        .then(sendResponse)
        .catch((error) => sendResponse({ filled: [], ready: [], ignored: [], review: [{ label: "AI error", reason: error.message || String(error) }] }));
      return true;
    }
    if (message?.action === "context") {
      sendResponse(currentContext());
      return false;
    }
    if (message?.action === "smartFill") {
      smartFill(message.profile || {})
        .then(sendResponse)
        .catch((error) => sendResponse({ filled: [], ready: [], ignored: [], review: [{ label: "Copilot error", reason: error.message || String(error) }] }));
      return true;
    }
    if (message?.action === "copilotStart") {
      startCopilotFlow()
        .then(sendResponse)
        .catch((error) => sendResponse({ error: error.message || String(error) }));
      return true;
    }
    if (!message || !["scan", "fill"].includes(message.action)) return false;
    inspect(message.profile || {}, message.action === "fill")
      .then(sendResponse)
      .catch((error) => sendResponse({ filled: [], ready: [], ignored: [], review: [{ label: "Extension error", reason: error.message || String(error) }] }));
    return true;
  });

  ensureCopilotPanel();
  startSetupAutomation().catch(() => {});
})();
