# Moonwing

Moonwing is a Clearwing-backed control plane for multi-user vulnerability scanning and source hunting.

## Architecture

- **FastAPI control API** — run submission, credential management, runtime profiles, findings
- **Worker service** — polls for queued runs, stages dependencies (targets, credentials, artifacts), executes Clearwing, normalizes findings
- **Postgres** — source of truth for users, credentials, runtime profiles, targets, runs, findings, artifacts
- **Redis** — queue backend and broker
- **MinIO** — local S3-compatible artifact storage with provenance tracking

## Service Map

| Service | Port | Description |
|---------|------|-------------|
| `moonwing-api` | 8000 | FastAPI control plane |
| `moonwing-worker` | — | Async worker for Clearwing jobs |
| `postgres` | 5432 | Primary transactional datastore |
| `redis` | 6379 | Queue backend |
| `minio` | 9000/9001 | S3-compatible artifact storage |

## Local Development

```bash
# Start dependencies
docker compose up -d

# Start app containers
docker compose --profile app up -d

# Run tests
python -m pytest -p no:cacheprovider -v
```

## Deployment (SecOps VM — 192.168.1.215)

```bash
ansible-playbook -i inventory/hosts.yml site.yml --limit secops --tags docker
```

### Deployment Verification

- [ ] `curl http://192.168.1.215:8000/docs` — API docs load
- [ ] Postgres, Redis, MinIO, API, and worker containers are healthy
- [ ] Worker log shows `worker entering poll loop`
- [ ] Submit a test run via API and verify it reaches `completed`

## Run State Machine

```
queued → staging → running → normalizing → completed
  ↓        ↓         ↓           ↓
failed   failed    failed      failed
  ↑        ↑         ↑
canceled canceled  canceled
                  needs_review
```

## Current Status

Tasks 1–10 complete. Core API, worker with staging, normalization, and integration tests are functional. Ansible deployment assets are ready for secops.
