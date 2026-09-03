"""End-to-end simulation of the memory subsystem, backend-agnostic.

Exercises the real code path -- middleware -> queue -> MemoryBackend -> storage,
then injection / recall / REST -- with only the extraction LLM stubbed.

Runs against an isolated ``CAPYBARA_HOME`` and refuses to run outside a temp
directory, so a developer's real ``memory.json`` is never touched.

Usage::

    CAPYBARA_HOME=$(mktemp -d) PYTHONPATH=. uv run python scripts/e2e_memory_simulation.py

This is the parity harness for the mem0 migration: run it under
``memory.backend: legacy``, then again under ``mem0``, and diff the results.
Section 9 deliberately asserts defect U-1 (no deduplication), which is expected
to FAIL under a mem0 backend -- that flip is the migration's success signal.
See docs/memory_migration/07-implementation-strategy.md.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

if "CAPYBARA_HOME" not in os.environ:
    raise SystemExit("Refusing to run: set CAPYBARA_HOME to a temp dir first (see module docstring).")

BASE = Path(os.environ["CAPYBARA_HOME"])
if not (str(BASE).startswith("/tmp") or str(BASE).startswith("/private/tmp") or str(BASE).startswith("/var/folders")):
    raise SystemExit(f"Refusing to run: CAPYBARA_HOME={BASE} is not a temp directory. This test destroys its home dir.")

# Start from a clean slate: a partial previous run would otherwise leave facts
# behind, and the legacy backend appends rather than dedupes (defect U-1).
if BASE.exists() and (str(BASE).startswith("/private/tmp") or str(BASE).startswith("/tmp")):
    import shutil

    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
# Setup: real config, stubbed extraction model
# --------------------------------------------------------------------------
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from src.config.memory_config import MemoryConfig, set_memory_config  # noqa: E402

set_memory_config(
    MemoryConfig(
        enabled=True,
        backend="legacy",
        debounce_seconds=1,
        max_facts=100,
        fact_confidence_threshold=0.7,
        injection_enabled=True,
        max_injection_tokens=2000,
        injection_relevance_threshold=0.25,
        recall_top_k=5,
    )
)

import src.agents.memory.updater as updater_mod  # noqa: E402
from src.agents.memory.backend import MemoryScopes, get_memory_backend  # noqa: E402
from src.agents.memory.queue import get_memory_queue, reset_memory_queue  # noqa: E402
from src.agents.middlewares.memory_middleware import MemoryMiddleware  # noqa: E402

EXTRACTION_CALLS: list[str] = []
NEXT_FACTS = []


class _FakeModel:
    """Deterministic stand-in for the extraction LLM."""

    def invoke(self, prompt: str):
        scope = "workspace" if '"scope": "workspace"' in prompt else "global"
        EXTRACTION_CALLS.append(scope)
        # Echo back removal of any fact the harness asked to drop.
        to_remove = [m for m in re.findall(r'"id": "(fact_[a-f0-9]+)"', prompt) if "DROPME" in prompt]
        payload = {
            "user": {
                "workContext": {"summary": f"Ship-broking analyst ({scope} scope).", "shouldUpdate": True},
                "personalContext": {"summary": "Bilingual EN/NL.", "shouldUpdate": True},
                "topOfMind": {"summary": "Validating vessel addresses at scale.", "shouldUpdate": True},
            },
            "history": {
                "recentMonths": {"summary": "Worked through IMO address validation.", "shouldUpdate": True},
                "earlierContext": {"summary": "", "shouldUpdate": False},
                "longTermBackground": {"summary": "Maritime domain expert.", "shouldUpdate": True},
            },
            "newFacts": NEXT_FACTS,
            "factsToRemove": to_remove,
        }
        return SimpleNamespace(content=json.dumps(payload))


updater_mod.MemoryUpdater._get_model = lambda self: _FakeModel()

THREAD = "thread-e2e-0001"


def runtime(add_to_memory: bool = True, thread_id: str = THREAD):
    return SimpleNamespace(context={"thread_id": thread_id, "add_to_memory": add_to_memory})


def conversation() -> list:
    """A realistic turn: user text, a tool round-trip, then the final answer."""
    return [
        HumanMessage(content="I'm a ship-broking analyst. Validate addresses in my IMO vessel CSV."),
        AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": "head file.csv"}, "id": "c1"}]),
        ToolMessage(content="id,company_imo,name,address", tool_call_id="c1", name="bash"),
        AIMessage(content="Found 17,842 rows. I'll validate the address column against a geocoder."),
    ]


# ==========================================================================
section("1. Write path: middleware -> queue -> backend -> disk")
# ==========================================================================
NEXT_FACTS = [
    {"content": "Works as a ship-broking analyst handling IMO vessel data", "category": "context", "confidence": 0.95},
    {"content": "Prefers Dutch-language communication", "category": "preference", "confidence": 0.9},
    {"content": "Validating 17,842 vessel addresses against a geocoder", "category": "goal", "confidence": 0.85},
    {"content": "Might possibly enjoy astronomy", "category": "preference", "confidence": 0.5},  # below gate
]

mw = MemoryMiddleware()
state = {"messages": conversation()}
mw.after_agent(state, runtime())
check("middleware queues the conversation", get_memory_queue().pending_count == 1,
      f"pending={get_memory_queue().pending_count}")

get_memory_queue().flush()

global_file = BASE / "memory.json"
ws_file = BASE / "threads" / THREAD / "memory.json"
check("global memory.json written", global_file.exists(), str(global_file))
check("workspace memory.json written", ws_file.exists(), str(ws_file))
check("extraction ran once per scope (Q-2 preserved in legacy)",
      EXTRACTION_CALLS == ["global", "workspace"], str(EXTRACTION_CALLS))

gdata = json.loads(global_file.read_text())
wdata = json.loads(ws_file.read_text())
check("global scope tagged", gdata.get("scope") == "global", gdata.get("scope"))
check("workspace scope tagged + scopeId=thread", wdata.get("scope") == "workspace" and wdata.get("scopeId") == THREAD,
      f'{wdata.get("scope")}/{wdata.get("scopeId")}')

contents = [f["content"] for f in gdata["facts"]]
check("3 facts above the 0.7 gate stored", len(gdata["facts"]) == 3, f"{len(gdata['facts'])}: {contents}")
check("0.5-confidence fact rejected by gate",
      not any("astronomy" in c for c in contents), str(contents))
check("facts carry source = thread_id", all(f["source"] == THREAD for f in gdata["facts"]))
check("narrative sections populated", bool(gdata["user"]["workContext"]["summary"]))

# ==========================================================================
section("2. SQLite index kept in sync")
# ==========================================================================
import sqlite3  # noqa: E402

db = BASE / "memory" / "memory.db"
check("index db created", db.exists(), str(db))
rows = sqlite3.connect(str(db)).execute(
    "SELECT scope, scope_id, COUNT(*) FROM memory_facts GROUP BY scope, scope_id ORDER BY scope"
).fetchall()
check("index has both scopes", len(rows) == 2, str(rows))
check("index row counts match files",
      dict((r[0], r[2]) for r in rows) == {"global": 3, "workspace": 3}, str(rows))

# ==========================================================================
section("3. Read path: relevance-filtered injection")
# ==========================================================================
from src.agents.memory.prompt import format_memory_for_injection  # noqa: E402

relevant = format_memory_for_injection(
    gdata, current_turn_text="Help me validate the vessel address column", workspace_memory_data=wdata,
    workspace_id=THREAD,
)
check("relevant turn injects a matching fact", "vessel addresses" in relevant, relevant[:160])

irrelevant = format_memory_for_injection(
    gdata, current_turn_text="What is the capital of Peru?", workspace_memory_data=wdata, workspace_id=THREAD,
)
check("unrelated turn suppresses facts", "vessel addresses" not in irrelevant, irrelevant[:160])

no_query = format_memory_for_injection(gdata, current_turn_text="", workspace_memory_data=wdata, workspace_id=THREAD)
check("query-less turn includes narrative (R-3 branch)", "User Context:" in no_query, no_query[:160])
check("query-ful turn omits narrative (R-3)", "User Context:" not in relevant)

# ==========================================================================
section("4. recall tool")
# ==========================================================================
import importlib  # noqa: E402

# `src.tools.builtins.__init__` rebinds the submodule name to the tool object,
# so reach the real module through sys.modules.
recall_mod = importlib.import_module("src.tools.builtins.recall_tool")
recall_mod = sys.modules["src.tools.builtins.recall_tool"]
recall_fn = recall_mod.recall_tool
recall_mod.get_config = lambda: {"configurable": {"thread_id": THREAD}}
out = recall_fn.invoke({"query": "vessel address validation"})
check("recall returns JSON", out.startswith("{"), out[:120])
parsed = json.loads(out)
check("recall found results", len(parsed["results"]) > 0, out[:200])
check("recall results carry documented keys",
      set(parsed["results"][0]) == {"id", "scope", "content", "category", "confidence", "score", "source"},
      str(sorted(parsed["results"][0])))
empty = recall_fn.invoke({"query": "zzzz nonexistent topic qqq"})
check("recall sentinel string preserved", empty == "No relevant memory found." or "results" in empty, empty[:80])

# ==========================================================================
section("5. Backend protocol + MemoryResponse projection (invariant I-1)")
# ==========================================================================
from src.gateway.routers.memory import MemoryResponse  # noqa: E402

backend = get_memory_backend()
for label, scopes in [("global", MemoryScopes.resolve("global")),
                      ("workspace", MemoryScopes.resolve("workspace", THREAD))]:
    prof = backend.get_profile(scopes=scopes)
    try:
        MemoryResponse(**prof)
        check(f"{label} profile validates as MemoryResponse", True)
    except Exception as e:  # noqa: BLE001
        check(f"{label} profile validates as MemoryResponse", False, str(e)[:200])

# ==========================================================================
section("6. REST API end-to-end")
# ==========================================================================
from fastapi.testclient import TestClient  # noqa: E402

from src.gateway.app import app  # noqa: E402

c = TestClient(app)

r = c.get("/api/memory", params={"scope": "global"})
check("GET /api/memory 200", r.status_code == 200, r.text[:120])
check("GET /api/memory returns facts", len(r.json()["facts"]) == 3, str(len(r.json()["facts"])))

r = c.get("/api/memory", params={"scope": "workspace", "workspace_id": THREAD})
check("GET /api/memory workspace 200", r.status_code == 200, r.text[:120])

r = c.get("/api/memory", params={"scope": "workspace"})
check("workspace without id -> 400", r.status_code == 400, f"{r.status_code}")

r = c.get("/api/memory/config")
check("GET /api/memory/config 200", r.status_code == 200)
check("decay_archive_threshold removed from config response",
      "decay_archive_threshold" not in r.json(), str(list(r.json())))
check("backend field not leaked into config response", "backend" not in r.json())

r = c.get("/api/memory/status", params={"scope": "global"})
check("GET /api/memory/status 200", r.status_code == 200, r.text[:120])

r = c.post("/api/memory/facts/fact_manual01", params={"scope": "global"},
           json={"content": "Uses DuckDB for local analytics", "category": "knowledge", "confidence": 0.95})
check("POST fact 200", r.status_code == 200, r.text[:160])
check("POST fact echoes stored fact", r.json()["id"] == "fact_manual01", r.text[:120])
check("manual fact reaches the index",
      sqlite3.connect(str(db)).execute(
          "SELECT COUNT(*) FROM memory_facts WHERE id='fact_manual01'").fetchone()[0] == 1)

r = c.delete("/api/memory/facts/fact_manual01", params={"scope": "global"})
check("DELETE fact 200", r.status_code == 200, r.text[:120])
check("deleted fact leaves the index",
      sqlite3.connect(str(db)).execute(
          "SELECT COUNT(*) FROM memory_facts WHERE id='fact_manual01'").fetchone()[0] == 0)
r = c.delete("/api/memory/facts/fact_missing", params={"scope": "global"})
check("DELETE missing fact -> 404", r.status_code == 404, str(r.status_code))

r = c.post("/api/memory/rules", params={"scope": "global"},
           json={"instruction": "Always answer in Dutch", "active": True})
check("POST rule 200", r.status_code == 200, r.text[:160])
rule_id = r.json().get("id", "")
r = c.patch(f"/api/memory/rules/{rule_id}", params={"scope": "global"}, json={"active": False})
check("PATCH rule 200", r.status_code == 200, r.text[:120])
check("PATCH rule applied", r.json()["active"] is False, r.text[:120])
r = c.patch("/api/memory/rules/rule_nope", params={"scope": "global"}, json={"active": False})
check("PATCH missing rule -> 404", r.status_code == 404, str(r.status_code))
r = c.delete(f"/api/memory/rules/{rule_id}", params={"scope": "global"})
check("DELETE rule 200", r.status_code == 200, r.text[:120])

r = c.post("/api/memory/forget-thread", params={"scope": "workspace", "workspace_id": THREAD},
           json={"thread_id": THREAD})
check("POST forget-thread 200", r.status_code == 200, r.text[:160])
check("forget-thread removed workspace facts", r.json()["removed"] == 3, r.text[:120])
check("forget-thread cleared index rows",
      sqlite3.connect(str(db)).execute(
          "SELECT COUNT(*) FROM memory_facts WHERE scope='workspace'").fetchone()[0] == 0)

r = c.post("/api/memory/clear", params={"scope": "global"})
check("POST clear 200", r.status_code == 200, r.text[:160])
check("clear emptied global facts", r.json()["memory"]["facts"] == [], r.text[:160])
check("clear emptied index",
      sqlite3.connect(str(db)).execute(
          "SELECT COUNT(*) FROM memory_facts WHERE scope='global'").fetchone()[0] == 0)

check("D-3 endpoints still present pre-Phase-5",
      c.post("/api/memory/redact", params={"scope": "global"},
             json={"reason": "test"}).status_code in (200, 400, 422, 500))

# ==========================================================================
section("7. Gates: add_to_memory=false, upload-only turns, /memory command")
# ==========================================================================
reset_memory_queue()
mw.after_agent({"messages": conversation()}, runtime(add_to_memory=False))
check("add_to_memory=false skips the queue", get_memory_queue().pending_count == 0,
      str(get_memory_queue().pending_count))

reset_memory_queue()
upload_only = [
    HumanMessage(content="<uploaded_files>\n- filename: secret.csv\n  path: /mnt/user-data/uploads/x/secret.csv\n</uploaded_files>"),
    AIMessage(content="I see the uploaded file."),
]
mw.after_agent({"messages": upload_only}, runtime())
check("upload-only turn is not queued", get_memory_queue().pending_count == 0,
      str(get_memory_queue().pending_count))

reset_memory_queue()
mw.after_agent({"messages": [HumanMessage(content="hello")]}, runtime())
check("user-only turn (no AI reply) is not queued", get_memory_queue().pending_count == 0,
      str(get_memory_queue().pending_count))

CMD_THREAD = "thread-cmd-0002"
mw.before_agent({"messages": [HumanMessage(content="/memory always cite IMO numbers")]}, runtime(thread_id=CMD_THREAD))
cmd_file = BASE / "threads" / CMD_THREAD / "memory.json"
check("/memory command persists a workspace rule", cmd_file.exists(), str(cmd_file))
if cmd_file.exists():
    rules = json.loads(cmd_file.read_text()).get("behaviorRules", [])
    check("rule text captured", any("always cite IMO numbers" == r["instruction"] for r in rules), str(rules))
    check("rule scoped to workspace", all(r["scope"] == "workspace" for r in rules), str(rules))

# ==========================================================================
section("8. Pre-compaction flush hook (bypasses debounce)")
# ==========================================================================
reset_memory_queue()
FLUSH_THREAD = "thread-flush-0003"
NEXT_FACTS = [{"content": "Uses geocoding API for address validation", "category": "knowledge", "confidence": 0.9}]
from src.agents.memory.summarization_hook import memory_flush_hook  # noqa: E402

ev = SimpleNamespace(
    thread_id=FLUSH_THREAD,
    agent_name=None,
    runtime=runtime(thread_id=FLUSH_THREAD),
    messages_to_summarize=conversation(),
)
memory_flush_hook(ev)
import time  # noqa: E402

for _ in range(50):
    if (BASE / "threads" / FLUSH_THREAD / "memory.json").exists():
        break
    time.sleep(0.1)
flush_file = BASE / "threads" / FLUSH_THREAD / "memory.json"
check("flush hook wrote memory without debounce", flush_file.exists(), str(flush_file))
if flush_file.exists():
    ff = json.loads(flush_file.read_text())["facts"]
    check("flush hook extracted the fact", any("geocoding" in f["content"] for f in ff), str(ff))

# ==========================================================================
section("9. Defect U-1: re-ingesting the same conversation duplicates facts")
# ==========================================================================
# This is the migration's headline defect, asserted rather than assumed:
# the legacy backend appends unconditionally, so replaying an identical
# conversation doubles the fact count instead of being a NOOP.
DUP_THREAD = "thread-dup-0004"
NEXT_FACTS = [{"content": "Prefers pytest over unittest", "category": "preference", "confidence": 0.9}]
dup_file = BASE / "threads" / DUP_THREAD / "memory.json"

reset_memory_queue()
mw.after_agent({"messages": conversation()}, runtime(thread_id=DUP_THREAD))
get_memory_queue().flush()
first_count = len(json.loads(dup_file.read_text())["facts"])

reset_memory_queue()
mw.after_agent({"messages": conversation()}, runtime(thread_id=DUP_THREAD))
get_memory_queue().flush()
second_count = len(json.loads(dup_file.read_text())["facts"])
dup_contents = [f["content"] for f in json.loads(dup_file.read_text())["facts"]]

check("first ingest stores 1 fact", first_count == 1, str(first_count))
check("U-1 CONFIRMED: identical re-ingest duplicates instead of NOOP",
      second_count == 2 and dup_contents[0] == dup_contents[1],
      f"count {first_count}->{second_count}: {dup_contents}")
print("        ^ expected under `legacy`; mem0's ADD/UPDATE/DELETE/NOOP loop is what fixes this")

# ==========================================================================
section("10. Isolation check")
# ==========================================================================
real = Path(__file__).resolve().parent.parent / ".capyhome" / "memory.json"
if real.exists():
    import hashlib

    digest = hashlib.md5(real.read_bytes()).hexdigest()
    expected = os.environ.get("REAL_MEMORY_MD5")
    check("developer's real memory.json untouched by this run",
          expected is None or digest == expected,
          f"MUTATED: {digest} != {expected}")
else:
    check("no real memory.json present to protect", True)
check("all writes landed under the temp CAPYBARA_HOME",
      all(str(p).startswith(str(BASE)) for p in BASE.rglob("*.json")))

# ==========================================================================
print(f"\n{'=' * 62}")
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nFAILED:")
    for f in FAIL:
        print(f"  - {f}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
