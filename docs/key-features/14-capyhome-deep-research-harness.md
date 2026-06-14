# CapyHome Is a Research Harness, Not Another Chatbot

> **LinkedIn hook (use as the post's first line):** "A chatbot gives you an answer. A research harness gives an AI a place to plan, search, delegate, use files, preserve evidence, and finish the work."
> **Audience:** LinkedIn -> Medium. Researchers, analysts, founders, and builders evaluating agentic deep-research systems.

---

The easiest way to misunderstand CapyHome is to call it a chatbot.

A chatbot is a conversation wrapped around a model. CapyHome is a **deep-research harness**: an environment that turns a model into a working system by giving it plans, tools, subagents, a sandbox, persistent knowledge, and observable execution.

The distinction matters because research quality is rarely limited by whether a model can write a fluent paragraph. It is limited by process:

- Did it break the question into the right parts?
- Did it search beyond the first plausible answer?
- Did it read the sources rather than repeat snippets?
- Did it compare conflicting evidence?
- Did it preserve useful findings for the next question?
- Can you inspect what it did?

CapyHome is built around those questions.

> **[Generate: Hero split-panel illustration using the character from `asset/CapyHome/capybara-logo.webp` as the base. Left panel labelled "Chatbot": a simple cartoon capybara at a laptop — one arrow from a speech bubble "Prompt" to a small card "Answer." Right panel labelled "Research Harness": the same capybara at a laptop orchestrating a richer illustrated flow — "Goal" → "Plan" → three parallel arrows each pointing to a small baby capybara (one with a magnifying glass, one with books, one with a notepad) → arrows converge at "Evidence" → "Synthesis" → "Vault 📚." Warm cream background, clear contrast between left and right panels.]**

## The model is the reasoner, not the whole product

Modern models are capable, but they are temporary. Their context fills up, their tool calls fail, and a fresh thread forgets yesterday's work.

A harness adds the missing operational layer:

1. **A lead agent** interprets the goal.
2. **Plan Mode** converts ambiguity into an editable research approach.
3. **Baby Capy subagents** investigate independent questions in parallel.
4. **WebSearch** finds and extracts source content.
5. **The sandbox** gives agents a filesystem and executable tools.
6. **The Knowledge Vault** preserves useful evidence across sessions.
7. **The activity timeline** makes the process inspectable.

This is why CapyHome can work on a market landscape, a technical architecture review, a literature scan, a slide deck, or a folder of internal documents. The model changes; the working environment remains.

## Why WebSearch is a separate capability

Web search is often treated as a checkbox: the model receives a few snippets and adds citations. Deep research needs more.

CapyHome connects to the separate open-source [WebSearch repository](https://github.com/yilongchua/websearch), which combines metasearch with page fetching and content extraction. It exposes the capability through MCP, so CapyHome can use it as a tool without absorbing the search implementation into the agent codebase.

The practical difference is that the agent can reason over extracted article content, not just titles and snippets. Search results can also be written as markdown and queued into the Knowledge Vault.

This gives you web-search capability without paying per query to a commercial search API. "Free" still has real costs - your machine, bandwidth, and electricity - but it removes a metered vendor from the critical path and keeps the stack under your control.

## A useful way to ask for deep research

Weak prompt:

> Tell me about solid-state batteries.

Research goal:

> Assess whether solid-state batteries are likely to reach mass-market passenger vehicles by 2030. Separate technical readiness, manufacturing economics, announced capacity, and demonstrated production. Identify disagreements between sources, cite major claims, and finish with the three uncertainties that matter most.

The second prompt creates an evidence structure. It tells the harness what decision the research should support, which dimensions must be investigated, and what uncertainty should remain visible.

The goal is not to make the prompt enormous. It is to define the decision behind the question.

## What happens after you press Enter

For a complex question, CapyHome can first produce `plan.md`: objective, assumptions, risks, acceptance criteria, and a dependency-aware todo graph.

Independent tasks become parallel research:

- one subagent examines technical milestones;
- one examines manufacturing yields and cost;
- one checks company announcements against demonstrated output.

The lead agent receives focused summaries, reconciles the evidence, and produces the final artifact. Meanwhile, eligible sources enter the vault pipeline so a later question about a company or battery chemistry does not begin from zero.

That last part changes the value curve. A normal search assistant is useful per session. A research harness becomes more useful as its workspace accumulates evidence.

## The impact: research becomes an asset

The immediate benefit is speed, especially when a question naturally fans out.

The deeper benefit is **continuity**. Sources become files. Questions become plans. useful evidence becomes vault pages. Internal folders can be mounted into the same workspace. A research session leaves behind materials another session can use.

This is closer to how a good analyst works. The final report matters, but so do the source notes, the unresolved questions, the comparison framework, and the ability to explain how the conclusion was reached.

## What CapyHome does not solve automatically

A harness does not make every answer correct.

Source quality still matters. Local models vary widely in tool use and synthesis. Search can miss paywalled or poorly indexed material. Parallel agents can independently repeat the same mistake. High-stakes conclusions still need human review.

The point of the harness is not to remove judgment. It is to make the work more systematic, observable, and reusable so judgment has better material to operate on.

## Video script (45-60 seconds, vertical Short)

> **[0:00-0:06] Hook:** "This looks like a chatbot, but the chat box is the least interesting part."
>
> **[0:06-0:16] Harness:** Rapidly highlight Plan Mode, tools, sandbox, WebSearch, subagents, and the Knowledge Vault. Caption: "A model needs a working environment."
>
> **[0:16-0:29] Goal:** Submit one decision-focused research question and show `plan.md` appearing.
>
> **[0:29-0:44] Work:** Show several Baby Capys researching separate dimensions while the activity timeline updates.
>
> **[0:44-0:54] Result:** Open the cited report, then the source markdown and new vault pages.
>
> **[0:54-0:60] Close:** "A chatbot gives an answer. A research harness builds the next answer too."

---

*Next: [The Knowledge Vault Turns Research Into a Cache ->](./15-knowledge-vault-research-cache.md).*
