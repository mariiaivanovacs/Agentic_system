"""
visualize_graph.py — reads any domain's graph from Neo4j and renders it as
an interactive HTML file using pyvis.

Opens the result in your default browser automatically.

Usage:
    python src/indexer/visualize_graph.py --domain example.com
    python src/indexer/visualize_graph.py --domain example.com --out my_graph.html
"""
from __future__ import annotations

import argparse
import os
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from pyvis.network import Network

load_dotenv()

# --------------------------------------------------------------------------- #
# Colour scheme                                                                #
# --------------------------------------------------------------------------- #

NODE_COLOURS = {
    "WebSite":  "#4A90D9",   # blue
    "WebPage":  "#7ED321",   # green
    "Company":  "#F5A623",   # orange
    "Mentor":   "#9B59B6",   # purple
    "Flow":     "#E74C3C",   # red
    "Skill":    "#1ABC9C",   # teal
    "Connector":"#F39C12",   # amber
    "Server":   "#95A5A6",   # grey
    "ExecutionTrace": "#E67E22",  # dark orange
}
DEFAULT_COLOUR = "#BDC3C7"

EDGE_COLOURS = {
    "LINKS_TO":     "#7ED321",
    "HAS_PAGE":     "#4A90D9",
    "MATCHED_WITH": "#9B59B6",
    "USES":         "#1ABC9C",
    "RUNS_ON":      "#95A5A6",
    "RAN_FLOW":     "#E67E22",
    "RESULTED_IN":  "#E74C3C",
}
DEFAULT_EDGE_COLOUR = "#BDC3C7"


# --------------------------------------------------------------------------- #
# Neo4j queries                                                                #
# --------------------------------------------------------------------------- #

def _get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )

def _db():
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def fetch_website_graph(domain: str) -> tuple[list[dict], list[dict]]:
    """Returns (nodes, edges) for a crawled WebSite domain."""
    driver = _get_driver()
    with driver.session(database=_db()) as session:
        node_rows = session.run(
            """
            MATCH (w:WebSite {domain: $domain})-[:HAS_PAGE]->(p:WebPage)
            RETURN
              w.domain      AS id,
              'WebSite'     AS label,
              w.domain      AS display,
              null          AS extra
            UNION
            MATCH (w:WebSite {domain: $domain})-[:HAS_PAGE]->(p:WebPage)
            RETURN
              p.url         AS id,
              'WebPage'     AS label,
              coalesce(p.title, p.url) AS display,
              toString(p.word_count) + ' words' AS extra
            """,
            domain=domain,
        ).data()

        edge_rows = session.run(
            """
            MATCH (w:WebSite {domain: $domain})-[:HAS_PAGE]->(p:WebPage)
            WITH w, collect(p.url) AS page_urls
            WITH page_urls + [w.domain] AS ids
            MATCH (a)-[r]->(b)
            WHERE (a.url IN ids OR a.domain IN ids)
              AND (b.url IN ids OR b.domain IN ids)
            RETURN
              coalesce(a.url, a.domain) AS from_id,
              coalesce(b.url, b.domain) AS to_id,
              type(r) AS rel_type,
              coalesce(r.anchor_text, '') AS label
            """,
            domain=domain,
        ).data()

    driver.close()
    return node_rows, edge_rows


def fetch_full_ecolink_graph() -> tuple[list[dict], list[dict]]:
    """Returns nodes and edges for the full EcoLink dual graph (Graph A + B)."""
    driver = _get_driver()
    with driver.session(database=_db()) as session:
        node_rows = session.run(
            """
            MATCH (n)
            WHERE n:Company OR n:Mentor OR n:Flow OR n:Skill
               OR n:Connector OR n:Server OR n:ExecutionTrace
            RETURN
              coalesce(n.id, n.name, toString(id(n))) AS id,
              labels(n)[0] AS label,
              coalesce(n.name, n.id, toString(id(n))) AS display,
              coalesce(toString(n.industry), toString(n.type),
                       toString(n.status), '') AS extra
            LIMIT 200
            """
        ).data()

        edge_rows = session.run(
            """
            MATCH (a)-[r]->(b)
            WHERE (a:Company OR a:Mentor OR a:Flow OR a:Skill
                   OR a:Connector OR a:Server OR a:ExecutionTrace)
              AND (b:Company OR b:Mentor OR b:Flow OR b:Skill
                   OR b:Connector OR b:Server OR b:Outcome)
            RETURN
              coalesce(a.id, a.name, toString(id(a))) AS from_id,
              coalesce(b.id, b.name, toString(id(b))) AS to_id,
              type(r) AS rel_type,
              coalesce(toString(r.outcome_score), '') AS label
            LIMIT 500
            """
        ).data()

    driver.close()
    return node_rows, edge_rows


# --------------------------------------------------------------------------- #
# Pyvis builder                                                                #
# --------------------------------------------------------------------------- #

def build_html(nodes: list[dict], edges: list[dict], title: str,
               out_path: str = "graph.html") -> str:
    net = Network(
        height="800px",
        width="100%",
        bgcolor="#1a1a2e",
        font_color="#ecf0f1",
        directed=True,
        notebook=False,
    )
    net.set_options("""
    {
      "physics": {
        "barnesHut": { "gravitationalConstant": -8000, "springLength": 150 },
        "stabilization": { "iterations": 200 }
      },
      "edges": { "smooth": { "type": "curvedCW", "roundness": 0.2 } }
    }
    """)

    seen_nodes: set[str] = set()
    for n in nodes:
        nid = str(n["id"])
        if nid in seen_nodes:
            continue
        seen_nodes.add(nid)
        label_type = n.get("label", "Node")
        colour = NODE_COLOURS.get(label_type, DEFAULT_COLOUR)
        tooltip = f"{label_type}: {n['display']}"
        if n.get("extra"):
            tooltip += f"\n{n['extra']}"
        net.add_node(
            nid,
            label=str(n["display"])[:40],
            title=tooltip,
            color=colour,
            size=20 if label_type in ("WebSite", "Company", "Flow") else 14,
            font={"size": 11, "color": "#ecf0f1"},
        )

    for e in edges:
        from_id = str(e["from_id"])
        to_id   = str(e["to_id"])
        rel     = e.get("rel_type", "")
        colour  = EDGE_COLOURS.get(rel, DEFAULT_EDGE_COLOUR)
        if from_id in seen_nodes and to_id in seen_nodes:
            net.add_edge(
                from_id, to_id,
                title=rel,
                label=str(e.get("label", ""))[:30] if e.get("label") else rel,
                color=colour,
                arrows="to",
                font={"size": 9, "color": "#bdc3c7"},
            )

    # inject a title banner
    net.html = net.generate_html()
    banner = f'<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#16213e;color:#ecf0f1;padding:8px 24px;border-radius:8px;font-family:sans-serif;font-size:14px;z-index:999;">{title}</div>'
    net.html = net.html.replace("<body>", f"<body>{banner}", 1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(net.html)

    return out_path


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Visualize the EcoLink graph from Neo4j")
    parser.add_argument("--domain", default=None,
                        help="Domain to visualize (e.g. example.com). Omit for full EcoLink graph.")
    parser.add_argument("--out",    default="graph_output.html",
                        help="Output HTML file (default: graph_output.html)")
    args = parser.parse_args()

    if args.domain:
        print(f"Fetching graph for domain: {args.domain} ...")
        nodes, edges = fetch_website_graph(args.domain)
        title = f"EcoLink — Web Graph: {args.domain}"
    else:
        print("Fetching full EcoLink dual graph (Graph A + B) ...")
        nodes, edges = fetch_full_ecolink_graph()
        title = "EcoLink NeuroCore — Dual Graph (Graph A + B)"

    if not nodes:
        print("No nodes found. Run the web indexer first or check your Neo4j connection.")
        raise SystemExit(1)

    out_path = build_html(nodes, edges, title=title, out_path=args.out)
    print(f"\n✓ Graph rendered: {out_path}")
    print(f"  Nodes : {len(nodes)}")
    print(f"  Edges : {len(edges)}")
    print(f"\nOpening in browser...")
    webbrowser.open(f"file://{Path(out_path).resolve()}")
