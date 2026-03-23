import re
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI, Query


app = FastAPI()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

ALLOWED_KEYWORDS = {
    "artificial intelligence",
    "ai",
    "enterprise software",
    "data science",
    "developer tools",
    "machine learning",
    "analytics",
    "automation",
    "saas",
    "infrastructure",
    "data",
    "ml",
    "agent",
    "agents",
}

SECTION_HEADERS = {
    "what they do:",
    "quick facts:",
    "funding:",
    "founders:",
    "take action:",
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    return clean_text(text).lower()


def build_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    query_params = parse_qs(parsed.query)
    query_params["page"] = [str(page)]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def get_page_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def split_lines(card: Tag) -> List[str]:
    return [
        clean_text(line)
        for line in card.get_text("\n", strip=True).split("\n")
        if clean_text(line)
    ]


def extract_startup_name(card: Tag) -> str:
    for tag_name in ["h1", "h2", "h3"]:
        tag = card.find(tag_name)
        if tag:
            text = clean_text(tag.get_text(" ", strip=True))
            if text:
                return text

    lines = split_lines(card)
    for line in lines[:5]:
        if ":" not in line and 1 <= len(line.split()) <= 5:
            return line

    return ""


def get_section_lines(card: Tag, label: str) -> List[str]:
    lines = split_lines(card)
    target = normalize(label)

    collected = []
    capture = False

    for line in lines:
        lower = normalize(line)

        if lower == target:
            capture = True
            continue

        if capture and lower in SECTION_HEADERS:
            break

        if capture:
            collected.append(line)

    return collected


def parse_what_they_do(section_lines: List[str]) -> Dict[str, object]:
    description_parts = []
    tags = []

    for line in section_lines:
        if len(line.split()) <= 4 and line and line[0].isupper():
            tags.append(line)
        else:
            description_parts.append(line)

    return {
        "description": clean_text(" ".join(description_parts)),
        "tags": tags,
    }


def parse_quick_facts(section_lines: List[str]) -> Dict[str, str]:
    quick_facts_text = " ".join(section_lines)
    result = {"hq": ""}

    if not quick_facts_text:
        return result

    hq_match = re.search(
        r"HQ:\s*(.*?)(?:\b\d{1,4}[–-]\d{1,4}\s*employees\b|Founded:|$)",
        quick_facts_text,
        re.I,
    )
    if hq_match:
        result["hq"] = clean_text(hq_match.group(1))

    return result


def extract_action_links(card: Tag, base_url: str = "https://topstartups.io") -> Dict[str, str]:
    links = {
        "linkedin_url": "",
        "company_website": "",
        "jobs_url": "",
    }

    for p in card.find_all("p"):
        p_text = normalize(p.get_text(" ", strip=True))
        if "take action:" in p_text:
            for a in p.find_all("a", href=True):
                link_text = normalize(a.get_text(" ", strip=True))
                href = clean_text(a["href"])

                if not href:
                    continue

                absolute_href = urljoin(base_url, href)

                if "see who works here" in link_text:
                    links["linkedin_url"] = absolute_href
                elif "check company site" in link_text:
                    links["company_website"] = absolute_href

    jobs_link = card.find("a", string=lambda s: s and "View Jobs" in s)
    if jobs_link and jobs_link.get("href"):
        links["jobs_url"] = urljoin(base_url, clean_text(jobs_link["href"]))

    return links


def parse_startup_card(card: Tag) -> Optional[Dict[str, object]]:
    name = extract_startup_name(card)
    what_they_do_lines = get_section_lines(card, "What they do:")
    quick_fact_lines = get_section_lines(card, "Quick facts:")

    parsed_wtd = parse_what_they_do(what_they_do_lines)
    facts = parse_quick_facts(quick_fact_lines)
    action_links = extract_action_links(card)

    startup = {
        "name": name,
        "description": parsed_wtd["description"],
        "tags": parsed_wtd["tags"],
        "hq": facts["hq"],
        "linkedin_url": action_links["linkedin_url"],
        "company_website": action_links["company_website"],
        "jobs_url": action_links["jobs_url"],
    }

    if not startup["name"] and not startup["description"]:
        return None

    return startup


def is_relevant(startup: Dict[str, object]) -> bool:
    tags_text = " ".join(startup.get("tags", [])) if isinstance(startup.get("tags"), list) else ""
    haystack = normalize(
        " ".join(
            [
                str(startup.get("name", "")),
                str(startup.get("description", "")),
                tags_text,
                str(startup.get("hq", "")),
            ]
        )
    )
    return any(keyword in haystack for keyword in ALLOWED_KEYWORDS)


def dedupe_startups(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    seen = set()
    unique = []

    for row in rows:
        key = normalize(str(row.get("name", "")))
        if key and key not in seen:
            seen.add(key)
            unique.append(row)

    return unique


def find_candidate_cards(soup: BeautifulSoup) -> List[Tag]:
    candidate_cards = []

    for card in soup.select("div.card.card-body"):
        text = card.get_text(" ", strip=True)
        if "What they do:" in text and "Quick facts:" in text:
            candidate_cards.append(card)

    return candidate_cards


def scrape_single_page(page_url: str) -> List[Dict[str, object]]:
    html = get_page_html(page_url)
    soup = BeautifulSoup(html, "html.parser")

    cards = find_candidate_cards(soup)

    startups = []
    for card in cards:
        parsed = parse_startup_card(card)
        if parsed and is_relevant(parsed):
            startups.append(parsed)

    return dedupe_startups(startups)


def scrape_startups(
    base_url: str,
    limit: Optional[int] = None,
    max_pages: Optional[int] = None,
) -> Dict[str, object]:
    all_startups = []
    pages_scraped = 0
    stop_reason = "unknown"
    page = 1

    while True:
        if max_pages is not None and page > max_pages:
            stop_reason = "max_pages_reached"
            break

        page_url = build_page_url(base_url, page)
        page_startups = scrape_single_page(page_url)
        pages_scraped += 1

        if not page_startups:
            stop_reason = "empty_page"
            break

        all_startups.extend(page_startups)

        if limit is not None and len(all_startups) >= limit:
            stop_reason = "limit_reached"
            all_startups = all_startups[:limit]
            break

        page += 1

    all_startups = dedupe_startups(all_startups)

    return {
        "items": all_startups,
        "pages_scraped": pages_scraped,
        "last_page_checked": page,
        "stop_reason": stop_reason,
    }


@app.get("/")
def root():
    return {"message": "FastAPI scraper is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/startups")
def get_startups(
    url: str = Query(..., description="Base page to scrape"),
    limit: Optional[int] = Query(None, ge=1, description="Optional max number of startups to return"),
    max_pages: Optional[int] = Query(None, ge=1, description="Optional safety cap on number of pages to scrape"),
):
    result = scrape_startups(base_url=url, limit=limit, max_pages=max_pages)

    return {
        "count": len(result["items"]),
        "pages_scraped": result["pages_scraped"],
        "last_page_checked": result["last_page_checked"],
        "stop_reason": result["stop_reason"],
        "items": result["items"],
    }