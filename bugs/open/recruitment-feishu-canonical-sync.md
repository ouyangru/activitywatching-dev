# Recruitment Feishu synchronization — code fixed, production verification pending

- Timestamp: 2026-09-15T13:33:14+08:00
- Symptom: QQ written-test/assessment items appear locally and in the local calendar but can be absent from Feishu progress/review.
- Code evidence: scan_qq_mail inserted recruitment_items without invoking queue_recruitment_feishu_proposal; backfill reclassified only persisted subject/title/extraction_note, ignoring item_type identified from the full body; /proposals capped the entire result, hiding pending items beyond the UI request of 150.
- Change: ingest item and pending proposal in one SQLite transaction; share canonical-item payload generation with historical repair; repair missing proposals on backend reads; limit history only, returning all pending proposals. Preserve terminal progress, local statuses, rejected/applied decisions, user-edited nonempty proposal fields, and Google Calendar paths. Do not enrich cancelled local items.
- Version: branch fix/recruitment-canonical-feishu-sync (commit containing this file).
- Validation: regression coverage for body-only written tests, historical assessments, idempotency, cancelled/done/uncertain items, review decisions, terminal statuses, and more than 150 pending items. Full Python suite: 115 passed, 1 existing deprecation warning; git diff --check passed.
- Online verification: not deployed or verified against production QQ/Feishu/Google accounts. External Feishu writes still require the existing proposal approval. After deployment, refresh progress to repair historical missing proposals, then verify approval writes and local/Google calendar consistency.
