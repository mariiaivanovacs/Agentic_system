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
import logging
import os
import re
import time
from collections import deque
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
                     domain: str) -> None:
    session.run(
        """
        MERGE (p:WebPage {url: $url})
        SET p.title       = $title,
            p.description = $description,
            p.h1          = $h1,
            p.word_count  = $word_count,
            p.status_code = $status_code,
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


def _clear_website(session, domain: str) -> None:
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
        word_count = len(soup.get_text().split())

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
            "links": links,
        }
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------- #
# Crawler                                                                      #
# --------------------------------------------------------------------------- #

def crawl(start_url: str, max_depth: int = 2, max_pages: int = 50,
          clear_existing: bool = False) -> dict:
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

    with driver.session(database=_db()) as session:
        if clear_existing:
            logger.info("Clearing existing data for domain: %s", domain)
            _clear_website(session, domain)

        _write_website_node(session, domain, start_url)
        logger.info("WebSite node created for: %s", domain)

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
            )
            pages_written += 1

            if depth < max_depth:
                for link_url, anchor in page["links"]:
                    link_domain = urlparse(link_url).netloc
                    if link_domain == domain and link_url not in visited:
                        _write_link_edge(session, page["url"], link_url, anchor)
                        edges_written += 1
                        queue.append((link_url, depth + 1))

            time.sleep(0.3)  # polite crawl delay

    driver.close()
    logger.info(
        "Done. Pages written: %d, Link edges written: %d", pages_written, edges_written
    )
    return {"domain": domain, "pages_written": pages_written, "edges_written": edges_written}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index a website into the EcoLink graph")
    parser.add_argument("--url",   required=True, help="Starting URL to crawl")
    parser.add_argument("--depth", type=int, default=1, help="Max crawl depth (default 1)")
    parser.add_argument("--max-pages", type=int, default=30, help="Max pages to index (default 30)")
    parser.add_argument("--clear", action="store_true", help="Delete existing data for this domain first")
    args = parser.parse_args()

    result = crawl(
        start_url=args.url,
        max_depth=args.depth,
        max_pages=args.max_pages,
        clear_existing=args.clear,
    )
    print(f"\n✓ Indexed {result['domain']}")
    print(f"  WebPage nodes : {result['pages_written']}")
    print(f"  LINKS_TO edges: {result['edges_written']}")
    print(f"\nView in Neo4j Browser:")
    print(f"  MATCH (w:WebSite {{domain: '{result['domain']}'}})-[:HAS_PAGE]->(p:WebPage)")
    print(f"  RETURN w, p LIMIT 50")
