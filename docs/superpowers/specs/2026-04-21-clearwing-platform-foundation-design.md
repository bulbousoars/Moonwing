# Clearwing Platform Foundation Design

**Date:** 2026-04-21
**Status:** Implemented — see README for what shipped beyond the original v1 scope.
**Production host:** `secops` / `192.168.1.215` (the design originally referenced
`192.168.1.250`; that inventory entry was stale and the VM moved before
deployment).

> Historical note: this document captures the *original* platform foundation
> intent. Several capabilities now in production (OIDC sign-in, LDAP directory
> sync, IAM/PAM with audit, email notifications, endpoint sensors, management
> UI) were intentionally outside the v1 non-goals listed below and were added
> in subsequent iterations. The README and `docs/sensors/rollout.md` describe
> the current shipped feature set.

## Goal

Replace the current GVM/OpenVAS-centric `secops` workflow with a multi-user Clearwing-based platform foundation on `192.168.1.215` that supports both live network scans and source-code hunts from day one, persists normalized results in a queryable source of truth, and leaves clean boundaries for a future UI, modular runtimes, and pluggable scanners.

## Context

The existing `secops` deployment path is an Ansible-managed Docker stack under `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack`. That role still provisions `openvas`, and the associated inventory is stale: `C:\Users\danie\Documents\Ansible\inventory\hosts.yml` currently lists `secops` as `192.168.1.250`, while the active host is `192.168.1.215`.

The first sub-project should not attempt to build the entire end-state product in one pass. The scoped goal is the platform foundation:

- deploy and operate Clearwing-backed jobs on `secops`
- support both `network_scan` and `source_hunt`
- persist runs, findings, and provenance in a transactional source of truth
- expose stable APIs for a later multi-user UI

This phase explicitly does **not** require a full production UI, search cluster, or generalized plugin marketplace.

## Non-Goals

- Rebuilding the GVM/OpenVAS workflow or importing its existing state
- Shipping a polished end-user UI in v1
- Designing a public SDK before the service contracts are stable
- Supporting every possible scanner or runtime engine in the first cut
- Making search indexing the primary persistence layer

## Recommended Approach

Use a **control plane + worker** architecture with Postgres as system of record and local S3-compatible storage for artifacts. This is the smallest design that still preserves the right boundaries for a real multi-user platform.

### Why this approach

- It avoids coupling the UI directly to Clearwing.
- It separates control concerns from execution concerns.
- It supports future modular runtimes and additional scanners without redefining the database model.
- It keeps the first implementation tractable while matching the long-term product direction.

## Architecture

The first slice has four major components:

### 1. Control API

The control API is the stable service boundary for all future clients. It owns:

- users and organizations/tenants
- credential references
- model/provider selection
- runtime profiles
- targets, repos, binaries, and SBOM inputs
- run creation, state tracking, cancellation, and retrieval
- normalized findings and artifact metadata queries

The UI should eventually talk only to this API, never directly to Clearwing or storage backends.

### 2. Execution Workers

Workers are responsible for:

- resolving the selected runtime profile
- materializing credentials just-in-time
- staging job inputs in isolated scratch space
- invoking Clearwing
- collecting raw outputs, logs, and evidence artifacts
- normalizing findings back into the database

Workers should be stateless apart from temporary scratch storage and references to persisted state.

### 3. Postgres Source of Truth

Postgres is the canonical record for:

- identities and ownership
- selected credential/model/runtime context
- targets and input provenance
- run lifecycle states
- normalized findings
- artifact metadata and storage references

This is intentionally **transactional-first**. Search projections can be added later without replacing the source of truth.

### 4. Local S3-Compatible Object Storage

Artifacts should be stored in a local S3-compatible store from day one rather than plain filesystem paths. This preserves portability while still allowing a fully local homelab deployment.

The system should store object references in Postgres rather than embedding local disk paths into API contracts. That way the storage provider can later move to cloud S3-compatible infrastructure with minimal API churn.

## Internal SDK Boundary

The platform should be implemented service-first, but with an internal core package rather than embedding business logic into the API and worker entrypoints.

The internal core package should own:

- job spec models
- scanner/runtime interfaces
- normalized finding contracts
- artifact/evidence metadata contracts
- credential reference models
- run state transition rules

This is an **internal SDK/core library**, not a public product SDK. A public SDK should only be considered after the service contracts stabilize.

## Supported Job Families

The platform-facing v1 job families should be:

- `network_scan`
- `source_hunt`

That keeps the external API small while still supporting Clearwing’s main workflows.

### `network_scan`

Used for live hosts, services, and web targets.

Platform responsibilities:

- accept one or more network targets
- apply runtime and scope constraints
- invoke the Clearwing network/pentest flow
- persist both raw execution artifacts and normalized findings

### `source_hunt`

Used for source repositories and related source-oriented artifacts.

Initial input kinds:

- `repo`
- `local_source_tree`
- `binary`
- `sbom`

The `source_hunt` family can later support specialized modifiers such as N-day validation or reverse-engineering modes without exploding the top-level API into many separate job types.

## Execution Flow

Run creation must make model/provider selection an explicit first-class input.

When a user creates a run, they select:

- job type: `network_scan` or `source_hunt`
- target input
- runtime profile
- credential
- provider/backend
- model identifier

### Separation of concerns

- **Credentials** answer: which account or endpoint is used?
- **Provider/model selection** answers: which model on that endpoint is used?
- **Runtime profile** answers: how should the scan behave?

### Run lifecycle

Each run should move through explicit states:

- `queued`
- `staging`
- `running`
- `normalizing`
- `completed`
- `failed`
- `canceled`
- `needs_review`

The run record should persist a resolved execution snapshot containing:

- runtime profile version or resolved settings snapshot
- credential reference
- provider/backend
- model identifier
- target snapshot
- relevant policy flags and limits

This snapshot is required for reproducibility and auditability when user defaults change later.

## User Profiles, Credentials, and Defaults

Both shared platform credentials and per-user credentials must be usable from the start.

### Credential ownership modes

- **Shared platform/org credentials**
  - managed by admins
  - available according to policy and runtime profile rules
- **User-owned credentials**
  - managed by individual users
  - selectable for runs they own or are authorized to launch

### User profile defaults

Each user profile should support:

- multiple saved credentials
- a default credential
- a default provider/model pairing
- optional per-job-family defaults
  - for example, a default for `network_scan`
  - another default for `source_hunt`

### Supported credential/backend patterns

The model should accommodate at least:

- OpenAI-hosted APIs
- Anthropic-hosted APIs
- OpenAI-compatible endpoints
- OpenRouter-style aggregators
- local/self-hosted endpoints such as Ollama

Secrets should be encrypted at rest and only materialized inside workers during execution.

## Runtime Profiles

Runtime profiles define execution behavior independently from credentials. They exist to make the platform modular and enforceable.

Runtime profiles should cover:

- scanner mode and depth
- concurrency
- timeout/budget limits
- tool or sandbox policy
- exploit-attempt permissions
- whether human approval is required before sensitive actions
- whether shared credentials may be used
- whether local/self-hosted backends are allowed
- allowed provider/model sets for that profile

Users can choose among allowed credentials and models, but only within the guardrails of the selected runtime profile.

## Data Model Direction

Postgres should hold structured relational records backed by selective `JSONB` fields for provider- or scanner-specific payloads.

Core entity families include:

- organizations/tenants
- users
- credentials
- runtime profiles
- provider/model selections
- targets
- repositories
- SBOM records
- binaries
- runs
- findings
- evidence/artifacts
- run events and failure details

### `JSONB` usage

Use `JSONB` for:

- raw Clearwing outputs
- provider-specific metadata
- parser leftovers
- evolving execution details that should not force immediate schema churn

Use typed columns for:

- ownership
- timestamps
- foreign-key relationships
- lifecycle state
- severity/status fields
- storage references

## Provenance and Artifact Tracking

The platform should treat all scan inputs as tracked artifacts with provenance metadata, especially SBOMs.

### General provenance requirements

Targets and uploaded/generated artifacts should preserve:

- source type
- source location
- retrieval timestamp
- related asset/context
- checksum or digest
- canonical object storage reference
- first seen / last seen where relevant

### SBOM requirements

An SBOM record should capture:

- source type
  - manual upload, downloaded by URL, pulled from registry, extracted from repo/build output, imported from another system
- source location
- source retrieval timestamp
- install timestamp or observed deployment timestamp
- original filepath
- current filepath
- checksum/digest
- format
  - SPDX, CycloneDX, etc.
- related asset
  - host, image, repo, package, deployment, or environment
- canonical object storage reference

This is necessary for both auditability and later UI correlation.

## Raw vs Normalized Results

Normalization is a first-class pipeline stage.

The system should preserve three layers:

### 1. Raw execution output

Keep the direct outputs from Clearwing and supporting tools for:

- debugging
- audit trails
- future parser improvements

### 2. Normalized findings

Persist a scanner-agnostic finding model so that:

- findings can be queried across runs
- future scanners can coexist with Clearwing
- the later UI does not need to understand every raw output shape

### 3. Evidence and artifact metadata

Store references linking findings back to:

- exact run
- exact target or repo snapshot
- credential/model/runtime context
- persisted evidence objects

## Failure Handling and Safety

Because Clearwing is an agentic offensive-security tool, the execution layer must be auditable, constrained, and resumable.

### Failure recording

Failures should capture:

- machine-readable failure code/category
- human-readable failure summary
- worker logs
- raw stderr/stdout
- links to relevant staged artifacts where safe

This allows the system to distinguish:

- provider/model failures
- target connectivity failures
- auth/permission failures
- sandbox/runtime failures
- parser/normalization failures

### Safety controls

Safety controls should be enforced by runtime profile and execution policy, including:

- exploit-attempt permissions
- human approval requirements for sensitive actions
- network scope restrictions
- allowed tool/runtime classes
- budget and time limits
- concurrency limits
- provider/backend restrictions

## API and UI Boundary

The first slice should expose stable APIs for:

- managing credentials
- managing provider/model preferences
- managing runtime profiles
- registering targets, repos, binaries, and SBOMs
- launching runs
- observing run state and retrieving logs/artifacts
- querying normalized findings and provenance metadata

The future UI should be built on these APIs rather than sharing internal worker or storage assumptions.

## Deployment Direction

The first deployment target is `secops` at `192.168.1.215`.

Existing Ansible automation should be updated rather than bypassed. The current `secops_docker_stack` role and stale inventory entry provide a natural deployment path, but they need to be reworked away from the current OpenVAS-centric compose definition.

At minimum, the infrastructure plan should account for:

- correcting the inventory host to `192.168.1.215`
- replacing or retiring the current `openvas` service path
- provisioning Postgres
- provisioning local S3-compatible storage
- provisioning the control API and worker services
- mounting appropriate persistent storage under `/mnt/storage`

## Testing Strategy

The first implementation should include tests for:

- run creation validation
- credential ownership and permission rules
- provider/model/runtime selection behavior
- run state transitions
- worker retry and idempotency behavior
- raw-to-normalized ingestion
- artifact provenance persistence
- SBOM metadata persistence
- shared vs user-owned credential policy enforcement

## Open Questions Deferred

These items should be intentionally deferred to later phases rather than forced into the first implementation:

- full end-user UI and UX design
- OpenSearch or other secondary indexing
- public SDK design
- support for additional scanners beyond the initial abstraction boundary
- cloud object storage migration specifics
- advanced org/tenant billing policy

## Summary

The first sub-project should be a multi-user Clearwing platform foundation with:

- a control API as the stable system boundary
- worker-based execution for `network_scan` and `source_hunt`
- Postgres as transactional source of truth
- local S3-compatible storage for artifacts
- both shared and per-user credentials available from day one
- explicit provider/model selection per run
- strong provenance tracking, especially for SBOMs
- runtime-profile-based safety and policy controls

This gives a usable replacement trajectory for the current GVM setup without hard-coding the product to today’s Clearwing CLI shape or today’s infrastructure layout.
