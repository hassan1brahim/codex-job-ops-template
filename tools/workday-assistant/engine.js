// Generic, script-first form filler. Site configs (sites/*.js) only add selectors and quirks.
// Boundary: fills visible controls from standing answers, never overwrites, never clicks Continue/Next/Submit.
(() => {
  "use strict";
  if (globalThis.JobOpsEngine) return;

  const A = () => globalThis.JOB_OPS_STANDING_ANSWERS;
  // MessageChannel yields are not throttled in background tabs (setTimeout is clamped to >= 1 s there).
  const channel = new MessageChannel();
  const yieldNow = () => new Promise((resolve) => { channel.port1.onmessage = () => resolve(); channel.port2.postMessage(0); });
  const sleep = async (ms) => { const end = performance.now() + ms; do { await yieldNow(); } while (performance.now() < end); };
  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").replace(/\*+/g, "").trim();
  const lower = (value) => norm(value).toLowerCase();

  function visible(el) {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }

  // ---- labels ------------------------------------------------------------------------------
  function ownLabel(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).map((id) => document.getElementById(id)?.textContent).join(" ");
    return norm(
      el.getAttribute("aria-label") || ids || (el.labels ? [...el.labels].map((l) => l.textContent).join(" ") : "") || el.getAttribute("placeholder") || ""
    );
  }

  // Question text for radios/checkboxes lives on the group, not on the option.
  function questionOf(el) {
    if (el.type === "radio" || el.type === "checkbox") {
      const group = el.closest('fieldset, [role="radiogroup"], [role="group"]');
      const legend = group?.querySelector("legend")?.textContent || group?.getAttribute("aria-label");
      if (legend) return norm(legend);
      let node = (group || el).parentElement;
      for (let i = 0; i < 6 && node; i += 1, node = node.parentElement) {
        const prev = node.previousElementSibling;
        const text = norm(prev?.innerText || "");
        if (text.length > 8 && !prev.querySelector("input,select,textarea")) return text;
      }
    }
    return ownLabel(el) || norm(el.closest("div")?.querySelector("label")?.textContent) || norm(el.name || el.id);
  }

  function contextOf(el) {
    let node = el.parentElement;
    for (let i = 0; i < 5 && node; i += 1, node = node.parentElement) {
      const text = norm(node.innerText || "");
      if (text.length > 80) return text.slice(0, 500).toLowerCase();
    }
    return "";
  }

  // ---- answers -----------------------------------------------------------------------------
  function blocked(question) {
    return (A().never_fill || []).some((src) => new RegExp(src, "i").test(question));
  }

  function standingFor(question, el) {
    if (blocked(question)) return { blocked: true };
    for (const entry of A().qa) {
      if (!new RegExp(entry.q, "i").test(question)) continue;
      if (entry.ctx && !new RegExp(entry.ctx, "i").test(contextOf(el))) continue;
      return entry;
    }
    return null;
  }

  // ---- setters -----------------------------------------------------------------------------
  function setText(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const type of ["input", "change", "blur"]) el.dispatchEvent(new Event(type, { bubbles: true }));
    return norm(el.value) === norm(value);
  }

  function formatFor(el, iso) {
    if (!iso) return "";
    if (el.type === "month") return iso.slice(0, 7);
    if (el.type === "date") return iso.slice(0, 10);
    const [y, m, d] = iso.slice(0, 10).split("-");
    return `${m}/${d}/${y}`;
  }

  function matchOption(options, wanted, prefix) {
    const w = lower(wanted);
    return (
      options.find((o) => lower(o.textContent) === w) ||
      options.find((o) => prefix && lower(o.textContent).startsWith(w)) ||
      options.find((o) => lower(o.textContent).startsWith(w)) ||
      (w.length > 2 ? options.find((o) => lower(o.textContent).includes(w)) : null)
    );
  }

  async function popupPick(trigger, wanted, prefix) {
    const before = new Set(document.querySelectorAll("*"));
    (trigger.closest('[role="combobox"], [class*="select"], [class*="Select"], [class*="dropdown"]') || trigger).dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    trigger.click();
    await sleep(250);
    const pool = [...document.querySelectorAll('[role="option"], [role="listbox"] *, li, [data-value]')].filter(visible);
    const fresh = pool.filter((n) => !before.has(n) || n.matches('[role="option"]'));
    const hit = matchOption(fresh.length ? fresh : pool, wanted, prefix);
    if (!hit) {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return false;
    }
    hit.click();
    await sleep(120);
    return true;
  }

  // react-select style: <input role="combobox">; type to filter, then click the matching option.
  async function setCombobox(el, wanted, prefix) {
    el.focus();
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    await sleep(60);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, wanted);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (let i = 0; i < 10; i += 1) {
      await sleep(150);
      const options = [...document.querySelectorAll('[role="option"]')].filter(visible);
      const hit = matchOption(options, wanted, prefix);
      if (hit) {
        hit.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        hit.click();
        await sleep(100);
        return true;
      }
    }
    el.blur();
    return false;
  }

  async function setSelect(el, wanted, prefix) {
    const opts = [...el.options].filter((o) => norm(o.textContent) && o.value !== "");
    const hit = matchOption(opts, wanted, prefix);
    if (hit) {
      el.value = hit.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return el.selectedOptions[0] === hit;
    }
    return popupPick(el.parentElement && visible(el) ? el : el.nextElementSibling || el.parentElement, wanted, prefix);
  }

  function setRadio(el, wanted, prefix) {
    const group = el.name ? [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)] : [el];
    const labelOf = (r) => ({ textContent: r.labels?.[0]?.textContent || r.closest("label")?.textContent || r.value });
    const hit = matchOption(group.map((r) => Object.assign(r, { _l: labelOf(r) })).map((r) => ({ textContent: r._l.textContent, node: r })), wanted, prefix);
    if (!hit) return false;
    (hit.node.labels?.[0] || hit.node).click();
    return hit.node.checked;
  }

  // ---- field loop ----------------------------------------------------------------------------
  const CONTROL = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="search"]), select, textarea';

  function hasValue(el) {
    if (el.getAttribute("role") === "combobox") {
      const shell = el.closest('[class*="select"]');
      return Boolean(norm(shell?.querySelector('[class*="single-value"]')?.textContent));
    }
    if (el.type === "radio") {
      const group = el.name ? document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`) : [el];
      return [...group].some((r) => r.checked);
    }
    if (el.type === "checkbox") return el.checked;
    if (el instanceof HTMLSelectElement) return Boolean(norm(el.selectedOptions[0]?.textContent)) && el.selectedIndex > 0;
    return Boolean(norm(el.value));
  }

  async function applyStanding(el, question, entry, report) {
    const prefix = Boolean(entry.prefix);
    let ok;
    if (el.type === "checkbox") {
      if (entry.a === "check" && !el.checked) el.click();
      ok = el.checked;
    } else if (el.type === "radio") ok = setRadio(el, entry.a, prefix);
    else if (el instanceof HTMLSelectElement) ok = await setSelect(el, entry.a, prefix);
    else if (el.disabled || el.readOnly) ok = false;
    else if (el.getAttribute("role") === "combobox") ok = await setCombobox(el, entry.a, prefix);
    else ok = setText(el, entry.a);
    await sleep(50);
    ok = ok || hasValue(el); // verify against the page, not the setter's return value
    (ok ? report.filled : report.failed).push({ q: question, a: entry.a, protected: Boolean(entry.protected) });
  }

  async function fillStanding(report, seenGroups) {
    for (const el of [...document.querySelectorAll(CONTROL)].filter((n) => visible(n) || n instanceof HTMLSelectElement)) {
      if (el.closest("[data-job-ops-row-done]")) continue;
      if (el.type === "radio") {
        if (el.name && seenGroups.has(el.name)) continue;
        if (el.name) seenGroups.add(el.name);
      }
      const question = questionOf(el);
      if (!question || el.id?.includes("sample")) continue;
      if (hasValue(el)) continue;
      const entry = standingFor(question, el);
      if (entry?.blocked) { report.needsYou.push({ q: question, why: "attestation / never auto-filled" }); continue; }
      if (!entry) { report.needsYou.push({ q: question, why: "no standing answer" }); continue; }
      await applyStanding(el, question, entry, report);
    }
  }

  // ---- repeated rows (education / work) ---------------------------------------------------
  const ROW_FIELDS = {
    education: [[/^degree name$/i, "degreeName"], [/^type of degree$/i, "type"], [/^university$/i, "university"], [/^start date$/i, "start"], [/^end date$/i, "end"], [/^completed\??$/i, "completed"]],
    work: [[/^company$/i, "company"], [/^position title$/i, "title"], [/^start date$/i, "start"], [/^is current position\??$/i, "current"], [/^end date$/i, "end"]]
  };

  function rowBlocks() {
    // A row = the smallest ancestor holding a Remove button and its fields.
    const blocks = [];
    for (const btn of document.querySelectorAll("button, a, [role=button]")) {
      if (lower(btn.textContent) !== "remove" || !visible(btn)) continue;
      let node = btn.parentElement;
      while (node && node.querySelectorAll(CONTROL).length < 2) node = node.parentElement;
      if (node && !blocks.includes(node)) blocks.push(node);
    }
    return blocks;
  }

  function rowKind(block) {
    const labels = [...block.querySelectorAll(CONTROL)].map(ownLabel);
    if (labels.some((l) => /^degree name$/i.test(l))) return "education";
    if (labels.some((l) => /^company$/i.test(l))) return "work";
    return null;
  }

  async function fillRow(block, kind, data, report) {
    for (const el of block.querySelectorAll(CONTROL)) {
      const label = ownLabel(el);
      const map = ROW_FIELDS[kind].find(([re]) => re.test(label));
      if (!map || data[map[1]] === undefined) continue;
      if (el instanceof HTMLInputElement && hasValue(el)) continue;
      const raw = data[map[1]];
      const value = el.type === "month" || el.type === "date" ? formatFor(el, raw) : raw;
      let ok;
      if (el instanceof HTMLSelectElement) ok = hasValue(el) || (await setSelect(el, value, false));
      else ok = setText(el, value);
      (ok ? report.filled : report.failed).push({ q: `${kind}: ${label}`, a: value });
      if (label.toLowerCase() === "is current position?") await sleep(150); // reveals "End date"
    }
  }

  async function fillRows(kind, report) {
    const wanted = A()[kind];
    const addLabel = /^add another/i;
    for (let pass = 0; pass < wanted.length + 1; pass += 1) {
      let blocks = rowBlocks().filter((b) => rowKind(b) === kind);
      if (blocks.length >= wanted.length) break;
      const add = [...document.querySelectorAll("button, a, [role=button]")].filter((b) => visible(b) && addLabel.test(norm(b.getAttribute("aria-label") || b.textContent)));
      const btn = kind === "work" ? add[add.length - 1] : add[0];
      if (!btn) break;
      btn.click();
      await sleep(250);
    }
    const blocks = rowBlocks().filter((b) => rowKind(b) === kind);
    for (let i = 0; i < wanted.length; i += 1) {
      if (!blocks[i]) { report.failed.push({ q: `${kind} row ${i + 1}`, a: "no row to fill" }); continue; }
      await fillRow(blocks[i], kind, wanted[i], report);
    }
    blocks.forEach((b) => b.setAttribute("data-job-ops-row-done", "1"));
  }

  // ---- public API ----------------------------------------------------------------------------
  async function run({ rows = true } = {}) {
    const report = { filled: [], failed: [], needsYou: [], files: [] };
    const seenGroups = new Set();
    if (rows) {
      await fillRows("education", report);
      await fillRows("work", report);
    }
    await fillStanding(report, seenGroups);
    for (const file of document.querySelectorAll('input[type="file"]')) {
      if (!file.files.length) report.files.push({ q: questionOf(file) || "file upload", why: "upload via From Device (Claude can do this)" });
    }
    document.querySelectorAll("[data-job-ops-row-done]").forEach((n) => n.removeAttribute("data-job-ops-row-done"));
    document.documentElement.dataset.jobOpsReport = JSON.stringify({ at: new Date().toISOString(), url: location.href, ...report });
    return report;
  }

  function panel(label, onRun) {
    if (document.getElementById("job-ops-engine-host")) return;
    const host = document.createElement("div");
    host.id = "job-ops-engine-host";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>:host{all:initial}.b{position:fixed;right:20px;bottom:22px;z-index:2147483647;border:0;border-radius:999px;background:#5b2ca0;color:#fff;padding:11px 16px;font:700 14px -apple-system,sans-serif;box-shadow:0 8px 28px rgba(31,22,61,.28);cursor:pointer}.p{position:fixed;right:20px;bottom:74px;z-index:2147483647;width:340px;max-height:60vh;overflow:auto;background:#fff;color:#172033;border:1px solid #ded9ed;border-radius:14px;padding:14px;font:13px -apple-system,sans-serif;box-shadow:0 18px 48px rgba(31,22,61,.25)}.h{display:none}li{margin:4px 0}</style><button class="b">✦ ${label}</button><div class="p h"></div>`;
    document.documentElement.appendChild(host);
    const btn = root.querySelector(".b");
    const out = root.querySelector(".p");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      out.classList.remove("h");
      out.textContent = "Filling…";
      try {
        const r = await onRun();
        const list = (items, f) => `<ul>${items.map((i) => `<li>${f(i)}</li>`).join("")}</ul>`;
        const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
        out.innerHTML = `<b>${r.filled.length} filled · ${r.needsYou.length} need you · ${r.failed.length} failed</b>` +
          (r.failed.length ? `<p>Failed (Claude can fix):</p>${list(r.failed, (i) => esc(i.q))}` : "") +
          (r.needsYou.length ? `<p>Needs you:</p>${list(r.needsYou, (i) => esc(`${i.q}`))}` : "") +
          (r.files.length ? `<p>Files:</p>${list(r.files, (i) => esc(i.q))}` : "") +
          `<p style="color:#126334;font-size:11px">Never clicks Continue or Submit.</p>`;
      } catch (error) {
        out.textContent = `Stopped: ${error.message || error}`;
      } finally {
        btn.disabled = false;
      }
    });
  }

  globalThis.JobOpsEngine = { run, panel, questionOf, standingFor };
})();
