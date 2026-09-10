Globex Logistics (org-002) — internal file storage.

This bucket belongs to org-002 only. It is served by a MinIO user
(org002svc) whose IAM policy grants it access to org-002 alone. Try
listing org-001's bucket with org002svc's credentials — MinIO returns
Access Denied, not "empty results": the boundary is enforced by the
storage layer itself, independent of Trino, Airflow, or OPA.
