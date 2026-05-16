"""
web_indexer.py — crawls a website and stores its structure in Neo4j.

Each page becomes a :WebPage node.
Each link between pages becomes a :LINKS_TO edge.
The domain itself becomes a :WebSite node linked to all its pages.

Usage:
    python src/indexer/web_indexer.py --url https://example.com --depth 2
    python src/indexer/web_indexer.py --url https://example.com --depth 1 --clear
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Neo4j connection                                                             #
# --------------------------------------------------------------------------- #

def _get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )

def _db():
    return os.environ.get("NEO4J_DATABASE", "neo4j")


# --------------------------------------------------------------------------- #
# Graph write helpers                                                          #
# --------------------------------------------------------------------------- #

def _write_website_node(session, domain: str, start_url: str) -> None:
    session.run(
        """
        MERGE (w:WebSite {domain: $domain})
        SET w.start_url  = $start_url,
            w.indexed_at = datetime()
        """,
        domain=domain,
        start_url=start_url,
    )


def _write_page_node(session, url: str, title: str, description: str,
                     h1: str, word_count: int, status_code: int,
                     domain: str, app_id: str) -> None:
    session.run(
        """
        MERGE (p:WebPage {url: $url})
        SET p.title       = $title,
            p.description = $description,
            p.h1          = $h1,
            p.word_count  = $word_count,
            p.status_code = $status_code,
            p.app_id      = $app_id,
            p.indexed_at  = datetime()
        WITH p
        MATCH (w:WebSite {domain: $domain})
        MERGE (w)-[:HAS_PAGE]->(p)
        """,
        url=url,
        title=title,
        description=description,
        h1=h1,
        word_count=word_count,
        status_code=status_code,
        domain=domain,
        app_id=app_id,
    )


def _write_link_edge(session, from_url: str, to_url: str, anchor_text: str) -> None:
    session.run(
        """
        MERGE (a:WebPage {url: $from_url})
        MERGE (b:WebPage {url: $to_url})
        MERGE (a)-[r:LINKS_TO]->(b)
        SET r.anchor_text = $anchor_text
        """,
        from_url=from_url,
        to_url=to_url,
        anchor_text=anchor_text[:200] if anchor_text else "",
    )


def _entity_id(domain: str, entity_type: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80] or "unknown"
    domain_slug = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")
    return f"webentity_{domain_slug}_{entity_type.lower()}_{slug}"


def _write_entity(
    session, domain: str, entity: dict,
    page_url: str | None = None,
    app_id: str | None = None,
) -> None:
    entity_type = entity["entity_type"]
    name = entity["name"]
    entity_id = entity.get("id") or _entity_id(domain, entity_type, name)
    metadata = json.dumps(entity.get("metadata", {}))
    _app_id = app_id or domain

    session.run(
        """
        MERGE (e:WebEntity {id: $id})
        SET e.name        = $name,
            e.entity_type = $entity_type,
            e.source      = $source,
            e.description = $description,
            e.category    = $category,
            e.value       = $value,
            e.url         = $url,
            e.metadata    = $metadata,
            e.app_id      = $app_id,
            e.indexed_at  = datetime()
        WITH e
        MATCH (w:WebSite {domain: $domain})
        MERGE (w)-[r:SITE_HAS_ENTITY]->(e)
        SET r.source = $source
        """,
        id=entity_id,
        name=name,
        entity_type=entity_type,
        source=entity.get("source", "website"),
        description=entity.get("description", ""),
        category=entity.get("category", ""),
        value=float(entity.get("value", 0) or 0),
        url=entity.get("url", ""),
        metadata=metadata,
        app_id=_app_id,
        domain=domain,
    )

    if page_url:
        session.run(
            """
            MATCH (p:WebPage {url: $page_url})
            MATCH (e:WebEntity {id: $id})
            MERGE (p)-[r:MENTIONS_ENTITY]->(e)
            SET r.confidence = $confidence,
                r.source = $source
            """,
            page_url=page_url,
            id=entity_id,
            confidence=float(entity.get("confidence", 0.75)),
            source=entity.get("source", "website"),
        )


def _link_entities(session, from_id: str, rel_type: str, to_id: str, **props) -> None:
    allowed = {"OWNS_CAMPAIGN", "DONATED_TO", "EXPOSES_ROUTE"}
    if rel_type not in allowed:
        raise ValueError(f"Unsupported entity relationship: {rel_type}")

    prop_set = ""
    if props:
        prop_set = " SET " + ", ".join(f"r.{k} = ${k}" for k in props)

    session.run(
        f"""
        MATCH (a:WebEntity {{id: $from_id}})
        MATCH (b:WebEntity {{id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        {prop_set}
        """,
        from_id=from_id,
        to_id=to_id,
        **props,
    )


def _link_site_route(session, domain: str, route_id: str) -> None:
    session.run(
        """
        MATCH (w:WebSite {domain: $domain})
        MATCH (r:WebEntity {id: $route_id})
        MERGE (w)-[:EXPOSES_ROUTE]->(r)
        """,
        domain=domain,
        route_id=route_id,
    )


def _write_app_profile(
    session, domain: str, start_url: str, source_path: str | None
) -> None:
    source_type = "hybrid" if source_path else "website"
    session.run(
        """
        MERGE (ap:AppProfile {app_id: $app_id})
        SET ap.app_name        = $app_name,
            ap.source_type     = $source_type,
            ap.base_url        = $base_url,
            ap.source_path     = $source_path,
            ap.last_indexed_at = datetime()
        WITH ap
        MATCH (w:WebSite {domain: $domain})
        MERGE (ap)-[:HAS_WEBSITE]->(w)
        """,
        app_id=domain,
        app_name=domain,
        source_type=source_type,
        base_url=start_url,
        source_path=source_path or "",
        domain=domain,
    )


def _clear_website(session, domain: str) -> None:
    session.run(
        """
        MATCH (w:WebSite {domain: $domain})-[:SITE_HAS_ENTITY]->(e:WebEntity)
        DETACH DELETE e
        """,
        domain=domain,
    )
    session.run(
        """
        MATCH (w:WebSite {domain: $domain})-[:HAS_PAGE]->(p:WebPage)
        DETACH DELETE p
        """,
        domain=domain,
    )
    session.run("MATCH (w:WebSite {domain: $domain}) DETACH DELETE w", domain=domain)


# --------------------------------------------------------------------------- #
# Page fetching                                                                #
# --------------------------------------------------------------------------- #

HEADERS = {"User-Agent": "EcoLink-NeuroCore-Indexer/1.0 (research bot)"}
TIMEOUT = 10


def _fetch_page(url: str) -> Optional[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        title = (soup.find("title") or soup.new_tag("t")).get_text(strip=True)[:300]
        desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        description = (desc_tag.get("content", "") if desc_tag else "")[:500]
        h1_tag = soup.find("h1")
        h1 = (h1_tag.get_text(strip=True) if h1_tag else "")[:200]
        page_text = " ".join(soup.get_text(" ").split())
        word_count = len(page_text.split())

        # collect internal links
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            anchor = a.get_text(strip=True)[:100]
            abs_url = urljoin(url, href)
            parsed = urlparse(abs_url)
            if parsed.scheme in ("http", "https") and parsed.fragment == "":
                # strip query string for dedup
                clean = parsed._replace(query="", fragment="").geturl()
                links.append((clean, anchor))

        return {
            "url": url,
            "status_code": resp.status_code,
            "title": title,
            "description": description,
            "h1": h1,
            "word_count": word_count,
            "text": page_text[:5000],
            "links": links,
        }
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------- #
# Entity extraction                                                            #
# --------------------------------------------------------------------------- #

CAMPAIGN_WORDS = ("campaign", "fund", "funding", "donate", "donation", "backer", "target")
ROLE_WORDS = ("dashboard", "profile", "payment", "withdraw", "logout")


def _extract_page_entities(domain: str, page: dict) -> list[dict]:
    text = " ".join(
        str(page.get(k, "")) for k in ("title", "description", "h1", "text")
    )
    entities: list[dict] = []

    if any(word in text.lower() for word in CAMPAIGN_WORDS):
        entities.append(
            {
                "id": _entity_id(domain, "Feature", "Campaign Funding"),
                "entity_type": "Feature",
                "name": "Campaign Funding",
                "description": "Website supports campaign discovery, creation, and donations.",
                "category": "Crowdfunding",
                "source": "page_text",
                "confidence": 0.8,
            }
        )

    for token in ROLE_WORDS:
        if re.search(rf"\b{re.escape(token)}\b", text, re.I):
            entities.append(
                {
                    "id": _entity_id(domain, "Feature", token),
                    "entity_type": "Feature",
                    "name": token.title(),
                    "description": f"UI capability detected from page text: {token}",
                    "source": "page_text",
                    "confidence": 0.65,
                }
            )

    for amount in sorted(set(re.findall(r"\b\d+(?:\.\d+)?\s*STX\b", text, re.I))):
        entities.append(
            {
                "id": _entity_id(domain, "TokenAmount", amount),
                "entity_type": "TokenAmount",
                "name": amount.upper(),
                "description": "Detected STX-denominated amount in rendered website text.",
                "category": "STX",
                "source": "page_text",
                "confidence": 0.7,
            }
        )

    return entities


def _string_literal(raw: str) -> str:
    return ast.literal_eval(raw.strip())


def _extract_object_field(block: str, field: str) -> str | None:
    match = re.search(rf"{field}\s*:\s*('(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\")", block)
    if not match:
        return None
    try:
        return _string_literal(match.group(1))
    except Exception:
        return match.group(1).strip("'\"")


def _extract_number_field(block: str, field: str) -> float:
    match = re.search(rf"{field}\s*:\s*(\d+(?:\.\d+)?)", block)
    return float(match.group(1)) if match else 0.0


def _campaign_blocks(text: str) -> list[str]:
    marker = "export const mockCampaigns"
    start = text.find(marker)
    if start < 0:
        return []
    array_start = text.find("[", start)
    array_end = text.find("];", array_start)
    if array_start < 0 or array_end < 0:
        return []
    body = text[array_start + 1:array_end]
    blocks: list[str] = []
    depth = 0
    block_start: int | None = None
    for idx, char in enumerate(body):
        if char == "{":
            if depth == 0:
                block_start = idx
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and block_start is not None:
                blocks.append(body[block_start:idx + 1])
                block_start = None
    return blocks


def _extract_source_entities(domain: str, source_path: str | None) -> tuple[list[dict], list[tuple[str, str, str, dict]]]:
    if not source_path:
        return [], []

    root = Path(source_path).expanduser().resolve()
    if not root.exists():
        logger.warning("Source path does not exist: %s", root)
        return [], []

    entities: dict[str, dict] = {}
    relationships: list[tuple[str, str, str, dict]] = []

    campaigns_file = next(root.rglob("campaigns.ts"), None)
    if campaigns_file:
        text = campaigns_file.read_text(encoding="utf-8")
        for block in _campaign_blocks(text):
            campaign_id = _extract_object_field(block, "id")
            title = _extract_object_field(block, "title")
            if not campaign_id or not title:
                continue

            owner = _extract_object_field(block, "owner") or "Unknown owner"
            category = _extract_object_field(block, "category") or ""
            description = _extract_object_field(block, "description") or ""
            target = _extract_number_field(block, "target")
            amount = _extract_number_field(block, "amountCollected")
            backers = _extract_number_field(block, "backers")
            campaign_entity_id = _entity_id(domain, "Campaign", campaign_id)
            owner_entity_id = _entity_id(domain, "Person", owner)
            entities[campaign_entity_id] = {
                "id": campaign_entity_id,
                "entity_type": "Campaign",
                "name": title,
                "description": description,
                "category": category,
                "value": target,
                "source": "source_code",
                "metadata": {
                    "campaign_id": campaign_id,
                    "target": target,
                    "amount_collected": amount,
                    "backers": int(backers),
                    "file": str(campaigns_file),
                },
            }
            entities[owner_entity_id] = {
                "id": owner_entity_id,
                "entity_type": "Person",
                "name": owner,
                "description": "Campaign owner detected in source data.",
                "source": "source_code",
                "metadata": {"file": str(campaigns_file)},
            }
            relationships.append((owner_entity_id, "OWNS_CAMPAIGN", campaign_entity_id, {}))

            for donor, donation in re.findall(
                r"\{\s*name:\s*('(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\")\s*,\s*amount:\s*(\d+(?:\.\d+)?)",
                block,
            ):
                donor_name = _string_literal(donor)
                donor_id = _entity_id(domain, "Person", donor_name)
                entities[donor_id] = {
                    "id": donor_id,
                    "entity_type": "Person",
                    "name": donor_name,
                    "description": "Donor detected in campaign source data.",
                    "source": "source_code",
                    "metadata": {"file": str(campaigns_file)},
                }
                relationships.append(
                    (donor_id, "DONATED_TO", campaign_entity_id, {"amount": float(donation)})
                )

    app_file = next(root.rglob("App.tsx"), None)
    if app_file:
        text = app_file.read_text(encoding="utf-8")
        for route in sorted(set(re.findall(r"<Route\s+path=[\"']([^\"']+)[\"']", text))):
            route_id = _entity_id(domain, "Route", route)
            entities[route_id] = {
                "id": route_id,
                "entity_type": "Route",
                "name": route,
                "description": "React route detected in App.tsx.",
                "source": "source_code",
                "metadata": {"file": str(app_file)},
            }

    for clar_file in root.rglob("*.clar"):
        text = clar_file.read_text(encoding="utf-8", errors="ignore")
        for kind, name in re.findall(r"\(define-(public|read-only)\s+\(([a-zA-Z0-9_-]+)", text):
            method_id = _entity_id(domain, "ContractMethod", name)
            entities[method_id] = {
                "id": method_id,
                "entity_type": "ContractMethod",
                "name": name,
                "description": f"Clarity {kind} contract method.",
                "category": kind,
                "source": "source_code",
                "metadata": {"file": str(clar_file)},
            }

    return list(entities.values()), relationships


# --------------------------------------------------------------------------- #
# Crawler                                                                      #
# --------------------------------------------------------------------------- #

def crawl(start_url: str, max_depth: int = 2, max_pages: int = 50,
          clear_existing: bool = False, source_path: str | None = None) -> dict:
    """
    Crawls start_url up to max_depth levels deep, staying on the same domain.
    Writes WebSite, WebPage nodes and LINKS_TO / HAS_PAGE edges to Neo4j.

    Returns a summary dict: {domain, pages_written, edges_written}.
    """
    parsed_start = urlparse(start_url)
    domain = parsed_start.netloc

    driver = _get_driver()
    pages_written = 0
    edges_written = 0
    entities_written = 0
    entity_relationships_written = 0

    with driver.session(database=_db()) as session:
        if clear_existing:
            logger.info("Clearing existing data for domain: %s", domain)
            _clear_website(session, domain)

        _write_website_node(session, domain, start_url)
        _write_app_profile(session, domain, start_url, source_path)
        logger.info("WebSite + AppProfile nodes created for: %s", domain)

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])

        while queue and pages_written < max_pages:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            logger.info("[depth %d] Fetching: %s", depth, url)
            page = _fetch_page(url)
            if page is None:
                continue

            _write_page_node(
                session,
                url=page["url"],
                title=page["title"],
                description=page["description"],
                h1=page["h1"],
                word_count=page["word_count"],
                status_code=page["status_code"],
                domain=domain,
                app_id=domain,
            )
            pages_written += 1

            for entity in _extract_page_entities(domain, page):
                _write_entity(session, domain, entity, page_url=page["url"], app_id=domain)
                entities_written += 1

            if depth < max_depth:
                for link_url, anchor in page["links"]:
                    link_domain = urlparse(link_url).netloc
                    if link_domain == domain and link_url not in visited:
                        _write_link_edge(session, page["url"], link_url, anchor)
                        edges_written += 1
                        queue.append((link_url, depth + 1))

            time.sleep(0.3)  # polite crawl delay

        source_entities, source_relationships = _extract_source_entities(domain, source_path)
        for entity in source_entities:
            _write_entity(session, domain, entity, app_id=domain)
            entities_written += 1
            if entity["entity_type"] == "Route":
                _link_site_route(session, domain, entity["id"])

        for from_id, rel_type, to_id, props in source_relationships:
            _link_entities(session, from_id, rel_type, to_id, **props)
            entity_relationships_written += 1

    driver.close()

    # Discover and write pipelines now that all entities are in the graph
    pipelines_written = 0
    try:
        from src.indexer.pipeline_builder import build_and_write as _build_pipelines
        pipelines_written = _build_pipelines(domain)
    except Exception as exc:
        logger.warning("Pipeline discovery failed (non-fatal): %s", exc)

    # Embed Skill nodes — non-fatal; skipped silently when GOOGLE_API_KEY is absent
    try:
        from src.graphrag.embedder import embed_graph_nodes
        embedded = embed_graph_nodes(labels=["Skill"])
        logger.info("Embedded %d Skill nodes.", embedded)
    except Exception as exc:
        logger.warning("Embedding step skipped (non-fatal): %s", exc)

    logger.info(
        "Done. Pages: %d, Link edges: %d, Entities: %d, Pipelines: %d",
        pages_written,
        edges_written,
        entities_written,
        pipelines_written,
    )
    return {
        "app_id": domain,
        "domain": domain,
        "pages_written": pages_written,
        "edges_written": edges_written,
        "entities_written": entities_written,
        "entity_relationships_written": entity_relationships_written,
        "pipelines_written": pipelines_written,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index a website into the EcoLink graph")
    parser.add_argument("--url",   required=True, help="Starting URL to crawl")
    parser.add_argument("--depth", type=int, default=1, help="Max crawl depth (default 1)")
    parser.add_argument("--max-pages", type=int, default=30, help="Max pages to index (default 30)")
    parser.add_argument("--clear", action="store_true", help="Delete existing data for this domain first")
    parser.add_argument(
        "--source-path",
        default=None,
        help="Optional local source folder to extract app identities/entities from",
    )
    args = parser.parse_args()

    result = crawl(
        start_url=args.url,
        max_depth=args.depth,
        max_pages=args.max_pages,
        clear_existing=args.clear,
        source_path=args.source_path,
    )
    print(f"\n✓ Indexed {result['domain']}")
    print(f"  WebPage nodes : {result['pages_written']}")
    print(f"  LINKS_TO edges: {result['edges_written']}")
    print(f"  WebEntity nodes: {result['entities_written']}")
    print(f"  Entity edges   : {result['entity_relationships_written']}")
    print(f"\nView in Neo4j Browser:")
    print(f"  MATCH (w:WebSite {{domain: '{result['domain']}'}})-[:HAS_PAGE]->(p:WebPage)")
    print(f"  RETURN w, p LIMIT 50")
