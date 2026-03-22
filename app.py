import re
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI, Query


app = FastAPI()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
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


def parse_startup_card(card: Tag) -> Optional[Dict[str, object]]:
    name = extract_startup_name(card)

    what_they_do_lines = get_section_lines(card, "What they do:")
    quick_fact_lines = get_section_lines(card, "Quick facts:")

    parsed_wtd = parse_what_they_do(what_they_do_lines)
    facts = parse_quick_facts(quick_fact_lines)

    startup = {
        "name": name,
        "description": parsed_wtd["description"],
        "tags": parsed_wtd["tags"],
        "hq": facts["hq"],
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

    for tag in soup.find_all(["div", "section", "article"]):
        text = tag.get_text(" ", strip=True)
        if "What they do:" in text and "Quick facts:" in text:
            candidate_cards.append(tag)

    unique = []
    seen_texts = set()

    for card in candidate_cards:
        text_key = clean_text(card.get_text(" ", strip=True))
        if text_key and text_key not in seen_texts:
            seen_texts.add(text_key)
            unique.append(card)

    return unique


def scrape_startups(url: str, limit: Optional[int] = None) -> List[Dict[str, object]]:
    html = get_page_html(url)
    soup = BeautifulSoup(html, "html.parser")

    cards = find_candidate_cards(soup)

    startups = []
    for card in cards:
        parsed = parse_startup_card(card)
        if parsed and is_relevant(parsed):
            startups.append(parsed)

    startups = dedupe_startups(startups)

    if limit is not None:
        return startups[:limit]

    return startups


@app.get("/")
def root():
    return {"message": "FastAPI scraper is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/startups")
def get_startups(
    url: str = Query(..., description="Page to scrape"),
    limit: Optional[int] = Query(None, ge=1, description="Optional max number of startups to return"),
):
    startups = scrape_startups(url=url, limit=limit)
    return {
        "count": len(startups),
        "items": startups,
    }