"""Shared middleware registry types and topological sort.

Each agent factory (``make_work_agent``, ``make_plan_agent``) builds its own
list of :class:`MiddlewareSpec` and feeds it to
:func:`topological_sort_middleware_specs` to produce an execution order that
respects declared ``after``/``before`` dependencies.
"""

from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware


@dataclass
class MiddlewareSpec:
    name: str
    factory: Callable[[], AgentMiddleware | None]
    after: set[str] = field(default_factory=set)
    before: set[str] = field(default_factory=set)
    # Lower priority runs earlier among siblings with no dependency edges between
    # them. Defaults to 0. Use this when deterministic ordering matters but
    # adding an explicit ``after={...}`` edge would over-constrain the DAG.
    priority: int = 0


def topological_sort_middleware_specs(specs: list[MiddlewareSpec]) -> list[MiddlewareSpec]:
    by_name = {spec.name: spec for spec in specs}
    if len(by_name) != len(specs):
        counts = Counter(spec.name for spec in specs)
        duplicate_names = [name for name, count in counts.items() if count > 1]
        raise ValueError(f"Duplicate middleware names found in registry: {duplicate_names}")

    graph: dict[str, set[str]] = {name: set() for name in by_name}
    in_degree: dict[str, int] = {name: 0 for name in by_name}

    def add_edge(src: str, dst: str) -> None:
        if dst not in graph[src]:
            graph[src].add(dst)
            in_degree[dst] += 1

    for spec in specs:
        for dependency in spec.after:
            if dependency not in by_name:
                raise ValueError(f"Middleware '{spec.name}' depends on unknown middleware '{dependency}'")
            add_edge(dependency, spec.name)
        for dependency in spec.before:
            if dependency not in by_name:
                raise ValueError(f"Middleware '{spec.name}' references unknown middleware '{dependency}' in before")
            add_edge(spec.name, dependency)

    def _rank(name: str) -> tuple[int, str]:
        # Tie-break first on explicit priority, then alphabetically for stability.
        return (by_name[name].priority, name)

    queue = deque(sorted([name for name, degree in in_degree.items() if degree == 0], key=_rank))
    ordered_names: list[str] = []
    while queue:
        current = queue.popleft()
        ordered_names.append(current)
        for neighbor in sorted(graph[current], key=_rank):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered_names) != len(specs):
        unresolved = [name for name, degree in in_degree.items() if degree > 0]
        raise ValueError(f"Middleware dependency cycle detected: {unresolved}")

    return [by_name[name] for name in ordered_names]
