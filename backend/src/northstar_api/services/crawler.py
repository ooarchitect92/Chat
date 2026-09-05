from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TypedDict
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

MAX_PAGES = 300
REQUEST_TIMEOUT = 15

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NEXORA-Bot/1.0)"}


class CrawledPage(TypedDict):
    url: str
    title: str
    text: str
    links: list[str]


def normalize_url(url: str) -> str:
    """Remove fragments and normalize trailing slashes."""
    url, _ = urldefrag(url)

    parsed = urlparse(url)

    path = parsed.path.rstrip("/")

    if not path:
        path = "/"

    return f"{parsed.scheme}://{parsed.netloc}{path}"


def is_same_domain(base_url: str, target_url: str) -> bool:
    """Allow only URLs belonging to the starting domain."""
    base_domain = urlparse(base_url).netloc.lower()
    target_domain = urlparse(target_url).netloc.lower()

    return base_domain == target_domain


def is_valid_page_url(url: str) -> bool:
    """Allow normal HTTP(S) pages and ignore downloadable/non-HTML files."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False

    ignored_extensions = (
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".zip",
        ".rar",
        ".mp4",
        ".mp3",
        ".avi",
        ".mov",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    )

    path = parsed.path.lower()

    if path.endswith(ignored_extensions):
        return False

    return True


def crawl_page(url: str) -> CrawledPage:
    """
    Download one webpage and extract:
    - URL
    - title
    - visible text
    - same-page links for further crawling
    """
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers=HEADERS,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove unwanted HTML content.
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    # Extract visible text.
    raw_lines = soup.get_text(
        "\n",
        strip=True,
    ).splitlines()

    lines: list[str] = []

    for line in raw_lines:
        line = line.strip()

        if not line:
            continue

        # Remove consecutive duplicate lines.
        if not lines or line != lines[-1]:
            lines.append(line)

    text = "\n".join(lines)

    links: list[str] = []

    for link in soup.find_all("a", href=True):
        href = str(link["href"])

        full_url = urljoin(url, href)
        full_url = normalize_url(full_url)

        if is_valid_page_url(full_url):
            links.append(full_url)

    links = list(dict.fromkeys(links))

    return {
        "url": url,
        "title": title,
        "text": text,
        "links": links,
    }


def discover_sitemap(base_url: str) -> list[str]:
    """
    Look for /sitemap.xml on the starting domain.
    Return same-domain page URLs found in the sitemap.
    """
    parsed = urlparse(base_url)

    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    try:
        response = requests.get(
            sitemap_url,
            timeout=10,
            headers=HEADERS,
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "xml",
        )

        urls: list[str] = []

        for loc in soup.find_all("loc"):
            url = loc.get_text(strip=True)

            if is_valid_page_url(url) and is_same_domain(base_url, url):
                urls.append(normalize_url(url))

        return list(dict.fromkeys(urls))

    except Exception as error:
        print("SITEMAP ERROR:", error)
        return []


def crawl_website(
    start_url: str,
    max_pages: int = MAX_PAGES,
) -> list[CrawledPage]:
    """
    Crawl a website using:
    1. sitemap discovery when available
    2. same-domain link discovery as a fallback/extension
    """
    start_url = normalize_url(start_url)

    visited: set[str] = set()
    queue: list[str] = [start_url]
    pages: list[CrawledPage] = []

    sitemap_urls = discover_sitemap(start_url)

    if sitemap_urls:
        print(f"Sitemap discovered {len(sitemap_urls)} URLs.")

        for url in sitemap_urls:
            if len(queue) >= max_pages:
                break

            if url not in queue:
                queue.append(url)

    else:
        print("No sitemap found. Using link discovery.")

    while queue and len(pages) < max_pages:
        current_url = queue.pop(0)

        if current_url in visited:
            continue

        visited.add(current_url)

        if not is_same_domain(
            start_url,
            current_url,
        ):
            continue

        if not is_valid_page_url(current_url):
            continue

        print(f"[{len(pages) + 1}/{max_pages}] Crawling: {current_url}")

        try:
            page = crawl_page(current_url)

            pages.append(page)

            for link in page["links"]:
                if link in visited:
                    continue

                if not is_same_domain(
                    start_url,
                    link,
                ):
                    continue

                if not is_valid_page_url(link):
                    continue

                if link not in queue:
                    queue.append(link)

        except Exception as error:
            print(f"FAILED: {current_url}")
            print(f"ERROR: {error}")

    print("\nCrawling completed.")
    print(f"Pages successfully crawled: {len(pages)}")

    return pages


def save_pages(
    pages: list[CrawledPage],
    filename: str = "website.json",
) -> str:
    """Save crawled pages to data/raw."""
    output_dir = Path("data/raw")
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = output_dir / filename

    with open(
        output_file,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            pages,
            file,
            indent=2,
            ensure_ascii=False,
        )

    return str(output_file)


def take_screenshot(
    url: str,
    filename: str,
) -> str:
    """
    Render a webpage with Playwright and save
    a full-page PNG screenshot.
    """
    output_dir = Path(tempfile.gettempdir()) / "northstar_screenshots"

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = output_dir / filename

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 900,
            }
        )

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            # Give JavaScript-rendered content time to appear.
            page.wait_for_timeout(3000)

            page.screenshot(
                path=str(output_file),
                full_page=True,
            )

        finally:
            browser.close()

    return str(output_file)
