# Phase 4 — Knowledge Graph Visualization

**Status**: Not started  
**Effort**: High (frontend-heavy, new React component with graph library)  
**Prerequisite**: Phases 1–3 (graph is most useful once vector search + CoT ingest + clipper are producing well-structured, cross-referenced pages)

---

## Problem

The vault currently holds 1,216 sources and 282 syntheses, but the only way to understand their relationships is through text search or by reading the autoresearch progress markdown files. There is no structural view of:

- How entities relate to each other
- Which concepts recur across multiple syntheses and sources
- Knowledge gaps: isolated pages with no connections
- Knowledge density: which areas of the vault are well-covered vs. sparse
- Bridge nodes: pages that connect otherwise separate knowledge clusters

The vault frontend tab shows objective-level progress cards. It does not show the knowledge topology.

---

## What llm_wiki Does

llm_wiki renders a full interactive knowledge graph using:

- **sigma.js** — WebGL-accelerated graph rendering
- **graphology** — graph data structure and algorithms
- **ForceAtlas2** — force-directed layout (runs in a web worker to avoid blocking UI)
- **graphology-communities-louvain** — automatic cluster discovery

**4-signal relevance model** for edge weights:

| Signal | Weight | Basis |
|---|---|---|
| Direct wikilink | ×3.0 | `[[wikilinks]]` in page content |
| Source overlap | ×4.0 | Pages sharing `sources[]` in frontmatter |
| Adamic-Adar | ×1.5 | Pages sharing common neighbors (weighted by neighbor degree) |
| Type affinity | ×1.0 | Same page type (entity↔entity, concept↔concept) |

**Louvain community detection** clusters pages into natural knowledge groups, independent of page type. Communities are scored by intra-edge density (cohesion = actual edges / possible edges). Communities with cohesion < 0.15 are flagged as sparse.

**Graph Insights** surface actionable information:
- **Isolated pages** (degree ≤ 1): not connected to the rest of the vault
- **Bridge nodes**: pages connecting 3+ separate communities — critical junction points
- **Surprising connections**: cross-community edges that represent unexpected relationships
- **Sparse communities**: clusters with weak internal cross-referencing

Each insight card has a "Deep Research" button that triggers targeted research to fill the gap.

---

## What to Build in Capybara

### Data source: wikilink parser

The vault's compiled pages already use `[[wikilink]]` syntax (this is part of the Karpathy pattern). A backend endpoint parses these to build an adjacency list:

**New endpoint**: `GET /api/vault/graph`

```python
@router.get("/graph")
async def get_vault_graph():
    vault_manager = get_default_vault_manager()
    pages = vault_manager.list_compiled_pages()

    nodes = []
    edges = []

    for page in pages:
        nodes.append({
            "id": page.id,
            "label": page.title,
            "type": page.frontmatter.get("type", "unknown"),
            "tags": page.frontmatter.get("tags", []),
            "source_count": len(page.frontmatter.get("sources", [])),
        })

        # Parse [[wikilinks]] from page content
        wikilinks = re.findall(r'\[\[([^\]]+)\]\]', page.content)
        for target in wikilinks:
            target_id = slugify(target)
            if target_id in page_id_set:
                edges.append({
                    "source": page.id,
                    "target": target_id,
                    "signal": "wikilink",
                    "weight": 3.0,
                })

        # Source overlap edges (pages sharing sources[])
        # computed separately after all pages are loaded

    # Source overlap: O(n²) over source_refs — acceptable for <5000 pages
    source_to_pages = defaultdict(list)
    for page in pages:
        for src in page.frontmatter.get("sources", []):
            source_to_pages[src].append(page.id)

    for src, sharing_pages in source_to_pages.items():
        for a, b in combinations(sharing_pages, 2):
            edges.append({"source": a, "target": b, "signal": "source_overlap", "weight": 4.0})

    return {"nodes": nodes, "edges": edges}
```

### Frontend: graph panel in vault tab

Add a "Graph" view toggle to the vault page. The existing vault page (`frontend/src/app/workspace/vault/page.tsx`) shows objective cards — add a tab switcher between `Objectives` and `Graph`.

**Libraries to add**:
```bash
pnpm add sigma graphology graphology-layout-forceatlas2 graphology-communities-louvain
```

sigma.js and graphology are the same libraries llm_wiki uses and are MIT licensed.

**Component structure**:

```
frontend/src/components/workspace/vault/
  VaultGraph.tsx           # main graph component
  VaultGraphInsights.tsx   # insights panel (isolated, bridges, sparse clusters)
  VaultGraphLegend.tsx     # node type / community legend with toggle
  VaultGraphControls.tsx   # zoom in/out, fit-to-screen, color-mode toggle
```

**VaultGraph.tsx — key implementation points**:

```tsx
import Graph from 'graphology';
import Sigma from 'sigma';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import louvain from 'graphology-communities-louvain';

export function VaultGraph() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { data } = useVaultGraph();   // new hook calling /api/vault/graph

  useEffect(() => {
    if (!data || !containerRef.current) return;

    const graph = new Graph({ multi: true });

    // Add nodes
    data.nodes.forEach(n => {
      graph.addNode(n.id, {
        label: n.label,
        size: Math.sqrt(n.source_count + 1) * 4,   // size by connection richness
        color: TYPE_COLORS[n.type] ?? '#888',
        x: Math.random(), y: Math.random(),          // ForceAtlas2 will re-layout
      });
    });

    // Add edges with weight
    data.edges.forEach(e => {
      graph.addEdge(e.source, e.target, { weight: e.weight });
    });

    // Run ForceAtlas2 layout (synchronous for small graphs, worker for large)
    forceAtlas2.assign(graph, { iterations: 100, settings: { gravity: 1 } });

    // Louvain community detection
    const communities = louvain(graph);
    graph.forEachNode((node) => {
      graph.setNodeAttribute(node, 'community', communities[node]);
    });

    // Render with Sigma
    const sigma = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      defaultEdgeColor: '#ccc',
    });

    return () => sigma.kill();
  }, [data]);

  return <div ref={containerRef} className="w-full h-[600px]" />;
}
```

**Color modes** (toggle between):
- **By type**: `entity` = blue, `concept` = green, `source` = gray, `synthesis` = orange
- **By community**: 12-color palette, each Louvain cluster distinct

**Node sizing**: `√(degree)` scaling — hub pages render visibly larger than leaf pages.

**Hover interaction** (matches llm_wiki behavior):
- Hovered node and its neighbors: full opacity
- Non-neighbors: 20% opacity
- Edge labels show relevance signal type (wikilink / source_overlap)

### Insights panel

Built as a sidebar panel alongside the graph (right column when graph is visible):

**Isolated pages** (`degree ≤ 1`):
```tsx
{insights.isolated.map(page => (
  <InsightCard
    title={page.label}
    detail="No connections to other pages"
    action={<DeepResearchButton topic={page.label} />}
  />
))}
```

**Bridge nodes** (connecting 3+ communities):
```tsx
{insights.bridges.map(page => (
  <InsightCard
    title={page.label}
    detail={`Connects ${page.community_count} knowledge clusters`}
    variant="highlight"
  />
))}
```

**Sparse communities** (cohesion < 0.15):
```tsx
{insights.sparse_communities.map(community => (
  <InsightCard
    title={`Cluster: ${community.top_label}`}
    detail={`${community.member_count} pages, cohesion ${community.cohesion.toFixed(2)}`}
    action={<DeepResearchButton topic={community.top_label} />}
  />
))}
```

The "Deep Research" button triggers an autoresearch objective via `useStartAutoresearchObjective()` — the same hook the vault page already uses for creating objectives. This closes the loop: graph insight → objective creation → scheduled research → new pages → graph updates.

---

## Backend: compute insights

Add graph analytics to the `/api/vault/graph` response (or a separate `/api/vault/graph/insights` endpoint):

```python
def compute_graph_insights(graph_data: dict) -> dict:
    G = build_networkx_graph(graph_data)

    # Isolated pages
    isolated = [n for n, d in G.degree() if d <= 1]

    # Community detection (Python: networkx-community or cdlib)
    communities = nx.algorithms.community.louvain_communities(G)
    community_map = {node: i for i, comm in enumerate(communities) for node in comm}

    # Cohesion per community
    sparse = []
    for i, comm in enumerate(communities):
        subgraph = G.subgraph(comm)
        n = len(comm)
        if n < 3:
            continue
        possible = n * (n - 1) / 2
        actual = subgraph.number_of_edges()
        cohesion = actual / possible if possible > 0 else 0
        if cohesion < 0.15:
            top_label = max(comm, key=lambda x: G.degree(x))
            sparse.append({"community_id": i, "top_label": top_label,
                           "member_count": n, "cohesion": cohesion})

    # Bridge nodes (connecting 3+ communities)
    bridges = []
    for node in G.nodes():
        neighbor_communities = set(community_map[n] for n in G.neighbors(node)
                                   if n in community_map)
        if len(neighbor_communities) >= 3:
            bridges.append({"id": node, "label": G.nodes[node].get("label", node),
                            "community_count": len(neighbor_communities)})

    return {"isolated": isolated, "sparse_communities": sparse, "bridges": bridges}
```

Python package: `networkx` (already commonly available) or `python-louvain` for community detection.

---

## Performance considerations

| Vault size | Nodes | Edges (est.) | Render approach |
|---|---|---|---|
| < 500 pages | < 500 | < 2,000 | Synchronous ForceAtlas2 in main thread |
| 500–2,000 pages | 500–2,000 | 2,000–20,000 | ForceAtlas2 in web worker |
| > 2,000 pages | > 2,000 | > 20,000 | Pre-compute layout server-side, cache positions |

The current vault has ~1,500 pages — use the web worker approach from launch. Position caching (persist ForceAtlas2 output to localStorage keyed by `graph_hash`) prevents layout re-computation on every page load.

---

## What the graph should reveal

- hub pages that many later syntheses depend on
- isolated pages that were ingested but never properly linked
- concept clusters that suggest strong topic coverage
- weakly connected areas that indicate thin or fragmented knowledge

Those insights should feed back into discovery and vault maintenance.
