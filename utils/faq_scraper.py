import re

import requests


JINA_BASE = "https://r.jina.ai"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_JINA_RENDER_TIMEOUT_SECONDS = 180
_JINA_REQUEST_TIMEOUT_SECONDS = 200
_JINA_CACHE_FALLBACK_TIMEOUT_SECONDS = 30
_FIRECRAWL_RENDER_TIMEOUT_MS = 120000
_FIRECRAWL_REQUEST_TIMEOUT_SECONDS = 135

_REMOVE_SELECTOR = ", ".join([
    "nav", "header", "footer", "aside",
    "#cart", ".cart", "[class*='cart']",
    "#header", "#footer", "#nav", "#sidebar",
    "[class*='sidebar']", "[class*='navigation']",
    "[class*='breadcrumb']", "[class*='cookie']",
    "[class*='popup']", "[class*='modal']",
    "[class*='newsletter']", "[class*='subscribe']",
    "[class*='related']", "[class*='recommended']",
    "[class*='upsell']", "[class*='cross-sell']",
    "form", "script", "style", "noscript", "iframe",
])

_COLLECTION_REMOVE_SELECTOR = ", ".join([
    "nav", "header", "footer",
    "#cart", ".cart", "[class*='cart']",
    "#header", "#footer", "#nav",
    "[class*='navigation']", "[class*='breadcrumb']", "[class*='cookie']",
    "[class*='popup']", "[class*='modal']",
    "[class*='newsletter']", "[class*='subscribe']",
    "[class*='related']", "[class*='recommended']",
    "[class*='upsell']", "[class*='cross-sell']",
    "script", "style", "noscript", "iframe",
])

_NOISE_LINE_PATTERNS = re.compile(
    r"^\s*("
    r"\$[\d,.]+|"
    r"Add to cart|Sold out|Sale price|"
    r"Regular price|Unit price|"
    r"Quantity must be|Adding product|"
    r"Please allow \d|"
    r"Pickup available|Usually ready|"
    r"Check availability|Service Center|"
    r"Skip to content|Log in|Sign in|"
    r"Search$|Menu$|Close$|"
    r"This page does not seem to contain|"
    r"\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"
    r")\s*$",
    re.IGNORECASE,
)

_COLLECTION_NOISE_LINE_PATTERNS = re.compile(
    r"^\s*("
    r"Add to cart|Sold out|Sale price|Regular price|Unit price|"
    r"Quantity must be|Adding product|"
    r"Please allow \d|"
    r"Pickup available|Usually ready|"
    r"Check availability|Service Center|"
    r"Skip to content|Log in|Sign in|"
    r"Search$|Menu$|Close$|Footer$|"
    r"\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"
    r")\s*$",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(
    r"(?:[$\u00a3\u20ac]\s?\d[\d,.]*(?:\.\d{2})?|\d[\d,.]*(?:\.\d{2})?\s?(?:USD|GBP|EUR))"
)
_PRODUCT_LINK_RE = re.compile(
    r"^\s*#{0,4}\s*(?:[-*]\s*)?\[(?P<name>[^\]]{3,})\]\(https?://[^\)]+\)\s*$"
)
_FILTER_LABELS = {
    "brand", "brands", "size", "sizes", "color", "colour", "colors", "colours",
    "price", "material", "materials", "style", "styles", "type", "types",
    "category", "categories", "availability", "product type", "fit", "capacity",
    "flavor", "flavour", "weight", "finish", "features",
}


def _score_paragraph(paragraph: str, min_words: int = 8) -> float:
    words = paragraph.split()
    if len(words) < min_words:
        return 0.0
    if len(re.findall(r"\[.+?\]\(https?://", paragraph)) > 2:
        return 0.0
    alpha_ratio = sum(character.isalpha() for character in paragraph) / max(len(paragraph), 1)
    if alpha_ratio < 0.5:
        return 0.0
    return len(words) * alpha_ratio


def is_ecommerce_collection_page(business_type: str, page_type: str) -> bool:
    if (business_type or "").strip().lower() != "ecommerce":
        return False
    normalised_page_type = (page_type or "").strip().lower()
    return "category" in normalised_page_type or "collection" in normalised_page_type


def _extract_title(text: str) -> str:
    title_match = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    return title_match.group(1).strip() if title_match else ""


def _normalise_lines(text: str, noise_pattern: re.Pattern) -> list[str]:
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line and not noise_pattern.match(line):
            lines.append(line)
    return lines


def _extract_collection_products(lines: list[str], limit: int = 30) -> list[dict]:
    products = []
    for index, line in enumerate(lines):
        match = _PRODUCT_LINK_RE.match(line)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        price = ""
        for following_line in lines[index + 1:index + 5]:
            price_match = _PRICE_RE.search(following_line)
            if price_match:
                price = price_match.group(0).strip()
                break
            if _PRODUCT_LINK_RE.match(following_line):
                break
        if name and not any(item["name"].lower() == name.lower() for item in products):
            products.append({"name": name, "price": price})
        if len(products) >= limit:
            break
    return products


def _extract_collection_filters(lines: list[str]) -> dict[str, list[str]]:
    filters = {}
    current_filter = None
    found_filter_heading = False
    for line in lines:
        clean = re.sub(r"^#+\s*", "", line).strip()
        clean = re.sub(r"^\*\s*", "", clean).strip()
        clean_key = clean.lower().rstrip(":")
        if clean_key == "filters":
            found_filter_heading = True
            current_filter = None
            continue
        if _PRODUCT_LINK_RE.match(line):
            current_filter = None
            continue
        if clean_key in _FILTER_LABELS:
            found_filter_heading = True
            current_filter = clean.rstrip(":")
            filters.setdefault(current_filter, [])
            continue
        if not found_filter_heading or not current_filter:
            continue
        if _PRICE_RE.search(clean) and current_filter.lower() != "price":
            continue
        if len(clean) > 40:
            current_filter = None
            continue
        if clean and clean not in filters[current_filter]:
            filters[current_filter].append(clean)
    return {name: values[:12] for name, values in filters.items() if values}


def _build_collection_context(text: str, max_chars: int) -> tuple[str, str]:
    title = _extract_title(text)
    lines = _normalise_lines(text, _COLLECTION_NOISE_LINE_PATTERNS)
    products = _extract_collection_products(lines)
    filters = _extract_collection_filters(lines)

    excerpt_text = "\n".join(lines)
    excerpt_text = re.sub(r"^\s*\*\s+\[.+?\]\(https?://.+?\)\s*$", "", excerpt_text, flags=re.MULTILINE)
    excerpt_text = re.sub(r"^#{1,4}\s+\[.+?\]\(https?://.+?\)\s*$", "", excerpt_text, flags=re.MULTILINE)
    excerpt_text = re.sub(r"\n{3,}", "\n\n", excerpt_text).strip()

    excerpt_parts = []
    chars_used = 0
    for paragraph in re.split(r"\n{2,}", excerpt_text):
        if chars_used >= max_chars // 2:
            break
        if _score_paragraph(paragraph, min_words=5) > 0 or paragraph.strip().startswith("#"):
            excerpt_parts.append(paragraph)
            chars_used += len(paragraph)

    sections = ["COLLECTION CONTEXT"]
    if products:
        sections.append(
            "Products found:\n" + "\n".join(
                f"- {item['name']} | {item['price']}" if item["price"] else f"- {item['name']}"
                for item in products
            )
        )
    if filters:
        sections.append(
            "Filters found:\n" + "\n".join(
                f"- {name}: {', '.join(values)}"
                for name, values in filters.items()
            )
        )
    if excerpt_parts:
        sections.append("Page excerpt:\n" + "\n\n".join(excerpt_parts))

    return "\n\n".join(sections).strip()[:max_chars].strip(), title


def _scrape_result(content: str, title: str, raw_chars: int, mode: str, error: str = "") -> dict:
    return {
        "content": content,
        "title": title,
        "success": bool(content),
        "error": error,
        "mode": mode,
        "raw_chars": raw_chars,
        "cleaned_chars": len(content),
    }


def _process_reader_text(text: str, max_chars: int, mode: str = "default") -> dict:
    text = (text or "").strip()
    if not text:
        return _scrape_result("", "", 0, mode, "Jina returned empty content")

    raw_chars = len(text)
    if mode == "ecommerce_collection":
        content, title = _build_collection_context(text, max_chars)
        if not content or content == "COLLECTION CONTEXT":
            return _scrape_result(
                "", title, raw_chars, mode, "No collection products, filters, or content found"
            )
        return _scrape_result(content, title, raw_chars, mode)

    title = _extract_title(text)

    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"^\s*\*\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,4}\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    lines = [line for line in text.splitlines() if not _NOISE_LINE_PATTERNS.match(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return _scrape_result("", title, raw_chars, mode, "No content found after stripping boilerplate")

    result_paragraphs = []
    chars_used = 0
    for paragraph in re.split(r"\n{2,}", text):
        if chars_used >= max_chars:
            break
        if _score_paragraph(paragraph) > 0 or paragraph.strip().startswith("#"):
            result_paragraphs.append(paragraph)
            chars_used += len(paragraph)

    content = "\n\n".join(result_paragraphs).strip()
    if len(content) > max_chars:
        truncated = content[:max_chars]
        last_period = truncated.rfind(".")
        content = truncated[:last_period + 1].strip() if last_period > max_chars * 0.5 else truncated.strip()
    if not content:
        return _scrape_result("", title, raw_chars, mode, "No substantive content found after scoring")
    return _scrape_result(content, title, raw_chars, mode)


def _request_cached_snapshot(url: str, headers: dict):
    fallback_headers = dict(headers)
    fallback_headers.pop("X-No-Cache", None)
    fallback_headers.pop("X-Remove-Selector", None)
    fallback_headers.pop("X-Timeout", None)
    return requests.get(
        f"{JINA_BASE}/{url}",
        headers=fallback_headers,
        timeout=_JINA_CACHE_FALLBACK_TIMEOUT_SECONDS,
    )


def scrape_page_context(api_key: str, url: str, max_chars: int = 10000, mode: str = "default") -> dict:
    if not url:
        return {"content": "", "title": "", "success": False, "error": "No URL provided"}

    headers = {
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
        "X-With-Links-Summary": "false",
        "X-With-Images-Summary": "false",
        "X-Remove-Selector": _COLLECTION_REMOVE_SELECTOR if mode == "ecommerce_collection" else _REMOVE_SELECTOR,
        "X-No-Cache": "true",
        "X-Timeout": str(_JINA_RENDER_TIMEOUT_SECONDS),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response_source = "live"
    fallback_attempted = False
    try:
        try:
            response = requests.get(
                f"{JINA_BASE}/{url}",
                headers=headers,
                timeout=_JINA_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in (400, 422):
                headers.pop("X-Remove-Selector", None)
                response = requests.get(
                    f"{JINA_BASE}/{url}",
                    headers=headers,
                    timeout=_JINA_REQUEST_TIMEOUT_SECONDS,
                )
        except requests.exceptions.Timeout:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        if response_source == "live" and 500 <= response.status_code < 600:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        response.raise_for_status()
        result = _process_reader_text(response.text, max_chars, mode)
        if not result["success"] and response_source == "live":
            fallback_attempted = True
            cached_response = _request_cached_snapshot(url, headers)
            cached_response.raise_for_status()
            cached_result = _process_reader_text(cached_response.text, max_chars, mode)
            if cached_result["success"]:
                result = cached_result
                response_source = "cached_fallback"

        result["source"] = response_source
        return result
    except requests.exceptions.Timeout:
        suffix = " after cached fallback" if fallback_attempted else ""
        return {"content": "", "title": "", "success": False, "error": f"Request timed out{suffix}"}
    except requests.exceptions.HTTPError as error:
        suffix = " after cached fallback" if fallback_attempted else ""
        return {
            "content": "",
            "title": "",
            "success": False,
            "error": f"HTTP {error.response.status_code}{suffix}",
        }
    except requests.exceptions.RequestException as error:
        return {"content": "", "title": "", "success": False, "error": str(error)}
    except Exception as error:
        return {"content": "", "title": "", "success": False, "error": str(error)}


def _firecrawl_failure(message: str) -> dict:
    return {"content": "", "title": "", "success": False, "error": message, "source": "firecrawl"}


def _firecrawl_http_error(status_code: int) -> str:
    if status_code in (401, 403):
        return "Firecrawl authentication failed. Update the API key in Settings."
    if status_code == 402:
        return "Firecrawl credits are unavailable. Check the Firecrawl account."
    if status_code == 429:
        return "Firecrawl rate limit reached. Try again later."
    if status_code >= 500:
        return "Firecrawl is temporarily unavailable. Try again later."
    return "Firecrawl could not scrape this page."


def scrape_page_context_firecrawl(
    api_key: str,
    url: str,
    max_chars: int = 10000,
    mode: str = "default",
) -> dict:
    if not url:
        return _firecrawl_failure("No URL provided")
    if not api_key:
        return _firecrawl_failure("Firecrawl API key is not configured.")

    try:
        response = requests.post(
            FIRECRAWL_SCRAPE_URL,
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "onlyCleanContent": False,
                "maxAge": 0,
                "waitFor": 0,
                "timeout": _FIRECRAWL_RENDER_TIMEOUT_MS,
                "removeBase64Images": True,
                "blockAds": True,
                "proxy": "auto",
                "storeInCache": False,
            },
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=_FIRECRAWL_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        return _firecrawl_failure("Firecrawl request timed out.")
    except requests.exceptions.RequestException:
        return _firecrawl_failure("Firecrawl could not be reached. Try again later.")

    if not 200 <= response.status_code < 300:
        return _firecrawl_failure(_firecrawl_http_error(response.status_code))
    try:
        response_body = response.json()
    except ValueError:
        return _firecrawl_failure("Firecrawl returned an invalid response.")

    page_data = response_body.get("data") if isinstance(response_body, dict) else None
    if not isinstance(response_body, dict) or response_body.get("success") is not True or not isinstance(page_data, dict):
        return _firecrawl_failure("Firecrawl could not scrape this page.")

    markdown = (page_data.get("markdown") or "").strip()
    if not markdown:
        return _firecrawl_failure("Firecrawl returned empty content.")
    metadata = page_data.get("metadata") or {}
    metadata_title = metadata.get("title", "") if isinstance(metadata, dict) else ""
    reader_text = f"Title: {metadata_title}\n\n{markdown}" if metadata_title else markdown
    result = _process_reader_text(reader_text, max_chars, mode)
    if metadata_title and not result.get("title"):
        result["title"] = metadata_title
    result["source"] = "firecrawl"
    return result
