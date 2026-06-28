#!/usr/bin/env python3
"""Extract Joaquin Trincado communications from the Geocities archive."""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urldefrag, urljoin
from urllib.request import Request, urlopen


INDEX_URL = "https://www.geocities.ws/eme_dela_cu/indexjt.htm"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125 Safari/537.36"
)
OUT_PATH = Path("data/comunicaciones-joaquin-trincado.json")
DATE_LINE_RE = re.compile(
    r"\b(?:Lunes|Martes|Mi[eé]rcoles|Jueves|Viernes|S[aá]bado|Domingo)\s+"
    r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)
INDEX_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
YEAR_RE = re.compile(r"(19\d{2})")
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def clean_space(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_name(text: str) -> str:
    text = unquote(text or "")
    text = clean_space(text)
    return unicodedata.normalize("NFC", text)


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        body = response.read()
    return body.decode("windows-1252", errors="replace")


@dataclass
class IndexLink:
    year: int
    date_label: str
    href: str


class IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_td = False
        self.in_a = False
        self.td_text: list[str] = []
        self.link_text: list[str] = []
        self.current_href: str | None = None
        self.links: list[tuple[str, str, str]] = []
        self.records: list[IndexLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "td":
            if self.in_td:
                self.flush_td()
            self.in_td = True
            self.td_text = []
            self.links = []
        elif self.in_td and tag == "a":
            self.in_a = True
            self.current_href = attrs_dict.get("href")
            self.link_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.in_td and tag == "a":
            if self.current_href:
                self.links.append(
                    (
                        clean_space("".join(self.link_text)),
                        self.current_href,
                        clean_space("".join(self.td_text)),
                    )
                )
            self.in_a = False
            self.current_href = None
            self.link_text = []
        elif tag == "td" and self.in_td:
            self.flush_td()

    def handle_data(self, data: str) -> None:
        if self.in_td:
            self.td_text.append(data)
        if self.in_a:
            self.link_text.append(data)

    def flush_td(self) -> None:
        text = clean_space("".join(self.td_text))
        year_match = YEAR_RE.search(text)
        if year_match:
            year = int(year_match.group(1))
            for label, href, _ in self.links:
                self.records.append(IndexLink(year=year, date_label=label, href=href))
        self.in_td = False
        self.td_text = []
        self.links = []


@dataclass
class Block:
    tag: str
    text: str
    anchors: list[str] = field(default_factory=list)


class PageParser(HTMLParser):
    block_tags = {"h1", "h2", "h3", "h4", "p", "li", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.skip_depth = 0
        self.current_tag: str | None = None
        self.current_text: list[str] = []
        self.current_anchors: list[str] = []
        self.blocks: list[Block] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.block_tags:
            self.flush_block()
            self.current_tag = tag
            self.current_text = []
            self.current_anchors = []
        if tag == "br" and self.current_tag:
            self.current_text.append("\n")
        if tag == "a":
            name = attrs_dict.get("name") or attrs_dict.get("id")
            if name:
                self.current_anchors.append(normalize_name(name))

    def handle_endtag(self, tag: str) -> None:
        if tag == "body":
            self.flush_block()
            self.in_body = False
            return
        if not self.in_body:
            return
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == self.current_tag:
            self.flush_block()

    def handle_data(self, data: str) -> None:
        if self.in_body and not self.skip_depth and self.current_tag:
            self.current_text.append(data)

    def flush_block(self) -> None:
        if not self.current_tag:
            return
        text = clean_space("".join(self.current_text))
        if text or self.current_anchors:
            self.blocks.append(
                Block(tag=self.current_tag, text=text, anchors=self.current_anchors[:])
            )
        self.current_tag = None
        self.current_text = []
        self.current_anchors = []


def parse_index() -> list[IndexLink]:
    parser = IndexParser()
    parser.records = []
    parser.feed(fetch_text(INDEX_URL))
    return parser.records


def index_date(year: int, label: str) -> str | None:
    match = INDEX_DATE_RE.match(clean_space(label).lower())
    if not match:
        return None
    day, month = (int(match.group(1)), int(match.group(2)))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_spanish_date(text: str) -> str | None:
    match = DATE_LINE_RE.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month_name = normalize_name(match.group(2)).lower()
    month = MONTHS.get(month_name)
    if not month:
        return None
    year = int(match.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


def find_start(blocks: list[Block], fragment: str) -> int | None:
    target = normalize_name(fragment)
    for index, block in enumerate(blocks):
        if target in block.anchors:
            return index
    return None


def find_end(blocks: list[Block], start: int) -> int:
    for index in range(start + 1, len(blocks)):
        if blocks[index].anchors:
            return index
    return len(blocks)


def title_from(blocks: list[Block]) -> str | None:
    if not blocks:
        return None
    first = blocks[0].text
    first_line = clean_space(first.split("\n", 1)[0])
    return first_line.rstrip(".") or None


def medium_from(lines: Iterable[str]) -> str | None:
    for line in lines:
        if re.search(r"\bM[eé]dium\b", line, flags=re.IGNORECASE):
            return clean_space(line)
    return None


def text_from(blocks: list[Block]) -> str | None:
    paragraphs: list[str] = []
    for index, block in enumerate(blocks):
        lines = [clean_space(line) for line in block.text.split("\n") if clean_space(line)]
        if index == 0:
            lines = [
                line
                for line in lines
                if line.rstrip(".") != title_from(blocks)
                and not DATE_LINE_RE.search(line)
                and not re.search(r"\bM[eé]dium\b", line, flags=re.IGNORECASE)
            ]
        elif block.tag in {"h2", "h3", "h4"} and (
            any(DATE_LINE_RE.search(line) for line in lines)
            or any(re.search(r"\bM[eé]dium\b", line, flags=re.IGNORECASE) for line in lines)
        ):
            continue
        if lines:
            paragraphs.append(clean_space("\n".join(lines)))
    full_text = "\n\n".join(paragraphs)
    return full_text or None


def extract() -> dict:
    entries = parse_index()
    pages: dict[str, list[Block]] = {}
    page_errors: dict[str, str] = {}
    communications = []

    for ordinal, entry in enumerate(entries, start=1):
        absolute = urljoin(INDEX_URL, entry.href)
        page_url, fragment = urldefrag(absolute)
        if page_url not in pages and page_url not in page_errors:
            try:
                parser = PageParser()
                parser.feed(fetch_text(page_url))
                pages[page_url] = parser.blocks
                time.sleep(0.05)
            except (HTTPError, URLError, TimeoutError) as exc:
                page_errors[page_url] = str(exc)

        blocks: list[Block] = []
        error = page_errors.get(page_url)
        if page_url in pages:
            start = find_start(pages[page_url], fragment)
            if start is None:
                error = f"Anchor not found: {normalize_name(fragment)}"
            else:
                end = find_end(pages[page_url], start)
                blocks = pages[page_url][start:end]

        all_lines = [
            clean_space(line)
            for block in blocks
            for line in block.text.split("\n")
            if clean_space(line)
        ]
        source_date = parse_spanish_date("\n".join(all_lines))
        date = source_date or index_date(entry.year, entry.date_label)
        year = int(date[:4]) if date else entry.year
        title = title_from(blocks)
        medium = medium_from(all_lines)
        text = text_from(blocks)
        normalized_fragment = normalize_name(fragment)

        communications.append(
            {
                "id": f"jt-{ordinal:03d}",
                "date": date,
                "dateLabel": clean_space(entry.date_label),
                "year": year,
                "indexYear": entry.year,
                "title": title,
                "medium": medium,
                "sourceUrl": absolute,
                "sourceIssueUrl": page_url,
                "sourceAnchor": normalized_fragment,
                "text": text,
                "needsReview": bool(error) or date is None or text is None,
                "reviewNote": error,
            }
        )

    return {
        "sourceIndexUrl": INDEX_URL,
        "generatedFrom": "scripts/extract_comunicaciones.py",
        "encoding": "windows-1252",
        "communicationCount": len(communications),
        "communications": communications,
    }


def main() -> int:
    data = extract()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {data['communicationCount']} communications to {OUT_PATH}")
    needs_review = [
        item for item in data["communications"] if item.get("needsReview")
    ]
    print(f"{len(needs_review)} entries marked for review")
    for item in needs_review:
        print(f"- {item['id']} {item['dateLabel']} {item['sourceUrl']} {item['reviewNote']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
