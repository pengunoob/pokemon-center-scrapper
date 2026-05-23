#!/usr/bin/env python3
"""Find Pokemon Center TCG products that are not marked out of stock.

This script reads public Pokemon Center listing pages and reports products that
do not contain common out-of-stock labels such as "sold out", "out of stock",
or "unavailable".
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


DEFAULT_URL = "https://www.pokemoncenter.com/category/tcg-cards"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

PRICE_RE = re.compile(r"\$\s?\d+(?:,\d{3})*(?:\.\d{2})?")
POKEMON_TITLE_RE = re.compile(
    r"(Pok(?:e|\u00e9)mon\s+(?:TCG|Trading Card Game)[^$]{0,220}?)(?:\$|sold out|out of stock|unavailable|$)",
    re.IGNORECASE,
)
OUT_OF_STOCK_MARKERS = (
    "sold out",
    "out of stock",
    "unavailable",
    "currently unavailable",
    "not available",
)
BLOCK_MARKERS = (
    "incapsula",
    "incapsula incident",
    "imperva",
    "access denied",
    "pardon our interruption",
    "request unsuccessful",
    "virtual queue",
    "waiting room",
)
NOISE_PHRASES = (
    "add to cart",
    "quick shop",
    "view details",
    "new release",
    "best seller",
)


@dataclass(frozen=True)
class Product:
    title: str
    price: str
    url: str
    page: int
    status: str


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


class ListingParser(HTMLParser):
    """Small text/link extractor for product listing pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[Token] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        attributes = {name.lower(): value for name, value in attrs if value is not None}
        if tag == "a" and is_product_link(attributes.get("href")):
            self.tokens.append(Token("link", attributes["href"]))
        elif tag == "img":
            alt = attributes.get("alt")
            if alt:
                self.tokens.append(Token("text", alt))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = normalize(data)
        if text:
            self.tokens.append(Token("text", text))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch(url: str, timeout: float, user_agent: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def looks_blocked(html: str) -> bool:
    lower_html = html.lower()
    return any(marker in lower_html for marker in BLOCK_MARKERS)


def is_product_link(href: str | None) -> bool:
    if not href:
        return False
    lowered = href.lower()
    return "/product/" in lowered and not lowered.startswith("#")


def text_between(tokens: list[Token], start: int, end: int) -> str:
    return normalize(" ".join(token.value for token in tokens[start:end] if token.kind == "text"))


def clean_text(text: str) -> str:
    text = normalize(text)
    for phrase in NOISE_PHRASES:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    for marker in OUT_OF_STOCK_MARKERS:
        text = re.sub(re.escape(marker), " ", text, flags=re.IGNORECASE)
    text = PRICE_RE.sub(" ", text)
    return normalize(text).strip(" -|")


def title_from_text(text: str, product_url: str) -> str:
    cleaned = clean_text(text)
    match = POKEMON_TITLE_RE.search(text)
    if match:
        title = clean_text(match.group(1))
        if len(title) >= 5:
            return title

    if 5 <= len(cleaned) <= 180 and "pokemon center official site" not in cleaned.lower():
        return cleaned

    return title_from_url(product_url)


def title_from_url(product_url: str) -> str:
    parts = [part for part in urlparse(product_url).path.split("/") if part]
    if "product" in parts and len(parts) >= 2:
        slug = parts[-1]
    else:
        slug = parts[-1] if parts else "pokemon-center-product"

    slug = unquote(slug)
    slug = re.sub(r"^\d+[-_]", "", slug)
    slug = slug.replace("-", " ").replace("_", " ")
    title = " ".join(word.upper() if word.lower() in {"tcg", "vmax", "ex"} else word.capitalize() for word in slug.split())
    return title or "Pokemon Center product"


def price_from(text: str) -> str:
    match = PRICE_RE.search(text)
    return match.group(0).replace(" ", "") if match else ""


def is_out_of_stock(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in OUT_OF_STOCK_MARKERS)


def parse_products(html: str, page_url: str, page: int) -> list[Product]:
    parser = ListingParser()
    parser.feed(html)

    link_indexes = [index for index, token in enumerate(parser.tokens) if token.kind == "link"]
    contexts_by_url: dict[str, dict[str, list[str]]] = {}

    for position, token_index in enumerate(link_indexes):
        href = parser.tokens[token_index].value
        product_url = urljoin(page_url, href).split("?")[0]

        previous_index = max(0, token_index - 3)
        next_index = link_indexes[position + 1] if position + 1 < len(link_indexes) else min(len(parser.tokens), token_index + 24)
        before_link = text_between(parser.tokens, previous_index, token_index)
        after_link = text_between(parser.tokens, token_index + 1, next_index)
        before_status = before_link if is_out_of_stock(before_link) else ""

        contexts = contexts_by_url.setdefault(product_url, {"status": [], "title": []})
        if before_status or after_link:
            contexts["status"].append(normalize(f"{before_status} {after_link}"))
        if after_link:
            contexts["title"].append(after_link)

    products: list[Product] = []
    for product_url, contexts in contexts_by_url.items():
        status_text = normalize(" ".join(contexts["status"]))
        title_text = normalize(" ".join(contexts["title"])) or status_text
        if is_out_of_stock(status_text):
            continue

        products.append(
            Product(
                title=title_from_text(title_text, product_url),
                price=price_from(title_text or status_text),
                url=product_url,
                page=page,
                status="not marked out of stock",
            )
        )

    return products


def filter_keywords(products: list[Product], keywords: list[str]) -> list[Product]:
    if not keywords:
        return products

    lowered_keywords = [keyword.lower() for keyword in keywords]
    return [
        product
        for product in products
        if any(keyword in product.title.lower() for keyword in lowered_keywords)
    ]


def scrape(args: argparse.Namespace) -> list[Product]:
    all_products: list[Product] = []
    seen_urls: set[str] = set()
    parsed_any_listing = False

    for page in range(1, args.pages + 1):
        page_url = build_page_url(args.url, page)
        if args.verbose:
            print(f"Checking page {page}: {page_url}", file=sys.stderr)

        html = fetch(page_url, args.timeout, args.user_agent)
        if looks_blocked(html):
            raise RuntimeError(
                "Pokemon Center returned a bot-protection, queue, or access-denied page. "
                "The scraper stops here instead of trying to bypass it."
            )

        page_products = filter_keywords(parse_products(html, page_url, page), args.keyword)
        if page_products:
            parsed_any_listing = True

        new_count = 0
        for product in page_products:
            if product.url in seen_urls:
                continue
            seen_urls.add(product.url)
            all_products.append(product)
            new_count += 1

        if args.verbose:
            print(f"Found {new_count} candidate product(s) on page {page}.", file=sys.stderr)

        if page > 1 and new_count == 0 and args.stop_on_empty:
            break

        if page != args.pages:
            time.sleep(args.delay)

    if not parsed_any_listing and not all_products:
        print(
            "No candidate products were parsed. The page structure may have changed, "
            "or every product on the checked pages is marked out of stock.",
            file=sys.stderr,
        )

    return all_products


def print_products(products: list[Product]) -> None:
    if not products:
        print("No Pokemon Center TCG products were found that are not marked out of stock.")
        return

    print(f"Found {len(products)} Pokemon Center TCG product(s) not marked out of stock:\n")
    for index, product in enumerate(products, start=1):
        price = f" - {product.price}" if product.price else ""
        print(f"{index}. {product.title}{price}")
        print(f"   {product.url}")
        print(f"   Page {product.page}; status: {product.status}")


def write_csv(path: str, products: list[Product]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["title", "price", "url", "page", "status"])
        writer.writeheader()
        for product in products:
            writer.writerow(asdict(product))


def write_json(path: str, products: list[Product]) -> None:
    with open(path, "w", encoding="utf-8") as json_file:
        json.dump([asdict(product) for product in products], json_file, indent=2)
        json_file.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Pokemon Center TCG listing pages for products not marked out of stock."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Listing URL to scrape. Default: {DEFAULT_URL}")
    parser.add_argument("--pages", type=int, default=20, help="Number of listing pages to check.")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between page requests, in seconds.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout, in seconds.")
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Only keep products whose title contains this keyword. Can be used more than once.",
    )
    parser.add_argument("--csv", help="Optional path to save results as CSV.")
    parser.add_argument("--json", help="Optional path to save results as JSON.")
    parser.add_argument(
        "--stop-on-empty",
        action="store_true",
        help="Stop after a page has no new candidate products.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print page-by-page progress to stderr.")
    parser.add_argument("--user-agent", default=USER_AGENT, help="User-Agent header for page requests.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.pages < 1:
        print("--pages must be at least 1.", file=sys.stderr)
        return 2
    if args.delay < 0:
        print("--delay cannot be negative.", file=sys.stderr)
        return 2

    try:
        products = scrape(args)
    except HTTPError as exc:
        print(f"HTTP error while fetching Pokemon Center: {exc}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Network error while fetching Pokemon Center: {exc}", file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(f"Timed out while fetching Pokemon Center: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print_products(products)

    if args.csv:
        write_csv(args.csv, products)
        print(f"\nSaved CSV results to {args.csv}")
    if args.json:
        write_json(args.json, products)
        print(f"\nSaved JSON results to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
