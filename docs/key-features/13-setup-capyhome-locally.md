# Build a Private AI Research Workspace on Your Own Laptop

> **LinkedIn hook (use as the post's first line):** "The hardest part of local AI should not be connecting five half-documented tools. Here is the complete path from a blank laptop to a private deep-research workspace."
> **Audience:** LinkedIn -> Medium. Builders, researchers, self-hosters, and privacy-conscious users who want CapyHome, a local model, and WebSearch running together.

---

One broken dependency. That is all it takes.

The model is only the first of five things a research agent needs. Add an interface, a runtime, web retrieval, persistent storage, and reliable service discovery — and every component has to find the others. Miss any one link and "local-first" stops meaning ownership and starts meaning a weekend infrastructure project you never agreed to.

CapyHome's setup is built around a simpler mental model:

- **CapyHome is the research workspace and agent harness.**
- **LM Studio or Ollama supplies the model.**
- **WebSearch supplies private search and page extraction.**
- **Docker gives the services a repeatable runtime.**

Once those four pieces are healthy, the user should be thinking about research questions, not ports.

> **[Generate: Hero illustration using the character from `asset/CapyHome/capybara-logo.webp` as the base. A cute cartoon capybara sits at the centre holding a laptop. Four rounded-corner illustrated cards float around the capybara connected by arrows pointing inward toward the laptop: top-left "CapyHome" (capybara icon), top-right "WebSearch" (magnifying glass icon), bottom-left "LM Studio / Ollama" (llama icon), bottom-right "Docker" (whale icon). Warm cream background, subtle leaf accents. Fully illustrated — no screenshot.]**

## What you are installing

CapyHome is not a single chatbot process. The local product has a frontend, an agent runtime, a gateway API, and sandboxed tools. Nginx exposes them through one address:

```text
http://localhost:2026
```

WebSearch remains a separate repository because it has a separate responsibility: metasearch, crawling, extraction, and an MCP interface. Keeping the source repositories separate makes ownership and upgrades clearer. The installer still manages them as one local product.

That boundary matters. "Integrated" does not have to mean "everything copied into one codebase." It can mean that one setup flow starts, checks, updates, and connects independently maintained systems.

## Step 1: install the foundations

Install and start:

1. **Git**, to clone and update the repositories.
2. **Docker Desktop**, including Docker Compose.
3. **LM Studio or Ollama**, unless you intend to use a cloud model.

For a local model, download one that supports tool use reliably and fits your available memory. Bigger is not automatically better if it leaves no RAM or VRAM for crawling and containers. A responsive model with dependable structured output is usually more useful to an agent than a larger model that constantly stalls.

In LM Studio, start the local server. In Ollama, make sure the service is running and the selected model has been pulled. CapyHome can configure either from **Settings -> Setup**.

## Step 2: clone CapyHome and WebSearch

The recommended release installer clones both repositories, creates local configuration, and starts CapyHome:

```bash
bash ~/Downloads/install-capyhome.sh
```

For a transparent manual checkout, keep both repositories as sibling folders:

```bash
cd ~/Desktop
git clone https://github.com/yilongchua/CapyHome.git
git clone https://github.com/yilongchua/websearch.git
cd CapyHome
make doctor
make local-prod
```

`make doctor` checks Docker, configuration, and the expected sibling WebSearch checkout before starting anything. This is intentionally boring. A setup command should fail early with a useful explanation, not leave seven containers running in a mysterious half-state.

> **[Generate: Illustration using the character from `asset/CapyHome/capybara-logo.webp` as the base. A cute cartoon capybara sits at a laptop, pumping a fist in celebration. The illustrated laptop screen shows a dark terminal window with three sections: two sibling folder names listed at the top ("CapyHome/" and "websearch/"), then a series of green checkmark lines for `make doctor` checks (e.g. "✓ Docker running", "✓ WebSearch found", "✓ Config valid"), and a final bold green line: "✓ CapyHome is starting at http://localhost:2026". Warm cream background, fully illustrated.]**

## Step 3: connect the model

Open `http://localhost:2026`, then go to **Settings -> Setup**.

Choose **LM Studio**, **Ollama**, or another provider, enter the endpoint and model details, and run the connection test. CapyHome synthesizes an OpenAI-compatible model entry from the endpoint, so local providers fit the same agent architecture as hosted ones.

The important product idea is choice. Planning, research, and synthesis are different workloads. You can start with one local model, then later mix models by task instead of rebuilding the system around a provider.

## Step 4: enable WebSearch

From **Settings -> Setup**, enable WebSearch with Docker. The supported command-line equivalent is:

```bash
make websearch-enable
```

CapyHome builds the sibling WebSearch checkout, starts its search stack, health-checks it, and enables the `websearch` MCP server. The default local-production topology can run multiple WebSearch replicas because deep research often launches independent searches in parallel.

Verify three things:

- CapyHome reports healthy at `http://localhost:2026`.
- At least one LLM provider passes its connection test.
- WebSearch is enabled and its MCP preview succeeds.

Then ask:

> Research the current landscape of local-first AI research tools. Compare their privacy model, retrieval approach, and support for persistent knowledge. Cite every major claim.

If the activity timeline shows searches and sources returning, the full loop is alive.

## Why this architecture is worth the setup

The benefit is not merely "it runs locally." It is **replaceability**.

You can change the model without replacing the workspace. You can improve WebSearch without rewriting the agent. You can update the interface without moving your knowledge. Each component has a clear job, and the boundaries let the system evolve.

Local ownership also changes the economics of experimentation. You can inspect the files, preserve research artifacts, tune the model, and decide exactly which services may leave the machine. The result is less polished magic and more durable capability.

## Common failure modes

- **Docker is installed but not running.** Start Docker Desktop, then rerun `make doctor`.
- **The repositories are not siblings.** Set `WEBSEARCH_ROOT` explicitly or move them under the same parent directory.
- **The model endpoint is healthy but weak at tool calls.** Try a stronger tool-capable model before debugging the entire stack.
- **The laptop is overloaded.** Reduce WebSearch replicas and use a smaller quantized model.
- **Port 2026 is occupied.** Stop the conflicting process or the previous CapyHome stack before restarting.

## Video script (45-60 seconds, vertical Short)

> **[0:00-0:05] Hook:** Show a blank desktop. "Let's turn this laptop into a private AI research workspace."
>
> **[0:05-0:15] Foundations:** Fast cuts: Docker running, then LM Studio or Ollama serving a model. Caption: "Containers + local brain."
>
> **[0:15-0:27] Install:** Show CapyHome and WebSearch cloning side by side, then jump to a successful `make doctor` and the stack starting.
>
> **[0:27-0:38] Connect:** Open Settings -> Setup, test the model, and enable WebSearch. Use green health indicators as the visual payoff.
>
> **[0:38-0:53] Proof:** Submit one cited research question and flash through parallel searches, extracted sources, and the final answer.
>
> **[0:53-0:60] Close:** Show `http://localhost:2026`. "Your model, your search stack, your research loop. All on your machine."

---

*Next: [CapyHome Is a Research Harness, Not Another Chatbot ->](./14-capyhome-deep-research-harness.md).*
