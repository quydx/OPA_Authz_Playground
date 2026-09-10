Acme Retail (org-001) — internal file storage.

This bucket belongs to org-001 only. It is served by a MinIO user
(org001svc) whose IAM policy grants it access to org-001 alone — not
because nobody asked for org-002's data, but because org001svc's own
credentials structurally cannot reach it. See README.md's "Storage
isolation" section for how this differs from every other isolation
guarantee in this POC: it's enforced by MinIO's own IAM, not OPA.
