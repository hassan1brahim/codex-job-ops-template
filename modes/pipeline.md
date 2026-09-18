# Mode: Pipeline

Use this to process `data/pipeline.md`.

## Steps

1. Read every unchecked `- [ ]` item.
2. For each URL, run:

   ```bash
   python scripts/jobops.py fetch "<url>"
   ```

3. If the fetch succeeds, evaluate the saved JD using `modes/evaluate.md`.
4. If the fetch fails, keep the item unchecked and add a short note.
5. For each completed item, mark it checked in `data/pipeline.md`.
6. Update `data/applications.md`.
7. When the pipeline line contains `board:<key>`, attach outputs with `jobboard artifacts <key>` and
   move the role to `READY_TO_APPLY` only after the resume validator passes.

## Stop Conditions

Stop before:

- external submission
- sending outreach
- applying through a portal
- answering legal/demographic fields without explicit profile data
