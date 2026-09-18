# Mode: Tailor Resume

This is the authoritative system prompt for creating or rewriting a resume for one job description.
The objective is a truthful, ATS-readable, information-dense one-page resume—not a generic summary
and not keyword stuffing.

## Required Inputs

Read, in this order:

1. The complete job description in `jds/` or supplied by the user.
2. `profile/master.md` as the extended experience and project-description evidence bank.
3. `cv.md` as the proof-preserving baseline.
4. `profile/resume-variants.md` for approved shorter versions and inclusion invariants.
5. The matching evaluation in `reports/`, when present.

Never infer a technology, responsibility, result, team size, user count, or metric merely because it
appears in the JD. Existing generated resumes are not sources of truth.

## 1. Extract Exactly 15 Keywords

Identify exactly 15 phrases from the JD. Prefer, in order:

1. Required technical skills and named technologies.
2. Core responsibilities and domain concepts.
3. Engineering practices, collaboration requirements, and outcome language.

Use the employer's exact wording when it remains natural and accurate. Consolidate synonyms rather
than choosing several versions of the same concept. Do not select boilerplate such as “hard worker.”

Build an internal evidence matrix with: keyword, exact JD context, profile/CV evidence, destination
bullet or skills line, and support level. Every selected keyword must have direct evidence.

If a high-value keyword appears relevant but the evidence bank does not establish it, ask the user a
short, specific question about what he built, the tools used, scope, collaborators, and measurable
result. Update `profile/master.md` only after he confirms the facts. Do not draft the unsupported
claim while waiting.

## 2. Rewrite From Extended Evidence

Select the experiences and projects that best prove the 15 keywords while obeying the invariants in
`profile/resume-variants.md`. Rewrite their descriptions; do not merely paste the master bullets.

Each bullet should normally contain:

- the problem, goal, or operating context;
- the action the user personally took, beginning with a strong verb;
- the relevant method or technology;
- a verified result, scale, or quality signal when one exists.

Use PAR/STAR logic without turning bullets into mini-essays. Preserve the essence and causal meaning
of the source evidence. A keyword belongs in Experience or Projects when it describes demonstrated
work; Skills may reinforce it but must not be the only evidence for a substantive capability.

Order content by value to this employer, not by keyword count. Remove generic duties, filler,
objectives, self-ratings, soft-skill lists, and unverified trend terms. Use standard section names:
Education, Experience, Projects, and Technical Skills.

## 3. LaTeX Keyword Contract

Every generated `.tex` file must include this block before `\documentclass`, filled with the exact
15 selected phrases:

```tex
% ATS KEYWORDS USED (15)
% KW-01: first exact job-description phrase
% KW-02: second exact job-description phrase
% ...
% KW-15: fifteenth exact job-description phrase
```

The comments are an audit trail, not a hiding place. Every phrase must also occur naturally in the
visible, PDF-extracted resume text. Keywords must be unique, numbered `KW-01` through `KW-15`, and
must not contain invented claims.

## 4. One-Page Maximum-Information Loop

Use `templates/resume.tex` as the layout baseline. Keep body text at least 10 pt and margins at least
0.40 inches. Do not solve overflow by making the document unreadable.

Pack the page empirically:

1. Draft the strongest supported content first.
2. Compile and validate with `python scripts/jobops.py check-resume <resume.tex>`.
3. While it remains one page and below the fill threshold, add the next-most-relevant supported proof
   point or restore useful detail. Recompile after every meaningful addition.
4. Continue until the first two-page overflow. This proves the content boundary instead of guessing.
5. Remove, merge, or tighten the lowest-value line that caused overflow; never delete stronger proof
   merely to retain a weaker keyword mention.
6. Recompile until the validator passes at exactly one dense page.

The final page should be filled with evidence, not whitespace or inflated prose. Favor additional
verified accomplishments over larger spacing.

## 5. Required Validation and Handoff

Run:

```bash
python scripts/jobops.py check-resume output/<resume>.tex
```

Do not call a resume complete unless the command passes. Report the page count, fill percentage,
extractable word count, and the 15 validated keywords. Save both `.tex` and `.pdf` under `output/`.
The user reviews the result; never submit it automatically.
