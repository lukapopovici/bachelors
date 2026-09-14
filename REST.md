
# API reference

All domain routes below are available with the `/api/v1` prefix and with the
legacy unversioned prefix. For example, `/api/v1/studies` and `/studies` point
to the same handler. Swagger at `/docs` is the authoritative interactive
reference, including request schemas and query parameters.

Resource endpoints declare Pydantic response models in OpenAPI, so generated
clients can rely on stable response shapes. Raw Orthanc study and instance
payloads remain dynamic because their fields depend on the source DICOM object.

REST conventions used by the API:

- Collections use plural nouns such as `/studies`, `/jobs`, `/records`, and `/pacs`.
- `GET` reads resources, `POST` creates resources or asynchronous job resources, and `DELETE` removes resources.
- Filters and pagination are query parameters; resource identifiers are path parameters.
- Asynchronous operations return `202 Accepted` with a `job_id` or retry resource identifier.
- Invalid input returns `422`, missing resources return `404`, unauthorized requests return `401` or `403`, and invalid state transitions return `409`.
- Login is intentionally an action endpoint because it creates a token rather than a persistent resource.
- Existing singular/action paths remain available as hidden compatibility aliases; new integrations should use the canonical paths below.

Authentication:

| Method | Endpoint | Description |
| --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Exchange username/password for a JWT |
| `POST` | `/api/v1/auth/users` | Create a user; admin role required |

Studies and instances:

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/studies` | Paginated PACS studies with modality/date filters |
| `GET` | `/api/v1/studies/{study_id}` | Retrieve a study from Orthanc |
| `GET` | `/api/v1/studies/{study_id}/instances` | List study instances |
| `POST` | `/api/v1/studies` | Validate and queue DICOM upload; returns `202` and `job_id` |
| `POST` | `/api/v1/studies/{study_id}/forwards` | Create a forwarding job to another PACS |
| `GET` | `/api/v1/instances/{instance_id}` | Retrieve selected DICOM metadata |

Jobs:

| Method | Endpoint | Description |
| --- | --- | --- |
| `POST` | `/api/v1/jobs/demo` | Queue a fixed 30-second demo job |
| `GET` | `/api/v1/jobs` | Paginated jobs, optionally filtered by status |
| `GET` | `/api/v1/jobs/{job_id}` | Retrieve job progress and errors |

Query and indexing:

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/query/strategies` | List available search strategies |
| `GET` | `/api/v1/query/studies` | List PACS studies and ingestion status |
| `POST` | `/api/v1/query/ingestions` | Create a bulk ingestion job for all studies |
| `POST` | `/api/v1/query/ingestions/{study_id}` | Create an idempotent ingestion job for one study |
| `GET` | `/api/v1/query/search` | Semantic, Euclidean, full-text, or hybrid search |
| `GET` | `/api/v1/query/records` | Paginated indexed records |
| `GET` | `/api/v1/query/records/{record_id}` | Retrieve one indexed record |
| `DELETE` | `/api/v1/query/records/{record_id}` | Delete one indexed record |

Administration, admin role required:

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/admin/pacs` | List configured target PACS entries without passwords |
| `POST` | `/api/v1/admin/pacs` | Add a PACS configuration |
| `GET` | `/api/v1/admin/pacs/{pacs_id}` | Retrieve one PACS configuration without its password |
| `DELETE` | `/api/v1/admin/pacs/{pacs_id}` | Delete a PACS configuration |
| `GET` | `/api/v1/admin/pacs/{pacs_id}/connectivity` | Test PACS connectivity |
| `DELETE` | `/api/v1/admin/indexed-records` | Delete application-indexed study records |
| `GET` | `/api/v1/admin/failed-instances` | List failed DICOM instances |
| `POST` | `/api/v1/admin/failed-instances/{id}/retries` | Create a retry for a failed instance |
| `DELETE` | `/api/v1/admin/failed-instances/{id}` | Delete one failed instance |
| `DELETE` | `/api/v1/admin/failed-instances` | Delete failed instances |
| `POST` | `/api/v1/admin/jobs/{job_id}/retries` | Create a retry for a failed job |
| `DELETE` | `/api/v1/admin/jobs/{job_id}` | Delete one job |
| `DELETE` | `/api/v1/admin/jobs` | Delete jobs, optionally by status |
| `GET` | `/api/v1/admin/audit` | Paginated audit records |
| `GET` | `/api/v1/admin/system-stats` | Aggregate application statistics |
| `GET` | `/api/v1/admin/workers` | Celery worker inspection |

Compatibility aliases retained for existing clients include:
`/studies/{study_id}/forward`, `/query/ingest/all`, `/query/ingest/{study_id}`,
`/admin/database`, `/admin/stats`, and the singular `/retry` paths. They are
accepted but hidden from the primary OpenAPI schema.

Operational endpoints are intentionally outside the versioned domain API:

| Method | Endpoint | Authentication | Description |
| --- | --- | --- | --- |
| `GET` | `/` | Bearer token | API identity and version |
| `GET` | `/health/live` | None | Process liveness check |
| `GET` | `/health/ready` | None | PostgreSQL and Redis readiness check |
| `GET` | `/health` | Bearer token | Full API, PACS, database, Redis, and worker health |
| `GET` | `/metrics` | Network-level protection | Prometheus exposition format |