# CapyHome Key Features — The Article Series

> A calm super-agent that actually gets things done. Give it a goal; it plans, delegates to a team of Baby Capys, runs real tools in a real sandbox, and hands you the finished work.

This folder is a series of **LinkedIn → Medium** articles, one per key feature. Each is written so the **H1 headline doubles as the LinkedIn hook line** — the first line of the LinkedIn post that drives readers to the full Medium article.

## Reading order

| # | Article | One-liner |
|---|---------|-----------|
| 01 | [The Knowledge Vault](./01-knowledge-vault.md) | A memory organized around *entities* and *concepts*, not chat logs. |
| 02 | [Hybrid Vault Search](./02-hybrid-vault-search.md) | Keyword + semantic search, fused — recall without exact words. |
| 03 | [Plan Mode & Work Mode](./03-plan-and-work-mode.md) | Think first, then execute — Planner → Generator → Evaluator over a todo DAG. |
| 04 | [Auto Mode](./04-auto-mode.md) | The "I trust you, go" switch — bypass approvals, keep the audit trail. |
| 05 | [Mount a Folder, Drive with Slash Commands](./05-slash-commands-mount-analyse.md) | Point CapyHome at your real files; `/mount`, `/analyse`. |
| 06 | [Web Search That Writes Markdown](./06-websearch-markdown.md) | Every search → markdown → a self-pruning cache of the web. |
| 07 | [The Autoresearch Loop](./07-autoresearch-loop.md) | Self-driving research with a question ledger that knows when to stop. |
| 08 | [Baby Capy Sub-agents](./08-baby-capy-subagents.md) | One lead, many workers — parallel sub-agents, isolated context. |
| 09 | [Skills](./09-skills.md) | Capability modules that load only when the task needs them. |
| 10 | [Local-First & Bring Your Own Brain](./10-local-first-byob.md) | Any model, fully local if you want — data never has to leave. |
| 11 | [The Browser Clipper](./11-browser-clipper.md) | A browser extension that feeds the vault — one click or automatic. |
| 12 | [Persistent Memory](./12-persistent-memory.md) | An assistant that remembers *you* across every session. |
| 13 | [Set Up CapyHome Locally](./13-setup-capyhome-locally.md) | Docker + LM Studio/Ollama + CapyHome + WebSearch, from zero to a working research stack. |
| 14 | [CapyHome as a Deep-Research Harness](./14-capyhome-deep-research-harness.md) | Why the product is an agent workspace, not another chatbot. |
| 15 | [The Knowledge Vault as a Research Cache](./15-knowledge-vault-research-cache.md) | Reuse evidence instead of repeatedly crawling the same sources. |
| 16 | [Plan, Work, and Auto Modes](./16-plan-work-auto-modes.md) | Choose control based on the task's ambiguity, cost, and trust. |
| 17 | [Autoresearch + Browser Clipper](./17-autoresearch-browser-clipper-loop.md) | Agent searches, human reading, and gap-filling feed one evidence base. |
| 18 | [Subagents + Slash Commands + Mounted Files](./18-subagents-slash-commands-mounted-files.md) | Parallel research grounded in the real folders on your machine. |

---

## 📋 Production guide (read before publishing)

Each article follows the same structure so the series is consistent and easy to film/illustrate:

1. **H1 = LinkedIn hook.** The title line is written to be the scroll-stopping first line of the LinkedIn post. A `> LinkedIn hook` blockquote restates it verbatim plus the target audience.
2. **2–4 flow diagrams**, pre-rendered to **PNG** in the warm "friendly-tech" capybara palette and stored in [`diagrams/`](./diagrams/). They're drop-in ready for Medium (just upload the PNG). Each diagram's Mermaid source lives in [`diagrams/src/`](./diagrams/src/) and the whole set is regenerable with `python3 diagrams/_build/render.py` — see [`diagrams/README.md`](./diagrams/README.md).
3. **"Under the hood: how it's built"** — the technical/implementation section, grounded in the real code (file paths, prompts, tunables).
4. **"What we considered"** — the design trade-offs, for credibility with a technical audience.
5. **Image markers** — every spot that wants a screenshot/graphic is tagged. Find them all with:
   ```bash
   grep -rn "User add" docs/key-features
   ```
6. **🎬 Video script** — a timed, scene-by-scene shooting script for a short screen-recording per feature.

### Screenshots
- A captured landing-page screenshot lives in [`_screenshots/landing.png`](./_screenshots/landing.png) (1440×900, grabbed headlessly from the running app).
- For deeper UI shots (vault explorer, plan phases, memory panel, the clipper popup), the `[User add: …]` markers describe exactly what to capture. Start the app (`make docker-start` → http://localhost:2026) and grab them, or reuse existing assets in [`asset/CapyHome/`](../../asset/CapyHome/) (Work_Mode.png, plan_mode-chat.png, Main_Landing.png, etc.).

### All 18 LinkedIn hooks (copy/paste for scheduling)

| # | Hook line |
|---|-----------|
| 01 | Your AI forgets everything the moment you close the tab. We built a memory that *compounds* instead. |
| 02 | A knowledge base you can't search is just a graveyard. We fused keyword + semantic search so recall doesn't depend on exact words. |
| 03 | The difference between a junior and a senior isn't speed — it's that the senior pauses, maps the work, then starts. We taught our agent that. |
| 04 | Planning gives you control. Sometimes you want to hand off a goal, close the laptop, and come back to finished work. Here's the switch that makes that safe. |
| 05 | Cloud AI tools make you upload files one at a time. We flipped it: point the AI at a real folder — it never touches your files until you say so. |
| 06 | Most agent web search is a black box. Ours writes clean markdown every time, then files it into a self-pruning library. |
| 07 | What if you could point an AI at a topic and just… let it learn — and it knew when to stop? |
| 08 | Most "AI agents" do one thing at a time. We run it like a team: one lead, a pack of parallel sub-agents, each in its own clean room. |
| 09 | The secret to a *more* capable AI agent is giving it *fewer* instructions at a time. |
| 10 | Two questions decide if you can trust an AI with real work: whose model is this, and where does my data go? Our answer: yours, and nowhere you don't choose. |
| 11 | The best knowledge base is the one that fills itself. I stopped bookmarking — my browser clips what I read into my AI's memory automatically. |
| 12 | Every fresh chat with a normal assistant starts from amnesia. We built memory that learns *you* and carries it across every session. |
| 13 | The hardest part of local AI should not be connecting five half-documented tools. Here is the complete path from a blank laptop to a private deep-research workspace. |
| 14 | A chatbot gives you an answer. A research harness gives an AI a place to plan, search, delegate, use files, preserve evidence, and finish the work. |
| 15 | AI research feels fast until you notice it keeps paying to rediscover the same articles. We built a cache for knowledge, not just web pages. |
| 16 | Simple tasks need momentum. Complex research needs a plan. Trusted workflows need autonomy. One AI mode should not pretend those are the same problem. |
| 17 | The agent searches one part of the web. You read another. A useful knowledge system should learn from both, then investigate the gaps neither of you noticed. |
| 18 | Parallel AI agents are a demo until they can inspect the same project, divide the work cleanly, and return something you can actually use. |

---

## The 60-second version

CapyHome is an open-source **super-agent harness**. A lead agent breaks a goal into a plan, spins up parallel sub-agents to work in isolation, runs code and tools inside a sandboxed filesystem, remembers what matters across sessions, and learns new tricks through composable skills — all behind a cozy Next.js workspace where you can watch it think.

It is **not** a coding-only tool. People use it to build slide decks, model spreadsheets, research a legal question, plan a trip, draft a podcast, analyse a dataset, or clear the week's admin.

```bash
git clone https://github.com/CapyHome/CapyHome.git
cd CapyHome
make config        # then add one model + one API key
make docker-start  # → http://localhost:2026
```

⭐ If the series is useful, star the repo — it's the single biggest thing that helps other people find the project.
