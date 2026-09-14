# MSV-med

MSV-med is a FastAPI service for working with DICOM studies in Orthanc. It includes asynchronous Celery jobs, PostgreSQL metadata and vector search, and Prometheus request metrics.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [API & Services](#api--services)
- [AI Indexing and Search](#ai-indexing-and-search)
- [DICOM Integration, Privacy, and Security](#dicom-integration-privacy-and-security)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Running the Stack](#running-the-stack)
- [Desktop GUI](#desktop-gui)
- [Service Logs](#service-logs)
- [Publishing](#publishing)
- [Requirements](#requirements)

---

## Features

- JWT login, role-based access control, and legacy bearer-token compatibility.
- Redis-backed request rate limiting.
- DICOM upload validation with file-count and request-size limits.
- Asynchronous DICOM upload, PACS forwarding, retry, and failed-instance workflows.
- Three isolated Celery queues for DICOM uploads, PACS forwarding, and demo jobs.
- Orthanc study, series, and instance browsing through the API and GUI.
- PACS configuration management and administrator connectivity checks.
- DICOM anonymization with identifying-tag removal, private-tag removal, and UID remapping.
- PostgreSQL metadata storage with pgvector embeddings.
- Cosine, Euclidean, PostgreSQL full-text, and hybrid search strategies.
- Idempotent study ingestion into the metadata and vector index.
- Persistent jobs, progress tracking, retries, failed-instance storage, audit logging, and worker inspection.
- Liveness, readiness, dependency health checks, and Prometheus request metrics.
- Dedicated Alembic migration job for repeatable Docker startup.
- Versioned `/api/v1` REST routes with compatibility aliases for existing clients.
- OpenAPI, Swagger, ReDoc, environment templates, Docker development overrides, and GUI container support.

---

## Architecture

```text
GUI or client
  |
  v
FastAPI API -- PostgreSQL/pgvector (metadata and embeddings)
  |
  +---------- Redis (Celery broker and result backend)
  |
  +---------- Orthanc (DICOM storage and PACS REST API)
  |
  +---------- Celery queues:
         dicom   -> upload processing
         forward -> PACS forwarding
         demo    -> UI/demo jobs
```

The normal flow is:

1. A client authenticates and submits a request.
2. The API validates request fields and DICOM headers.
3. Long-running upload and forwarding work is persisted as a job and sent to the appropriate Celery queue.
4. Workers update job progress in PostgreSQL and retain retryable failures in `failed_instances`.
5. Orthanc stores DICOM objects; PostgreSQL stores indexed metadata and optional 384-dimensional embeddings.
6. Query endpoints read PACS metadata or indexed records, depending on the operation.

PostgreSQL data is stored in the `postgres_data` Docker volume. Orthanc data is stored in the `orthanc_data` volume. `docker compose down` preserves both; `docker compose down -v` permanently deletes them. Failed DICOM instances are stored as binary data for retry, so protect the database volume as PHI.

---

## API & Services

The recommended installation is Docker Compose. It starts the complete stack:

| Service | Address |
|---|---|
| FastAPI API | `http://localhost:8000` |
| Versioned API routes | `http://localhost:8000/api/v1` |
| Swagger documentation | `http://localhost:8000/docs` |
| ReDoc documentation | `http://localhost:8000/redoc` |
| Orthanc web UI/API | `http://localhost:8042` |
| Orthanc DICOM port | `4242` |
| PostgreSQL with pgvector | host port `5433` |
| Redis | host port `6379` |

Domain API routes are available under the stable `/api/v1` prefix, for example `/api/v1/auth/login` and `/api/v1/studies`; the original unversioned paths remain available for backward compatibility.

[OVERVIEW OF ENDPOINTS](REST.md)

---

## AI Indexing and Search

After studies are present in Orthanc, index one study or all studies through Swagger or curl:

```bash
curl -X POST "http://localhost:8000/api/v1/query/ingestions" \
  -H "Authorization: Bearer $TOKEN"

curl "http://localhost:8000/api/v1/query/search?q=chest%20CT&strategy=cosine" \
  -H "Authorization: Bearer $TOKEN"
```

The first indexing request downloads or initializes the `all-MiniLM-L6-v2` embedding model. Search strategies include `cosine`, `euclidean`, `fulltext`, and `hybrid`.

Uploads require valid DICOM files with SOP Instance, Study Instance, and Series Instance UIDs. When anonymization is enabled, identifying tags and private tags are removed and UIDs are remapped consistently across the job. The current anonymization is intended for controlled development and staging workflows; validate it against your organization's approved DICOM confidentiality profile before using it with clinical data in production.

---

## DICOM Integration, Privacy, and Security

- Only valid DICOM files with SOP Instance, Study Instance, and Series Instance UIDs are accepted by the API upload path.
- Uploads are limited to 100 files and 50 MiB per request.
- Forwarding and upload options support PACS credentials, anonymization, an optional examination result, and an optional webhook URL.
- Anonymization removes configured identifying tags, private tags, and remaps common UIDs consistently within a job.
- The anonymizer is a custom implementation. Not checked against legal policy. Use at your own risk.
- Audit records are stored in PostgreSQL and include request ID, actor, action, resource, outcome, and JSON details.
- Production mode requires strong API/JWT secrets and an administrator password of at least 12 characters. Network access to Orthanc, Redis, and PostgreSQL should be private.

---

## Getting Started

```bash
export TOKEN=$(curl -sS -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')

curl -i http://localhost:8000/ \
  -H "Authorization: Bearer $TOKEN"

curl -s http://localhost:8000/health \
  -H "Authorization: Bearer $TOKEN"

curl -s http://localhost:8000/metrics
```

The health response should contain `"api":"ok"` and `"pacs_reachable":true`. The metrics endpoint is intended for an internal Prometheus scrape and should not be exposed directly to the public internet.

---

## Configuration

The Docker Compose file includes development defaults, so a fresh clone can start without creating an environment file:

```bash
docker compose up --build -d
```

For a local test run, the built-in defaults use `admin` / `admin123` and `changeme` for the legacy API secret. Change these values before exposing the service to another machine or network. You can either export variables in your shell or create an untracked `.env` file to override them. Do not commit `.env`.

The important settings are:

```env
API_SECRET=replace_this_with_a_long_random_value
JWT_SECRET=replace_this_with_a_different_long_random_value
JWT_EXPIRE_MINUTES=60
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_this_demo_password
ORTHANC_USER=orthanc
ORTHANC_PASS=orthanc
DATABASE_URL=postgresql://msvmed:msvmed@postgres:5432/msvmed
REDIS_URL=redis://redis:6379/0
ORTHANC_URL=http://orthanc:8042
```

Configuration reference:

| Variable | Purpose | Local default |
|---|---|---|
| `APP_ENV` | Runtime mode; production enables secret validation | `development` in Compose |
| `API_SECRET` | Legacy bearer-token compatibility secret | `changeme` |
| `JWT_SECRET` | JWT signing secret | `changeme` |
| `JWT_EXPIRE_MINUTES` | JWT lifetime | `60` |
| `ADMIN_USERNAME` | Bootstrap administrator username | `admin` |
| `ADMIN_PASSWORD` | Bootstrap administrator password | `admin123` |
| `DATABASE_URL` | SQLAlchemy PostgreSQL connection URL | Docker service URL |
| `REDIS_URL` | Celery broker/result backend URL | `redis://redis:6379/0` |
| `ORTHANC_URL` | PACS HTTP API URL | `http://orthanc:8042` in Docker |
| `ORTHANC_USER` / `ORTHANC_PASS` | PACS credentials | `orthanc` / `orthanc` |
| `API_URL` | GUI API URL | `http://localhost:8000` |
| `API_TOKEN` | Optional GUI bearer token; takes precedence over login | unset |
| `API_USERNAME` / `API_PASSWORD` | GUI automatic login credentials | `admin` / `admin123` |
| `UI_FONT` | Optional GUI font path | platform-dependent |

`DATABASE_URL`, `REDIS_URL`, and `ORTHANC_URL` must use Docker service names inside containers and `localhost` when the process runs directly on the host.

Generate a secret on Linux with:

```bash
openssl rand -hex 32
```

On first API startup, an administrator is created automatically if `ADMIN_USERNAME` does not already exist. Existing users and passwords are never overwritten.

The Compose file supplies the internal Docker addresses for `DATABASE_URL`, `REDIS_URL`, and `ORTHANC_URL`. Do not replace those with `localhost` while running the API and worker in Docker: inside a container, `localhost` means that same container.

---

## Running the Stack

From the project directory:

```bash
docker compose up --build -d
docker compose up --wait
```

Watch startup logs:

```bash
docker compose logs -f migrate api worker worker-forward worker-demo postgres orthanc redis
```

The first build downloads Python packages and the embedding model, so it can take several minutes. Check container status:

```bash
docker compose ps
```

The migration job runs once and must complete successfully before the API and workers start. The stack then runs PostgreSQL, Redis, Orthanc, the API, and three queue-specific Celery workers:

- `worker`: DICOM upload processing
- `worker-forward`: PACS forwarding
- `worker-demo`: UI/demo jobs used for testing concurrency and job representation in the database during development.

PostgreSQL is initialized with pgvector from `docker/initdb/01-enable-pgvector.sql`. The migration job applies files in `alembic/versions/` and exits after success; an exited migration container is expected.

To stop the stack while preserving data:

```bash
docker compose down
```

To stop it and delete the PostgreSQL and Orthanc data volumes:

```bash
docker compose down -v
```

The second command permanently deletes imported studies and indexed metadata.

---

## Desktop GUI

The easiest Fedora/Linux option is to run the GUI on the host and the backend in Docker. Install the GUI dependencies with uv:

```bash
uv sync --extra gui
API_URL=http://localhost:8000 \
API_USERNAME=admin \
API_PASSWORD=admin123 \
uv run python gui.py
```

The GUI logs in automatically with `API_USERNAME` and `API_PASSWORD` when `API_TOKEN` is not set. For a non-default installation, use the credentials you configured in `.env`.

If Fedora reports missing graphical libraries, install them with:

```bash
sudo dnf install -y mesa-libGL libX11 libXext libXrender libXcursor libXinerama libXi libXrandr libXxf86vm glfw
```

The GUI connects to the API, so the API and worker containers must already be running. The Docker GUI profile is also available on an X11 Linux desktop:

```bash
xhost +local:docker
docker compose -f docker-compose.yml -f docker-compose.gui.yml --profile gui up --build
```

If using Wayland, running `gui.py` directly on the host is usually simpler than forwarding X11 from a container. The only requirement is the dearpygui library and a font. If the latter is missing the GUI will default to the system's font.

For development with live source reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The development override mounts `src/` into the API container and enables Uvicorn reload. *It is not intended for production.*

---

## Service Logs

For troubleshooting see:

```bash
docker compose logs api
docker compose logs worker
docker compose logs worker-forward
docker compose logs worker-demo
docker compose logs migrate
docker compose logs postgres
```

---

## Publishing

Before publishing, confirm that:

- `git status --short` contains no `.env`, credential, key, or generated data files.
- Production deployments set `APP_ENV=production`, long unique `API_SECRET` and `JWT_SECRET` values, and an `ADMIN_PASSWORD` of at least 12 characters.
- Default Docker credentials are changed before exposing API, Orthanc, PostgreSQL, or Redis outside the local machine.
- `docker compose ps` shows the API, PostgreSQL, Redis, Orthanc, and all three workers as running and healthy where health checks are configured. The migration service should show `Exited (0)`.

The Docker defaults are for local development only. They are deliberately convenient for a fresh clone and are not suitable for an internet-facing deployment.

---

## Requirements

### Recommended: Docker

Install Docker Engine and the Docker Compose plugin. On Fedora (the distro that the program was developed on), you can use Docker's official repository:

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and back in once after adding yourself to the `docker` group. Verify the installation:

```bash
docker run --rm hello-world
docker compose version
```

On another Linux distribution, install Docker Engine and Docker Compose from the Docker documentation, then run the same verification commands.

### Optional: local Python installation

The manual setup requires:

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) for locked dependency installation
- PostgreSQL 16 with the `vector` extension
- Redis 7 or newer
- An Orthanc server

Docker is easier because it supplies PostgreSQL, pgvector, Redis, and Orthanc with the correct networking and credentials.

[make it work w/o docker](NODOCKER.md)
