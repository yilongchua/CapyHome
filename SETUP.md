# CapyHome Setup

## Local Production

Prerequisites:

- Docker Desktop
- Git
- Python 3

Download the CapyHome installer and run one command:

```bash
bash ~/Downloads/install-capyhome.sh
```

The installer:

1. Creates managed CapyHome and WebSearch checkouts on the Desktop.
2. Creates missing local configuration without overwriting existing files.
3. Starts CapyHome through Docker Compose.
4. Starts the local setup daemon used by **Settings > Setup**.

Open **http://localhost:2026**.

Use **Settings > Setup** to configure an LLM, check Docker and WebSearch health,
enable or repair WebSearch, and update both repositories.

WebSearch starts when you choose either:

- **Enable with Docker**
- **Enable with Podman**

Docker remains required for the CapyHome core stack. Podman is an optional
WebSearch runtime. On macOS, install Podman Desktop, allocate at least 4-6 GB to
the Podman machine, install `podman-compose` if the Compose provider is absent,
and run `podman machine start` before choosing Podman. Linux bind mounts are
configured with SELinux relabeling for rootless Podman.

The recommended default is eight WebSearch replicas.

If Docker is unavailable, setup stops without changing the running installation:

```text
Docker is not running. Start Docker Desktop and try again.
```

## Recovery

Run these commands from the CapyHome checkout:

```bash
make doctor
make local-prod
make websearch-enable
make websearch-enable-podman
make local-prod-logs
```

## Contributor Development

```bash
make check
make config
make install
make dev
```

The contributor flow remains host-native and separate from local production.
