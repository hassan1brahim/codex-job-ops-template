// Ashby / Greenhouse / Lever application pages: the generic engine plus one button.
(() => {
  "use strict";
  if (!globalThis.JobOpsEngine) return;
  const isForm = document.querySelector('input[type="file"], input[type="email"], form');
  if (!isForm) return;
  globalThis.JobOpsEngine.panel("Job Ops fill", () => globalThis.JobOpsEngine.run({ rows: false }));
})();
