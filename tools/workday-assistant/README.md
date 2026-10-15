# Job Ops Workday Assistant

A local Chrome extension that fills the visible step of a Workday application from your reviewed
profile. It deliberately does **not** click Next, Review, Submit, accept legal terms, answer demographic
questions, or mark an application as submitted.

## Install

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select this folder:

   `<repo>/tools/workday-assistant`

5. Open the extension's **Details -> Extension options** and fill the remaining blank profile fields.

## Use

The flow follows the useful part of Jobright's model: set up the profile once, open a supported ATS,
click one primary Autofill action, then inspect a transparent review checkpoint before continuing.

1. Start the localhost AI bridge: `python tools/workday-assistant/ai_server.py`.
2. Open an application on a `*.myworkdayjobs.com` site.
3. Click the floating **Job Ops** pill or open the extension popup.
4. Select **Autofill application**.
5. The Copilot fills verified profile data first, uploads the selected résumé, and asks AI only about
   remaining non-sensitive fields.
6. Review the on-page summary and every unresolved item, then advance manually.
7. On the final review page, inspect the complete application and submit it yourself.

Existing answers are preserved rather than overwritten. Workday tenants can customize questions and
markup, so an unfamiliar or unsupported field remains in the review checkpoint instead of being guessed.

### One-time account setup

From a Workday job page, choose **Autofill application**. The requested default Workday password is
stored in the local extension profile. The extension may click only the exact controls **Apply**, **Autofill with Resume**,
**Create Account**, and **Sign In**. The password is kept in Chrome's in-memory session storage for
at most ten minutes and is erased after the sign-in action; the requested default remains in the
local extension source.

The setup flow uses Chrome's debugger permission only long enough to place the configured local PDF
into Workday's résumé input, then immediately detaches. The default is the canonical backend starter
résumé at `profile/starter-resumes/SWE_Full_Stack.pdf`. It also fills the approved My Information fields,
including LinkedIn as the recruiting source, No for prior employer history, and Main/Home for phone
type. The extension still never clicks Next, Review, or Submit on an application.

### AI-assisted fields

Run `python tools/workday-assistant/ai_server.py`; the main Autofill action invokes it automatically
after deterministic profile filling. The localhost-only bridge uses the authenticated Codex CLI installed on this Mac,
grounds answers in `config/profile.yml`, `profile/master.md`, and `cv.md`, and returns schema-validated
answers. The extension refuses AI answers for sensitive/legal/demographic fields and does not advance
or submit the application. No API key is stored in the extension.

## Filled automatically

- Name, email, phone, address, links, and current education when saved in settings.
- U.S. work authorization and sponsorship answers from the canonical Job Ops profile.
- Native Workday inputs and common custom listboxes.

## Always review manually

- Cover-letter uploads and any résumé other than the configured backend starter.
- Demographic, disability, veteran, legal, compensation, criminal-history, age, signature, and consent fields.
- Graduation dates, availability, relocation, and any other blank profile fact.
- Employer-specific questions and free-response answers.

## Safety boundary

There is no code in this extension that searches for or clicks a submit/next button. A successful fill
means only that approved values were written into visible form controls. Job Ops remains review-first,
and `APPLIED` must be recorded only after you have external submission evidence.

## Script-first engine (IBM first, more sites next)

`engine.js` is a generic filler; `sites/<ats>.js` only registers a site. Answers come from one file,
`profile/application-defaults.json` (copy it from `profile/application-defaults.example.json`); after editing it run `python3 tools/workday-assistant/build_standing_answers.py`
(a test fails if `standing-answers.js` is stale).

On `careers.ibm.com/.../JobApplication` click **Job Ops fill**: it fills education and work rows (adds rows
as needed), every question with a standing answer, and the voluntary EEO/DOB answers you
approved in that file. It never overwrites an existing value, never clicks Continue or Submit, and never ticks the
"I certify" attestation. The panel lists **failed** fields (Claude fixes those), **needs you** fields
(no standing answer or attestation) and file uploads. The same report is written to
`document.documentElement.dataset.jobOpsReport`.
