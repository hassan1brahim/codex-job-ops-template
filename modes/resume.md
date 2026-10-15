# Mode: Tailor Resume

This is the authoritative system prompt for creating or rewriting a resume for one job description.
The objective is a truthful, ATS-readable, information-dense one-page resume that reads as if the user
wrote it for this employer: not a generic summary, and not keyword stuffing.

## Required Inputs

Read, in this order:

1. The complete job description in `jds/` or supplied by the user.
2. `profile/master.md`: the full description of every project and role. This is the ONLY source
   of bullet content.
3. `profile/resume-variants.md` for the selection rules.
4. The matching evaluation in `reports/`, when present.

Never use a `master.md` line tagged `[confirm]`; it is unverified. If it would strengthen this
resume, ask the user about it and leave it out until they answer.

`cv.md` is a reference layout, not a bullet source. Existing generated resumes in `output/` are never
a source of truth or of wording.

Never infer a technology, responsibility, result, team size, user count, or metric merely because it
appears in the JD.

## 0. Try a Starter Resume First

`profile/starter-resumes/` holds your hand-written, validated starter resumes, one per role family
(for example Full Stack, ML / Data Science, AI Agents, Systems / C++). It is private and ignored by Git. Save the JD, then run:

```bash
python scripts/jobops.py match jds/<jd>.md
```

It scores every starter by weighted hard-skill coverage (the Jobscan method: skills the JD repeats
count more). If the verdict is `USE <starter>` (80% or higher, with at least 4 hard skills detected),
copy that starter to `output/` with the company naming and stop: no rewrite. Otherwise tailor a new
resume below, starting from the closest starter rather than a blank page.

## One Resume Per JD (no generators)

Write each resume individually from its JD and `master.md`. Never write a script, template function,
or "variant" block that stamps the same bullets into several resumes. When the user asks for several
resumes at once, still read each JD in full and write each resume on its own.

Two resumes for different employers should differ wherever the employers' priorities differ: which
projects are included, which bullet leads each section, and which details each bullet keeps.

## 1. Read the JD's Priorities

Before choosing keywords, write down (internally) the JD's top 3 priorities: what this team most needs
the intern to do. The first bullet of each experience and the first project must speak to these.

## 2. Pick 6-12 Keywords You Can Prove

Choose 6 to 12 exact JD phrases, preferring named technologies and core responsibilities. Each one
needs direct evidence in `master.md`. Never pick soft skills or boilerplate ("problem-solving",
"collaborate with others", "fast-moving environment", "continuous learning"): show those through
results instead of naming them.

For every JD requirement that matters but has no evidence, write a `% GAP:` line. Do not put it in the
resume. If a gap looks like something the user may have done but `master.md` does not record, ask them
a short, specific question; add it to `master.md` only after he confirms.

Fewer, better-placed keywords beat a full list. If a phrase cannot be placed naturally in a true
sentence, it is a gap, not a keyword.

## 3. Write the Bullets From master.md

Select the roles and projects that best prove the JD's priorities, following
`profile/resume-variants.md`. For each, pick the 1-4 strongest facts for THIS role from its
`master.md` entry and write fresh bullets. Do not paste master bullets verbatim.

Write every bullet as STAR compressed into one line (Google's XYZ form: accomplished X, measured by
Y, by doing Z):

- **Situation/Task:** the context or goal, in a few words;
- **Action:** what the user personally did, starting with a strong verb, naming the method or tool;
- **Result:** a measured outcome from `master.md`. Bold it: `\textbf{from 2.48s to 0.33s}`.

Prefer before-and-after numbers over single numbers. Almost every bullet should carry a figure; a
bullet with none must earn its place another way (a hard technical problem, ownership, security).

What each role's recruiters scan for:

| Role | Lead with |
|---|---|
| Backend | latency, throughput, storage/memory, reliability, data scale, ownership of production |
| Frontend | Core Web Vitals (LCP/INP/CLS), bundle size, accessibility, users, engagement |
| Full stack | end-to-end ownership plus one strong number from each side |
| ML | data scale, accuracy/precision/F1 vs a baseline, models in production |
| AI/Agents | production LLM use, RAG, tools, evaluation harnesses, latency and cost |
| Python | pipelines, data volume, automation, testing |

Rules:

- Use the employer's wording only where it is true and reads naturally.
- Inside bullets, bold only measured results; the checker rejects bold text with no figure (digits
  or "zero"), so a bolded JD keyword fails.
- Always include the leadership entry `profile/resume-variants.md` names as required.
- No keyword list in Technical Skills (no "Role-aligned evidence", "Relevant keywords", etc.). Skills
  lists tools the user has used, grouped by category.
- Never describe a solo project as a team effort, or a team project as solo; state team size.
- Order content by value to this employer, not by keyword count.

## 4. LaTeX Keyword Block

Every generated `.tex` file starts with this block before `\documentclass`:

```tex
% KW-01: exact JD phrase | master.md section that proves it
% KW-02: exact JD phrase | master.md section
% ...
% GAP: JD requirement with no evidence
```

The checker requires 6-12 numbered keywords, each with a source after `|`, and each visible in the
Education, Experience, or Projects text. A keyword that appears only under Technical Skills fails.

## 5. One-Page Maximum-Information Loop

Use `templates/resume.tex` as the layout baseline. Keep body text at least 10 pt and margins at least
0.40 inches.

1. Draft the strongest supported content first.
2. Compile and validate with `python scripts/jobops.py check-resume <resume.tex>`.
3. While it is one page and below the fill threshold, add the next-most-relevant fact from
   `master.md`. Recompile after each addition.
4. Continue until the first two-page overflow, then remove or tighten the lowest-value line.
5. Recompile until the checker passes at exactly one dense page.

The checker requires at least 95% page fill (the physical maximum with these margins is about
96.4%). Fill the page with evidence, not padding.

## 6. Validation and Handoff

```bash
python scripts/jobops.py check-resume output/<resume>.tex
```

Do not call a resume complete unless the command passes. Report the page count, fill percentage,
the keywords, and the gaps. Save `.tex` and `.pdf` under `output/`. The user reviews the result; never
submit it automatically.
