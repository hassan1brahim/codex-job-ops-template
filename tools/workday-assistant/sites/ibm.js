// IBM careers (careers.ibm.com). The generic engine does the work; IBM only needs the button.
// Quirks handled in engine.js: custom "Is current position?" select (options appear on open),
// "End date" appearing after current=No, and repeated education/work rows.
(() => {
  "use strict";
  if (!globalThis.JobOpsEngine || !/JobApplication/.test(location.pathname)) return;
  globalThis.JobOpsEngine.panel("Job Ops fill", () => globalThis.JobOpsEngine.run({ rows: true }));
})();
