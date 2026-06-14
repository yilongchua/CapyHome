# Seamless Local Onboarding and Web Search Setup

## 1. Goal

Make the supported first-run path:

1. The user opens a release link or scans a QR code.
2. A small installer is downloaded.
3. The user runs one terminal command.
4. CapyHome is installed on the Desktop, configured with safe defaults, started in local production mode, and opened at `http://localhost:2026`.
5. Inside **Settings > Setup**, the user can configure an LLM and enable, start, stop, update, or repair the WebSearch services bundled into CapyHome's Docker deployment.
6. Once WebSearch is healthy, CapyHome registers and enables its MCP server automatically.

The first-run path must not require manually copying example files, editing YAML/JSON, manually cloning a second repository, or typing Docker Compose commands. The installer manages separate CapyHome and WebSearch checkouts. WebSearch remains an independently developed codebase; only its deployment and update lifecycle are integrated into CapyHome.

## 2. Current-State Findings

- `README.md` and `SETUP.md` still advertise `make local-stack-start`, which assumes unrelated repositories already exist on the user's Desktop.
- `scripts/local-stack.sh` contains machine-specific paths and manages WebSearch, ComfyUI, browser automation, and local LLM concerns together. It is unsuitable as the public onboarding path.
- `config.example.yaml` is not a portable initial config:
  - It defines a specific LM Studio model.
  - It points WebSearch to `http://192.168.1.39:9000`.
  - It enables optional services before they are installed.
- No committed secret was found in the current example files. The real `config.yaml`, `.env`, and `extensions_config.json` are correctly gitignored and may contain local secrets, so they must remain untracked.
- `extensions_config.example.json` already contains the desired WebSearch MCP shape (`http://localhost:9000/mcp` and `/health`) but leaves it disabled.
- The Settings dialog already has provider onboarding and MCP management. WebSearch setup should reuse those APIs and patterns.
- The Gateway can persist MCP configuration, but its current full-replacement endpoint is a poor fit for installers because concurrent updates can overwrite unrelated MCP changes.
- A web page cannot silently install Docker Desktop or execute host Docker commands. The host-side installer and the running Gateway must own those operations, with explicit user actions in the UI.

## 3. Product Decisions

### 3.0 WebSearch ownership boundary

Merge WebSearch into the **CapyHome Docker product**, but do not merge its source code into the CapyHome repository.

The recommended boundary is:

- The WebSearch repository owns its application code, tests, Dockerfiles, Compose topology, and releases.
- CapyHome owns the user-facing orchestration: service definitions, profiles, health checks, networking, persistence, worker scale, Settings controls, and MCP registration.
- The installer clones WebSearch into a managed sibling directory without copying its source into CapyHome.
- CapyHome users do not manually operate the WebSearch checkout.
- The Settings **Update All** action fast-forwards both configured repositories to their latest configured branches, rebuilds the Docker stack, and restarts it.

Do not use a Git submodule. Keep two normal checkouts so each repository can be fetched, validated, updated, and repaired independently.

Do not copy WebSearch Dockerfiles or application source into CapyHome. That would create two release authorities and make security fixes/version ownership ambiguous.

The integration contract between the repositories should be small and versioned:

- Repository URLs and update branches.
- Expected Compose file and build context.
- Required environment variables.
- Exposed internal ports.
- `/health` behavior.
- `/mcp` transport contract.
- Compose service names.
- Persistent volume expectations.
- Supported worker scaling.

### 3.1 Supported installation path

Ship a versioned release installer rather than linking users to a raw repository ZIP.

- macOS/Linux artifact: `install-capyhome.sh`
- Optional macOS convenience artifact: `CapyHome.command`
- Windows follow-up artifact: `install-capyhome.ps1`
- QR code target: the HTTPS release landing page, not a mutable `main` branch file.
- Default install directory: `~/Desktop/CapyHome`
- Managed WebSearch directory: `~/Desktop/websearch`
- Existing directory behavior: update only when it is a valid CapyHome Git checkout; otherwise stop with a clear error.
- The installer clones both repositories and records their URLs, branches, and paths in a local managed-install manifest.

The documented one-command experience should be:

```bash
bash ~/Downloads/install-capyhome.sh
```

Browsers normally download into `~/Downloads`, not the Desktop. The installer itself should place CapyHome on the Desktop. Marketing copy should not promise that the browser controls the download destination.

### 3.2 Local production runtime

Add a production Compose file instead of using the development stack:

```text
docker/docker-compose.prod.yaml
docker/docker-compose.websearch.yaml
```

Production requirements:

- Build immutable frontend, Gateway, and LangGraph images.
- Run `next start`, not `next dev`.
- Remove source-code bind mounts and reload flags.
- Persist application state in named volumes or an explicit host data directory.
- Keep `localhost:2026` as the only normal user-facing port.
- Add health checks for nginx, frontend, Gateway, and LangGraph.
- Pin image/dependency versions for releases.
- Use `restart: unless-stopped`.
- Do not require Node, pnpm, uv, Python, or nginx on the host.
- Declare WebSearch and its supporting services in a CapyHome-owned Compose integration file while building from the managed WebSearch checkout.
- Build WebSearch from the managed WebSearch checkout referenced by `WEBSEARCH_ROOT`.
- Attach WebSearch to the private CapyHome Compose network. Do not publish port 9000 unless an advanced/debug option explicitly enables host access.
- Address the MCP server through Docker DNS, for example `http://websearch:9000/mcp`, rather than through `localhost` or a host bridge.

Compose structure:

```yaml
# docker/docker-compose.websearch.yaml
services:
  websearch:
    build:
      context: ${WEBSEARCH_ROOT}
      dockerfile: Dockerfile
    image: capyhome-websearch:local
    # Do not set container_name; Compose cannot scale a service with a fixed name.
    expose:
      - "9000"
    networks:
      - capyhome
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9000/health"]
      interval: 10s
      timeout: 5s
      retries: 20
    restart: unless-stopped
```

The complete file must preserve the tested topology from WebSearch's `docker-compose.multi.yml`, including its nginx ingress, shared configuration, output storage, and dashboard sidecar. CapyHome owns the invocation and integration wiring; WebSearch remains the source of truth for how its containers are built and run.

Use one stable Compose project name, such as `capyhome`, for the core and WebSearch files:

```bash
docker compose \
  -p capyhome \
  -f docker/docker-compose.prod.yaml \
  -f docker/docker-compose.websearch.yaml \
  up -d --remove-orphans
```

For deployments where WebSearch is optional, either:

1. Include its Compose file only when enabled, or
2. Put WebSearch services behind a `websearch` Compose profile.

Prefer selecting the additional Compose file because it makes the active deployment explicit and avoids profile-related surprises during `down`, `ps`, and updates.

Expose one idempotent root command:

```bash
make start
```

For the user-facing installation, redefine this as the Docker production path. Keep development startup under `make dev`; if the current host-native production command remains useful, rename it to `make start-native`.

`make start` must:

1. Verify Docker and Compose.
2. Create missing local config files without overwriting existing files.
3. Generate required local secrets.
4. Validate configuration.
5. Build/pull and start the production stack.
6. Wait for `/api/health`.
7. Print and, where supported, open `http://localhost:2026`.

### 3.3 Configuration ownership

Do not commit a real `config.yaml`. Keep generated local files gitignored.

Create a safe baseline template, either by simplifying `config.example.yaml` or adding `config.quickstart.yaml`:

- No API keys, tokens, personal paths, LAN IPs, or machine-specific model IDs.
- Optional services disabled until configured.
- WebSearch URL expressed only in the MCP extension after activation.
- A valid no-model startup state so the Settings UI can load before an LLM is configured.
- `$ENV_VAR` references for every secret.

Add `scripts/bootstrap-config.sh`, which is idempotent:

- Copy templates only when files are absent.
- Generate `BETTER_AUTH_SECRET` with a cryptographically secure source.
- Create `.env` with mode `0600`.
- Preserve all existing user configuration.
- Validate YAML/JSON before startup.
- Print field-level validation errors without printing secret values.

Settings-managed values should continue to live in `extensions_config.json`. Secrets entered in the UI need a later hardening pass: store secret references in JSON and actual values in `.env` or a local secret store, and redact secrets from GET responses.

## 4. Settings Experience

Add a first navigation item: **Settings > Setup**. Reopen it automatically while required setup is incomplete.

### 4.1 Setup overview

Display five cards:

| Card | States | Primary action |
|---|---|---|
| CapyHome | healthy, starting, degraded | Retry / View logs |
| Docker | installed-running, installed-stopped, missing, unsupported | Start Docker / Download Docker |
| LLM Provider | configured, missing, unhealthy | Configure provider |
| Web Search | disabled, building, starting, healthy, unhealthy | Enable with Docker / Enable with Podman / Repair |
| Software Update | checking, current, update available, updating, restarting, failed | Update All |

Completion criteria:

- CapyHome Gateway is healthy.
- At least one enabled LLM provider passes its connection test.
- WebSearch is optional, but its card clearly states which features are unavailable until installed.

Store only presentation state such as dismissed/completed hints in local storage. Derive operational state from the backend on every open.

`update available` means either managed repository's configured remote branch contains commits not present in the local checkout.

### 4.2 Docker interaction

The **Download Docker** button should open the official OS-specific Docker Desktop installation page. It must not claim to install Docker automatically.

The backend status response should distinguish:

- Docker CLI missing.
- Docker daemon not running.
- Compose plugin missing.
- Docker healthy.
- Unsupported deployment where host Docker control is disabled.

After Docker Desktop is installed, the user presses **Check again**. A containerized CapyHome service cannot reliably launch a host desktop application. An optional **Open Docker Desktop** action requires a native host helper installed by the release installer; otherwise the UI should show the normal OS launch instructions.

### 4.3 WebSearch one-step activation

The primary action is **Enable Web Search**. One click should:

1. Verify host-control policy and Docker health.
2. Verify the managed WebSearch checkout and required Compose files.
3. Build the WebSearch image from the managed checkout.
4. Reconcile the CapyHome Compose project with `docker-compose.websearch.yaml`.
5. Start nginx, dashboard, and eight WebSearch replicas.
6. Wait for the container health checks to pass.
7. Upsert and enable the `websearch` MCP entry using `http://websearch:9000/mcp`.
8. Preview MCP tools and verify at least one tool is returned.
9. Report success without requiring a CapyHome restart.

Equivalent operator command:

```bash
docker compose \
  -p capyhome \
  -f docker/docker-compose.prod.yaml \
  -f docker/docker-compose.websearch.yaml \
  up -d --build --scale websearch=8 --remove-orphans
```

This wraps the behavior of WebSearch's `docker-compose.multi.yml` while keeping source ownership in the WebSearch checkout.

Use eight WebSearch replicas as the recommended default. Store the replica count in local configuration so a future Advanced setting can change it without changing Compose files.

WebSearch supports two optional runtime buttons:

- **Enable with Docker** joins WebSearch to the CapyHome Docker network and registers `http://websearch-proxy:9000/mcp`.
- **Enable with Podman** runs WebSearch in a separate Podman network, publishes port 9000, and registers `http://host.docker.internal:9000/mcp` for the Docker-hosted CapyHome services.

Persist the selected runtime so Repair and Update All continue using the same engine. Switching runtime stops the previous WebSearch stack first to avoid a port-9000 collision.

### 4.4 Recovery actions

Provide explicit actions:

- Start
- Stop
- Restart
- Repair
- Update All
- View recent logs
- View WebSearch release
- Disable WebSearch

`Repair` reruns validation, Compose reconciliation, health checks, and MCP upsert. It must not delete volumes by default.

`Disable WebSearch` stops/removes WebSearch containers and disables its MCP entry. It preserves named volumes unless the user separately confirms **Delete WebSearch data**.

### 4.5 Update All

Add one **Update All** action under Settings > Setup. It updates both repositories and restarts the full deployment.

Required flow:

1. Check Docker. If the CLI, daemon, or Compose is unavailable, stop immediately and show one simple error such as `Docker is not running. Start Docker Desktop and try again.`
2. Verify that both managed directories are the expected Git repositories.
3. Refuse to update a repository with uncommitted changes. Show which repository is dirty; never discard user changes.
4. Fetch both remotes before changing either checkout.
5. Verify both configured branches can be fast-forwarded. Do not merge, rebase, or resolve conflicts automatically.
6. Record the old and target commit SHA for both repositories in an update-state file.
7. Fast-forward both repositories to their remote branch tips.
8. Run the combined Docker Compose build and `up -d --scale websearch=<configured count> --remove-orphans`.
9. Wait for CapyHome, WebSearch nginx, and the replica-distribution smoke test.
10. Reconcile the WebSearch MCP entry.
11. Mark the update complete and let the Settings UI reconnect after the application restarts.

The update action must run in a detached host updater or a small bootstrap service outside the application Compose project. Gateway cannot reliably pull and restart the project that is currently serving the update request. The UI should receive an accepted response, show **Updating and restarting**, then poll `http://localhost:2026/api/health` until the new stack returns.

This is intentionally “latest configured branch,” not an atomic release bundle. If one repository advances incompatibly, the combined build or health gate may fail. Preserve the recorded SHAs so **Retry** can continue from known state and diagnostics can show exactly which versions were attempted.

## 5. Backend Design

Create a managed integrations module rather than allowing the frontend to submit shell commands.

Suggested files:

```text
backend/src/integrations/
  models.py
  registry.py
  runner.py
  websearch.py
backend/src/gateway/routers/setup.py
backend/tests/test_setup_router.py
backend/tests/test_websearch_integration.py
```

### 5.1 API contract

```text
GET    /api/setup/status
POST   /api/setup/integrations/websearch/enable
POST   /api/setup/integrations/websearch/start
POST   /api/setup/integrations/websearch/stop
POST   /api/setup/integrations/websearch/restart
POST   /api/setup/integrations/websearch/repair
POST   /api/setup/integrations/websearch/update
POST   /api/setup/integrations/websearch/disable
DELETE /api/setup/integrations/websearch/data
GET    /api/setup/jobs/{job_id}
GET    /api/setup/integrations/websearch/logs
GET    /api/setup/update/status
POST   /api/setup/update/all
```

Long operations return `202 Accepted` and a `job_id`. The frontend polls job state initially; SSE can be added later if build progress needs streaming.

Job phases:

```text
queued -> checking_prerequisites -> fetching_repositories -> validating_fast_forward
       -> updating_repositories -> building -> reconciling_compose -> starting
       -> checking_health -> registering_mcp -> verifying_tools -> succeeded
       -> failed | cancelled
```

Persist job metadata under `.capyhome/setup/jobs/`, with bounded, redacted logs. On Gateway restart, mark interrupted jobs failed and make `Repair` available.

### 5.2 Command safety

- Commands are assembled from a server-owned integration manifest.
- Never accept image names, Compose files, service names, scale values outside validated bounds, or arbitrary arguments from the browser.
- Use `asyncio.create_subprocess_exec`, not `shell=True`.
- Set explicit working directories and timeouts.
- Allow only the two managed repository URLs, configured branches, known Compose files, service names, and validated scale values.
- Update repositories only with `git fetch` followed by a verified fast-forward. Never run an automatic merge, rebase, reset, or force checkout.
- Redact credentials and environment values from logs.
- Serialize operations with a per-integration lock.
- Make every action idempotent.

### 5.3 Host control policy

Docker socket access is already mounted into Gateway/LangGraph in the development Compose file. For production, isolate setup authority:

Preferred final architecture:

- A small `setup-agent` service owns `/var/run/docker.sock` and read-only access to the CapyHome Compose manifests.
- Gateway calls it over a private Compose network.
- The service exposes only typed integration operations.
- LangGraph and frontend never receive the Docker socket.
- The installer sets an absolute `CAPYHOME_ROOT` and mounts the release Compose directory into `setup-agent` at the identical absolute path. This prevents Docker daemon path mismatches when the agent invokes Compose.
- WebSearch persistence should use named volumes. Avoid host bind mounts in the bundled WebSearch deployment unless the path is generated and validated by CapyHome.

Acceptable first release:

- Gateway owns the Docker socket and setup endpoints.
- Endpoints bind only through the authenticated local API.
- Remote/non-local deployments disable managed setup with `CAPYHOME_MANAGED_SETUP_ENABLED=false`.

Do not expose these endpoints without authentication when CapyHome is reachable beyond loopback.

### 5.4 Atomic MCP upsert

Add a narrow endpoint/helper instead of read-modify-write from the frontend:

```text
PUT /api/mcp/servers/{server_name}
```

It should:

- Lock the config file.
- Reload the latest JSON.
- Update only the named server.
- Preserve unknown top-level keys, skills, community tools, user models, and other MCP servers.
- Write through a temporary file and atomic rename.
- Reload the in-process config cache.
- Return the saved server with secret fields redacted.

For WebSearch, save:

```json
{
  "enabled": true,
  "type": "http",
  "url": "http://websearch:9000/mcp",
  "health_url": "http://websearch:9000/health",
  "timeout_seconds": 25,
  "description": "Local WebSearch MCP managed by CapyHome"
}
```

## 6. Frontend Design

Suggested files:

```text
frontend/src/components/workspace/settings/setup-settings-page.tsx
frontend/src/components/workspace/settings/setup-status-card.tsx
frontend/src/components/workspace/settings/websearch-setup-card.tsx
frontend/src/core/setup/api.ts
frontend/src/core/setup/hooks.ts
frontend/src/core/setup/types.ts
```

Changes:

- Add `"setup"` to `SettingsSection` in `settings-dialog.tsx`.
- Add Setup labels and action/error text to locale types and `en-US.ts`.
- Reuse the existing LLM settings page or deep-link to it.
- Reuse React Query for status polling and job invalidation.
- Show the exact current phase during install.
- Preserve the last useful error and offer **Copy diagnostics**.
- Disable duplicate actions while a job is active.
- Meet keyboard, focus, reduced-motion, and screen-reader requirements.
- Keep generic MCP editing under **Tools**; managed WebSearch belongs under **Setup** and appears read-only/managed in the generic list.

## 7. Installer and Release Flow

Add:

```text
scripts/install-capyhome.sh
scripts/bootstrap-config.sh
scripts/start-production.sh
scripts/check-production.sh
docker/docker-compose.prod.yaml
docker/docker-compose.websearch.yaml
```

Installer behavior:

1. Detect supported OS and architecture.
2. Verify `git`, `curl`, and Docker.
3. If Docker is missing, open/print the official installer URL and exit with a resumable message.
4. Clone CapyHome into `~/Desktop/CapyHome`.
5. Clone WebSearch into `~/Desktop/websearch`.
6. Write the managed-install manifest with both origins, branches, and paths.
7. Run `scripts/bootstrap-config.sh`.
8. Run `make start`.
9. Wait for health and open the UI.

Release automation:

- Build and attach installer artifacts to each GitHub Release.
- Publish SHA-256 checksums.
- Embed the release tag and expected CapyHome repository.
- Generate a QR code that points to a stable release landing URL.
- Test the installer on a clean macOS runner/VM and a Linux VM.
- Never pipe an unsigned mutable remote script directly into a shell in primary documentation.

## 8. Documentation Migration

Update `README.md` and replace `SETUP.md` with two clearly separated paths:

### Users

```bash
bash ~/Downloads/install-capyhome.sh
```

Then open `http://localhost:2026` and finish setup under **Settings > Setup**.

### Contributors

Keep `make check`, `make install`, and `make dev`.

Remove the public “Fully local research stack” instructions and references to `make local-stack-start`. Deprecate these Make targets for one release, printing a message that directs users to Settings > Setup, then remove `scripts/local-stack.sh` after its unrelated ComfyUI/browser/LLM responsibilities have dedicated owners.

Document the WebSearch source repository for contributors and attribution. The installer clones and manages it automatically; users should not need to enter that checkout or run its commands directly.

## 9. Test Plan

### Backend unit tests

- Docker missing, daemon stopped, Compose missing, and healthy detection.
- WebSearch state derivation from checkout validity, built image, containers, health endpoint, and MCP config.
- Command argument construction exactly matches the approved Compose invocation.
- Repository, branch, Compose-file, service-name, and scale allow-list enforcement.
- Dirty checkout and non-fast-forward update rejection.
- Partial dual-repository update state and retry behavior.
- Job state transitions, timeout, cancellation, restart recovery, and log redaction.
- Concurrent MCP updates preserve unrelated configuration.
- Atomic writes survive a simulated failure.
- MCP is enabled only after WebSearch health and tool preview succeed.

### Frontend checks

- Status card rendering for every state.
- Enable/update progress and failed-step recovery.
- No duplicate mutation while a job is active.
- Setup completion/deep-link behavior.
- Static website mode hides host-control actions.
- `pnpm lint` and `pnpm typecheck`.

### End-to-end matrix

- Fresh macOS install with Docker already running.
- Fresh macOS install with Docker missing.
- Existing CapyHome install with user config.
- Existing legacy WebSearch checkout and separately running containers.
- Port 9000 conflict.
- WebSearch fetch or image build failure.
- Health endpoint never becomes ready.
- CapyHome restart during installation.
- Offline/partial download recovery.
- Linux host resolution via `host-gateway`.
- Upgrade from the deprecated `local-stack` setup without losing data.

Acceptance target: from an existing Docker installation, a new user reaches a healthy CapyHome UI with one terminal command, and enables healthy WebSearch MCP with one Settings action and no manual file edits.

## 10. Feasibility, Risk, and Impact Assessment

### 10.1 Overall verdict

The idea is **feasible and beneficial if implemented in stages**.

Bundling WebSearch into the CapyHome deployment while keeping its source repository separate is a good product boundary:

- Users experience one product, one installer, one Settings surface, and one support path.
- Maintainers retain independent code ownership and release cadence.
- CapyHome can manage both checkouts and remove several machine-specific setup steps.
- MCP registration becomes deterministic rather than dependent on users editing JSON.

The proposal becomes risky if “one click” is interpreted as allowing the browser-facing Gateway to run unrestricted Docker and Git commands. Seamlessness should come from a constrained, versioned deployment contract, not from a general remote shell.

Recommended decision:

- **Go** for one-command CapyHome startup, managed dual-repository checkouts, bundled WebSearch Compose orchestration, automatic health checks, and automatic MCP registration.
- **Go** for a Settings **Update All** button that updates both repositories and restarts, provided the operation runs through a detached updater that survives the restart.
- **Go** for simple Docker failure handling: stop immediately, preserve the current installation, and show a concise retry message.
- **No-go** for arbitrary shell execution, automatic conflict resolution, silent Docker Desktop installation, or exposing Docker-management APIs on a remotely reachable instance.

### 10.2 Expected impact

Positive user impact:

- Removes the manual second-repository clone and manual `docker compose` command.
- Eliminates incorrect Desktop paths and LAN-specific URLs.
- Reduces setup from several technical decisions to Docker plus an LLM provider.
- Makes failures visible and recoverable in one place.
- Gives support a consistent deployment topology and diagnostics bundle.

Positive engineering impact:

- Establishes a tested compatibility matrix between CapyHome and WebSearch.
- Moves infrastructure assumptions into version-controlled Compose and health contracts.
- Makes WebSearch activation and MCP registration idempotent.
- Separates contributor workflows from end-user installation.

Costs:

- CapyHome becomes operationally responsible for another multi-service stack.
- Release testing, build security, disk use, startup time, and support burden increase.
- Cross-repository compatibility and coordinated security releases become ongoing work.
- The installer and setup control plane become security-sensitive product components.

### 10.3 Highest-risk pitfalls

#### Docker socket authority

Mounting `/var/run/docker.sock` gives a container effective root-level control of the host. A vulnerability in a setup endpoint, dependency, or browser request path could create privileged containers, mount host files, or exfiltrate secrets.

Mitigations:

- Run updates through a detached host updater or isolated bootstrap service with no LLM or general tool execution.
- Expose only typed operations for known service IDs.
- Require local authentication, CSRF protection, strict origin checks, and loopback-only exposure by default.
- Disable managed setup automatically when public binding or remote deployment is detected.
- Never pass user-provided command fragments to Docker or Compose.

#### Self-management failure

A setup service inside the same Compose project can stop or recreate itself while running `compose up`, `down`, or `update`. The UI may report a timeout even when the operation succeeds, or leave a partially updated stack.

Mitigations:

- In phase 1, let the host installer/CLI own Compose reconciliation.
- If a setup service is added, keep it in a stable bootstrap project separate from the application project it manages.
- Use durable job records and reconciliation after reconnect rather than assuming a continuous HTTP request.
- Never include the control service in a `down` operation it initiated.
- Treat updates as desired-state changes that can resume after process restart.

#### Bootstrap dependency loop

Settings cannot repair CapyHome when the frontend or Gateway cannot start. It also cannot install or launch Docker when Docker is absent unless a trusted native host helper already exists.

Mitigations:

- Keep `make start`, `make doctor`, and `make repair` as complete non-UI recovery paths.
- Make the installer resumable after Docker installation.
- Print exact recovery commands and log locations on startup failure.
- Treat Settings as the happy-path control surface, not the only control surface.

#### Resource consumption

Eight WebSearch replicas plus search, crawling, queue, database, and cache services may overwhelm common laptops. Symptoms include Docker memory pressure, swap usage, thermal throttling, slow LLM inference, and apparent CapyHome hangs.

Mitigations:

- Keep eight as the recommended default, as requested, but state the expected hardware profile and resource cost.
- Persist the replica count as configuration so a future Advanced setting can lower or raise it.
- Display current replica count and Docker resource pressure in diagnostics.
- Apply container memory/CPU limits where the workload tolerates them.
- Test CapyHome plus local LLM plus WebSearch together, not in isolation.

#### First-run latency and disk usage

Building CapyHome and WebSearch after pulling source changes can turn “one click” into a long operation and consume substantial bandwidth, CPU, and disk. A silent progress spinner will look broken.

Mitigations:

- Reuse Docker layer cache across updates.
- Publish approximate first-build and incremental-update requirements.
- Show fetch, build, restart, and health phases, elapsed time, and recent logs.
- Make retry idempotent.
- Add disk-space preflight and actionable cleanup instructions.

#### Cross-platform variability

Docker Desktop behavior, filesystem sharing, architecture, networking, command availability, and browser download handling differ across macOS, Windows, and Linux.

Mitigations:

- Define the initial support matrix explicitly. A macOS-first release is preferable to claiming untested universal support.
- Ensure both repositories' Dockerfiles and base images build on `linux/amd64` and `linux/arm64`.
- Avoid host bind mounts and host networking.
- Test Intel macOS, Apple Silicon, and the selected Linux distribution in clean VMs.
- Add Windows only with a native PowerShell installer and Windows-specific recovery tests.

#### WebSearch topology assumptions

The eight-container design is structurally valid, but its success depends on routing and shared-filesystem behavior.

Verified from the current WebSearch checkout:

- `websearch` has no fixed `container_name`, so Compose can create eight replicas.
- Every replica embeds its own FastAPI server, SearXNG process, and Crawl4AI runtime. These are genuinely independent search/crawl workers rather than eight shells around one shared worker.
- Only nginx publishes host port 9000. Replica API ports stay inside the Compose network, so they do not collide on the host.
- Each replica has its own in-process admission semaphore. Eight replicas therefore multiply aggregate request capacity.
- All replicas mount the same output directory and configuration files.

Critical issues to resolve:

1. **Traffic distribution is not yet proven.** The documented design says nginx uses `least_conn`, but the current checked-in nginx config has one service-name upstream rather than an explicit least-connection pool. nginx may resolve the Compose DNS name to only one replica for long periods.
2. **The local dynamic-DNS modification is runtime-specific.** It uses resolver `10.89.0.1`, which is associated with Podman networking and is not Docker's normal embedded DNS (`127.0.0.11`). Shipping that value would make Docker behavior unreliable.
3. **Scale does not guarantee utilization.** Eight healthy containers provide no benefit if nginx sends most requests to one or two addresses. The existing UAT passes after observing only two upstreams, which is too weak for an eight-replica recommendation.
4. **Shared output has cross-process races.** Package directories are collision-resistant and event appends use append-only file writes, which is encouraging. However, all replicas can independently rewrite retention files and marker files. Retention currently has no cross-process lock, so one replica can prune or rewrite while another appends.
5. **Upstream rate pressure multiplies.** Eight embedded SearXNG instances can query external engines concurrently. Local capacity may rise while remote engines throttle or block the host IP.
6. **Per-replica limits are not global limits.** If each replica allows two extraction workers, eight replicas permit up to sixteen concurrent crawls, plus browser subprocesses. This is the intended throughput gain, but it explains the CPU/RAM impact.
7. **Restart discovery must be tested.** Replica IPs change after recreate. nginx must re-resolve service DNS or be recreated after scale/restart without dropping the public endpoint for an extended period.

Mitigations:

- Preserve nginx as the single MCP/API ingress and scale only the `websearch` service.
- Replace the hardcoded resolver with a Docker-compatible, tested routing design. Options include generating an nginx upstream list during reconciliation, using a DNS-aware proxy configuration that handles multiple A records, or recreating nginx after every replica change.
- If `least_conn` is required, configure and verify a real upstream pool rather than relying on DNS round-robin.
- Strengthen the live test: issue at least 32 concurrent requests, observe all eight upstream container identities, require a reasonable distribution bound, then kill/recreate replicas and repeat.
- Add a multi-process shared-output stress test. Protect retention/marker rewrites with a filesystem lock or move retention into one dedicated sidecar.
- Keep the shared output package writer because its timestamp, hostname, and random-token naming avoids normal package collisions.
- Test external-engine throttling under eight replicas and tune SearXNG engine/backoff settings if needed.

Conclusion: eight independent containers are a sound architecture for bursty agent research, and the current service definition supports Compose scaling. The critical blocker is proving load distribution and restart discovery. Until the stronger UAT passes, “eight replicas running” must not be treated as “eight replicas serving.”

#### Cross-repository latest-branch updates

Updating both repositories to their latest configured branches is operationally simple, but it is not transactional. CapyHome can update successfully while WebSearch fetch/build fails, or the two latest commits can be temporarily incompatible.

Mitigations:

- Fetch and validate both repositories before fast-forwarding either.
- Record old and target SHAs for both repositories.
- Reject dirty worktrees and non-fast-forward histories.
- Run build, health, MCP preview, and eight-replica distribution gates after every update.
- Show both installed commit SHAs in Settings and diagnostics.
- Keep the updater alive outside the application restart boundary.

#### Configuration and secret exposure

Current configuration APIs can return or persist sensitive endpoint values. Setup diagnostics and logs may accidentally include tokens, environment variables, filesystem paths, or model credentials.

Mitigations:

- Return redacted secret fields from every GET endpoint.
- Store secret references separately from public configuration.
- Apply `0600` permissions to local secret files.
- Use structured log redaction and test it with representative failures.
- Make **Copy diagnostics** exclude secrets by construction.

#### Networking and MCP readiness

Container DNS solves host-address differences, but MCP availability still depends on shared network membership, correct startup ordering, health semantics, and cache invalidation. A healthy HTTP endpoint does not guarantee that MCP tools are usable.

Mitigations:

- Put Gateway, LangGraph, and WebSearch on one explicit private network.
- Require both `/health` success and MCP tool preview success.
- Keep the MCP entry disabled until verification completes.
- Reconcile MCP state after every restart and update.
- Define whether in-flight agent runs survive WebSearch restart.

### 10.4 UX pitfalls

- Calling the action **Install Web Search** is misleading when it only pulls containers; **Enable Web Search** is clearer.
- “Download Docker” must not imply automatic installation.
- Requiring WebSearch before the first chat would make optional infrastructure block the core product.
- Automatically reopening Setup on every launch can become irritating; show a dismissible banner after the first guided session.
- Too many technical states can overwhelm new users. Present one recommended action, with details under diagnostics.
- A successful container start is not sufficient. The UI should say ready only after health, MCP preview, and a small search smoke test pass.
- Destructive cleanup must separate containers, cached images, and user data.

### 10.5 Recommended staged architecture

#### Target architecture

- Installer plus `make start`, `make doctor`, and `make repair`.
- CapyHome-owned production and WebSearch Compose files.
- Managed CapyHome and WebSearch Git checkouts.
- WebSearch enabled with eight replicas by default.
- Settings shows status, diagnostics, MCP verification, and **Update All**.
- A separate, minimal bootstrap updater performs Git, build, and restart operations.
- Strong local authentication, CSRF/origin defenses, durable jobs, and self-restart recovery are required.
- Security review and clean-machine end-to-end tests gate release.

Implement this target through the delivery phases below. The Settings update button should not ship before the detached updater and restart-recovery behavior are complete.

#### Later optimization

- Adaptive worker scaling.
- Native host helper only if launching Docker Desktop or deeper OS integration is still valuable.

Database migration and data rollback risk are intentionally out of scope for this version of the assessment.

### 10.6 Success and failure metrics

Measure:

- Median time from download to healthy UI.
- Median time to healthy WebSearch.
- Percentage completing setup without documentation.
- Failure rate by phase and OS/architecture.
- Download size, disk use, memory use, and idle CPU.
- Repair success rate.
- MCP verification and first-search success rate.
- Percentage of users who disable WebSearch after enabling it.

Release gates:

- At least 90% clean-machine success on the supported platform matrix.
- No setup endpoint capable of executing unapproved commands.
- No unredacted secrets in API responses, logs, or diagnostics.
- Recovery works when the initial operation is interrupted.
- Core CapyHome remains usable when WebSearch is disabled or unhealthy.

Failure triggers:

- Repeated host resource exhaustion.
- Setup-related security finding.
- In-app Docker control produces inconsistent or self-interrupted deployments.

## 11. Delivery Sequence

### Phase 1: Reliable CapyHome bootstrap

- Safe default config.
- Idempotent bootstrap script.
- Production Compose stack.
- One `make start` command.
- Health wait and clear diagnostics.

### Phase 2: Setup status page

- Settings > Setup.
- Docker, CapyHome, and LLM status.
- First-run completion logic.

### Phase 3: Bundled WebSearch via host lifecycle

- Managed WebSearch checkout.
- CapyHome-owned WebSearch Compose deployment.
- Eight-replica default and persisted scale setting.
- Correct nginx load distribution and restart discovery.
- `make websearch-enable`, `make websearch-enable-podman`, `make websearch-disable`, and `make doctor`.
- Health verification.
- Atomic MCP upsert and tool preview.
- Settings status, diagnostics, and copyable recovery commands.

### Phase 4: Release distribution

- Signed/versioned installer artifacts.
- Release landing page and QR code.
- Clean-machine CI/VM tests.
- User and contributor documentation split.

### Phase 5: Update All lifecycle control

- Separate bootstrap/setup-agent project.
- Dual-repository fast-forward update and durable job runner.
- Authentication, CSRF/origin defenses, command allow-list, and remote-deployment lockout.
- Enable/disable/repair/**Update All** controls in Settings.
- Dedicated security review and interrupted-update tests.

### Phase 6: Hardening and cleanup

- Secret redaction/storage improvements.
- Remove deprecated local-stack code and stale docs.
- Add Windows installer if Windows is a supported target.

## 12. Definition of Done

- A clean machine with Docker can install and start CapyHome with one terminal command.
- Startup is idempotent and never overwrites user config.
- The initial config contains no secret, personal path, private IP, or machine-specific model.
- The UI is usable before an LLM is configured and guides the user to add one.
- WebSearch can be enabled, repaired, updated, and disabled through the supported host lifecycle; Settings provides status and diagnostics.
- Successful WebSearch setup automatically creates, enables, health-checks, and previews its MCP server.
- Direct Settings control of Docker is considered complete only after the Phase 5 security and recovery gates pass.
- The old local research stack is no longer advertised.
- Backend tests, frontend lint, frontend typecheck, and clean-machine smoke tests pass.
