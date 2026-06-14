# Parallel Agents Become Useful When They Can Work on Your Real Files

> **LinkedIn hook (use as the post's first line):** "Parallel AI agents are a demo until they can inspect the same project, divide the work cleanly, and return something you can actually use."
> **Audience:** LinkedIn -> Medium. Developers, consultants, researchers, and knowledge workers whose real work lives in local folders.

---

It is easy to make multiple agents produce text in parallel.

The harder problem is making that parallelism useful:

- each worker needs a clear scope;
- independent work should run concurrently;
- dependent work must wait;
- the lead needs concise results rather than every transcript;
- agents need access to the files the task is actually about;
- writes need boundaries.

CapyHome combines **Baby Capy subagents**, **slash commands**, and a mounted sandbox path to solve those coordination problems as one workflow.

## One lead, several focused workers

The lead agent decomposes a task and can launch up to three subagents concurrently. Each worker receives its own context, tools, budget, timeout, and assignment.

![Lead delegates, summaries flow back](./diagrams/08-baby-capy-subagents-d1.png)

Context isolation is not merely a scaling trick. It protects reasoning quality.

If one giant agent reads every file, every search result, and every intermediate thought, its context becomes an attic. Focused workers can inspect a narrow slice and return a useful summary. The lead preserves enough space to compare and synthesize.

This pattern fits research naturally:

- one worker maps architecture;
- one traces data flow;
- one audits tests and operational risks;
- the lead reconciles their findings.

## `/mount` connects the sandbox to real work

Type `/mount`, choose a local folder, and CapyHome exposes it inside the agent environment at:

```text
/mnt/user-data/mounted
```

The virtual path gives tools a stable location while the middleware maps access back to the selected host directory.

This is a better interaction for large projects than uploading files one by one. Folder structure is evidence. Imports, neighboring documents, configuration, and naming conventions all help an agent understand how the work fits together.

> **[User add: split screenshot with the native folder picker on the left and `/mnt/user-data/mounted` visible in the CapyHome file tree on the right.]**

## Slash commands make the safety contract memorable

The most useful commands form a staged workflow:

```text
/mount         Select the local folder
/analyse       Build markdown mirrors and analysis artifacts
/publishdocs   Write reviewed documentation back
/handoff       Package context and continue in a fresh thread
/compact       Reduce context deterministically
/new           Start a fresh conversation in the workspace
```

`/analyse` is deliberately read-only with respect to the mounted files. It creates staged artifacts such as a repository overview that you can inspect before anything is published back.

![Stage -> review -> commit](./diagrams/05-slash-commands-mount-analyse-d1.png)

The design goal is not to remove write capability. It is to make the moment of write-back explicit.

## An end-to-end example

Imagine inheriting an unfamiliar repository and needing a technical due-diligence report.

1. Run `/mount` and select the repository.
2. Run `/analyse` to produce a deterministic markdown mirror and architecture overview.
3. Enter Plan Mode and ask for an assessment of architecture, security boundaries, test coverage, deployment risk, and maintainability.
4. Approve the todo graph.
5. Watch independent Baby Capys inspect separate concerns in parallel.
6. Review the synthesized report and its source references.
7. Run `/publishdocs` only when the documentation is ready to return to the mounted folder.

This is where parallel agents stop being theatre. They are operating over a shared project map, with distinct assignments and an explicit publishing boundary.

## Why summaries flow back instead of transcripts

Raw worker transcripts feel transparent, but they are a poor coordination format.

The lead needs:

- what the worker examined;
- what it concluded;
- supporting file paths or sources;
- uncertainty and unresolved questions;
- the artifact it produced.

A concise result contract reduces token use and makes synthesis more reliable. The activity timeline can still show which worker ran and what tools it used, while the lead receives the information needed for the next decision.

## Why cap parallelism

More agents are not automatically faster.

Every additional worker increases model load, search concurrency, rate-limit pressure, and the chance of duplicated effort. On a local machine, it also competes with Docker, the browser, and the local model for memory.

A small concurrency limit encourages meaningful decomposition. Three well-scoped workers are often better than ten workers all discovering the same README.

The todo dependency graph supplies the other half of the answer: parallelize only tasks that are actually ready, then synthesize after their prerequisites complete.

## The impact: local files become a collaborative workspace

Mounted folders change the agent from a detached answer generator into a collaborator that can understand the materials already surrounding the task.

Subagents make that understanding faster without forcing one context window to carry everything. Slash commands make the workflow legible. The sandbox and publish step keep the user's control visible.

Together, these features support a strong division of labor:

- the user chooses the folder and defines the goal;
- the planner defines the work;
- subagents investigate in parallel;
- the lead integrates the evidence;
- the user decides when results become part of the real project.

## Video script (45-60 seconds, vertical Short)

> **[0:00-0:06] Hook:** "Multiple agents writing paragraphs is not teamwork. Give them a real project."
>
> **[0:06-0:17] Mount:** Type `/mount`, choose a repository, and show it appearing at `/mnt/user-data/mounted`.
>
> **[0:17-0:29] Analyse:** Run `/analyse` and open the generated repository overview. Caption: "Read-only staging."
>
> **[0:29-0:45] Parallel work:** Submit a due-diligence task and show three Baby Capys inspecting architecture, security, and tests simultaneously.
>
> **[0:45-0:54] Control:** Open the final report, then run `/publishdocs` to write back deliberately.
>
> **[0:54-0:60] Close:** "Real files, parallel research, and you control when anything changes."

---

*Back to the [series index](./00-index.md).*
