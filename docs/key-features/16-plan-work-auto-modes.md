# The Best AI Mode Depends on How Expensive It Is to Be Wrong

> **LinkedIn hook (use as the post's first line):** "Simple tasks need momentum. Complex research needs a plan. Trusted workflows need autonomy. One AI mode should not pretend those are the same problem."
> **Audience:** LinkedIn -> Medium. Researchers, operators, and agent builders deciding how much control to keep during execution.

---

Most AI interfaces offer one interaction pattern: type a request and hope the system chooses the right amount of thinking.

That is convenient, but it hides an important decision. The cost of a wrong turn is not constant.

Drafting five title ideas is cheap to redo. A multi-hour market analysis can waste dozens of searches if the framing is wrong. An overnight research run should not pause at 2:00 a.m. to ask whether it may choose the option it already recommends.

CapyHome makes that decision visible through **Work Mode**, **Plan Mode**, and the **Auto Mode** modifier.

## Work Mode: when the path is already clear

Use Work Mode for direct tasks:

- summarize this folder;
- turn these notes into a table;
- research three named competitors;
- fix a specific error;
- produce a report from an agreed outline.

The agent receives its full toolset and can delegate parallel work immediately. There is little value in generating a planning ceremony when the requested output and path are already concrete.

The benefit is momentum. The user expresses intent once and watches execution begin.

## Plan Mode: when framing is the real work

Use Plan Mode when the question is broad, consequential, or underspecified:

- Which market should we enter?
- Is this technology ready for production?
- What caused a company's performance to diverge from competitors?
- How should we redesign this system?

Before execution, CapyHome creates an editable `plan.md` containing the objective, assumptions, constraints, risks, acceptance criteria, and a todo graph.

![The Planner -> Generator -> Evaluator loop](./diagrams/03-plan-and-work-mode-d1.png)

The todo graph matters because research is not a flat checklist. Some questions can run in parallel; others depend on earlier findings. A dependency-aware plan gives the system a way to move quickly without synthesizing before the evidence exists.

Planning also creates a valuable pause: you can catch a misunderstanding while it is still one paragraph in a file instead of after twenty tool calls.

## Auto Mode: when the plan is trusted but your attention is scarce

Auto Mode is not a third reasoning style. It is a modifier that removes selected waiting points from Plan Mode.

Normally, the system may wait for plan approval or clarification. With Auto Mode enabled, it can auto-approve the plan and choose a clarification's recommended option. Those decisions remain marked in the transcript.

![Where Auto Mode removes the gates](./diagrams/04-auto-mode-d1.png)

This is a narrower and more useful definition of autonomy than "let the AI do anything." The execution process remains the same. Auto Mode changes who must be present at predictable gates.

The result is a practical trust ladder:

| Situation | Choice | Why |
|---|---|---|
| Clear and reversible | Work Mode | Start immediately |
| Ambiguous or high-impact | Plan Mode | Review framing before spending effort |
| Complex but familiar | Plan + Auto | Keep structure, remove routine waiting |

## A research example in all three modes

Request:

> Compare three local LLM serving options for a 64 GB Mac.

In **Work Mode**, the agent can research the named options and produce a comparison directly. This is appropriate if you already know the dimensions that matter.

In **Plan Mode**, it may first clarify workload, model size, concurrency, Apple Silicon support, and whether ease of use or throughput matters more. You can edit the plan before execution.

In **Plan + Auto**, it can produce the same structured plan, accept recommended defaults, execute the independent research in parallel, and leave a complete audit trail for the morning.

No mode is universally superior. The right question is: **where would human attention change the outcome enough to justify waiting?**

## Why explicit modes beat invisible complexity detection

An agent can guess whether a task is complex, but complexity is not the only variable.

A short request may carry legal or financial consequences. A long request may be a routine workflow the user has run ten times. The user knows the stakes and desired involvement better than a classifier inferring them from word count.

Explicit modes make control predictable. The system does not suddenly interrupt a Work Mode task because it decided to "upgrade" the workflow. The user selects the contract.

## The wider impact: autonomy becomes adjustable

The usual debate frames AI systems as either assistants or autonomous agents.

In practice, useful systems need adjustable autonomy:

- high supervision while discovering a workflow;
- lighter supervision once the workflow is understood;
- renewed supervision when the stakes or context change.

Plan, Work, and Auto provide that progression without requiring three different products. You can earn trust task by task.

## Video script (45-60 seconds, vertical Short)

> **[0:00-0:06] Hook:** "A title brainstorm and an overnight market analysis should not use the same AI mode."
>
> **[0:06-0:18] Work Mode:** Submit one clear request. Show the agent starting immediately. Caption: "Clear task? Just work."
>
> **[0:18-0:34] Plan Mode:** Submit an ambiguous goal, open `plan.md`, and edit one assumption. Caption: "High cost of being wrong? Plan first."
>
> **[0:34-0:49] Auto Mode:** Enable Auto, show the plan approving itself, and highlight an `[Auto Mode] Selected:` decision.
>
> **[0:49-0:58] Close:** Show all three controls. "Momentum, control, or hands-off execution. You choose the level of attention."

---

*Next: [Autoresearch and the Browser Clipper Build a Living Evidence Base ->](./17-autoresearch-browser-clipper-loop.md).*
