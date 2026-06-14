# The Most Expensive Research Is the Research You Already Did

> **LinkedIn hook (use as the post's first line):** "AI research feels fast until you notice it keeps paying to rediscover the same articles. We built a cache for knowledge, not just web pages."
> **Audience:** LinkedIn -> Medium. Researchers, RAG engineers, analysts, and teams that repeatedly revisit the same domains.

---

Imagine an analyst who throws away every source note after submitting a report.

They might still produce good work, but every follow-up begins with the same searches, the same reading, and the same effort to reconstruct context. That is how most AI research products behave by default.

CapyHome's **Knowledge Vault** starts from a different premise: useful research should become infrastructure for future research.

It acts like a cache, but not a conventional browser cache. It does not merely preserve a copy of a URL. It turns source material into searchable pages organized around **entities** and **concepts**, with summaries, claims, references, open questions, and links back to evidence.

![How a single source becomes structured knowledge](./diagrams/01-knowledge-vault-d1.png)

## Why "save every page" is not enough

A folder full of downloaded articles solves disappearance, but not retrieval.

Three months later, you may remember the idea but not the title. You may ask about "organizational autonomy" when the article used "decentralized decision-making." You may want everything about a company spread across twenty sources.

The vault therefore creates two durable views:

- **Entity pages** collect knowledge about named things such as companies, products, people, and places.
- **Concept pages** collect recurring ideas, mechanisms, frameworks, and themes.

Those pages compound across research sessions. A source about an electric-vehicle manufacturer can strengthen both the company page and concept pages for battery supply chains, vertical integration, or manufacturing yield.

The thought process is simple: organize knowledge around what future questions will refer to, not around the chat that happened to discover it.

## Search once, reuse many times

When WebSearch returns an eligible result with extracted content, CapyHome can queue it for vault ingestion. Duplicate URLs and content hashes are filtered, weak sources can be rejected by a trust threshold, and ingestion creates the compiled pages used by later vault searches.

The next time an agent receives a related question, it can search the vault before reaching for the open web.

That produces several kinds of leverage:

- **Lower latency:** local retrieval is faster than crawling the same page.
- **Lower model cost:** previous extraction and organization do not need to be repeated.
- **Greater consistency:** follow-up answers can build on the same evidence base.
- **Resilience:** useful content remains available if the page changes or disappears.
- **Cumulative depth:** every serious project improves the starting point of the next one.

![Why it is a cache for in-depth analysis](./diagrams/06-websearch-markdown-d3.png)

## A concrete example

Suppose you research whether a software company can maintain growth while improving margins.

The first run gathers earnings reports, executive interviews, pricing pages, analyst commentary, and product documentation. The vault extracts the company as an entity and concepts such as operating leverage, customer concentration, and usage-based pricing.

Two weeks later, you ask:

> How does this company's pricing model compare with its closest competitor, and what does that imply for margin expansion?

A stateless agent starts again. CapyHome can retrieve the existing company and pricing pages, identify what is already known, and use live WebSearch only for missing or time-sensitive evidence.

That is the real benefit of caching: not "never search again," but **search selectively because you know what you already have**.

## Hybrid retrieval protects both precision and recall

The vault combines keyword matching with semantic retrieval when embeddings are configured.

Keyword search is excellent for exact names, product codes, quotations, and financial terms. Semantic search helps when the new question expresses an old idea with different words. Fusing both avoids a common RAG failure: choosing between exactness and meaning when a good research system needs both.

The index remains local and file-backed. The compiled knowledge remains readable markdown rather than being trapped inside a remote vector database.

## Why the vault also needs pruning

Compounding only works if the accumulated material remains useful.

An ingestion system that accepts every thin page, duplicate, navigation fragment, and weak source eventually creates a larger search problem. CapyHome therefore includes trust gates, deduplication, linting, aliasing, and pruning.

Deletion sounds counterintuitive in a knowledge system, but curation is part of memory. Human researchers do not preserve every search result with equal weight. A useful vault must be able to say, "This adds no durable value."

Dry-run controls make that judgment inspectable before destructive cleanup.

## The impact on deep research

The vault changes deep research from a sequence of isolated jobs into a program of work.

Day one establishes the core entities and concepts. Later questions expose gaps. Autoresearch fills selected gaps. Browser clips add human-curated material. New web searches update time-sensitive claims. The evidence base grows in the shape of your interests.

Over time, the system spends less effort rediscovering foundations and more effort investigating what is genuinely new.

That is what "memory that compounds" should mean: not remembering the conversation, but preserving the work.

## Video script (40-55 seconds, vertical Short)

> **[0:00-0:06] Hook:** Show the same prompt in two fresh chatbot windows. "Why does AI keep researching the same articles twice?"
>
> **[0:06-0:19] First task:** Show CapyHome searching once, then creating entity and concept pages in the vault.
>
> **[0:19-0:34] Follow-up:** Start a new thread and ask a related question. Highlight vault retrieval before live web search.
>
> **[0:34-0:47] Impact:** Split screen: "Search everything again" versus "Reuse what we know, search only the gaps."
>
> **[0:47-0:55] Close:** "The cheapest research is the research you already did and kept."

---

*Next: [Plan, Work, or Auto: Choosing the Right Level of Control ->](./16-plan-work-auto-modes.md).*
