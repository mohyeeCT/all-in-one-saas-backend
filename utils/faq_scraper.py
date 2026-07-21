import re
from urllib.parse import urlparse

import requests


JINA_BASE = "https://r.jina.ai"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
AIO_OWNED_PAGE_CAPTURE_VERSION = "current-aio-owned-page-capture-v2"
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

_JINA_DIAGNOSTIC_LINE_PATTERNS = re.compile(
    r"^\s*(?:"
    r"Warning:\s*This page contains iframe.*|"
    r"Markdown Content:|"
    r"Images:|"
    r"Links/Buttons:|"
    r"This page does not seem to contain any (?:images|buttons/links)\.?"
    r")\s*$",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(
    r"(?:[$\u00a3\u20ac]\s?\d[\d,.]*(?:\.\d{2})?|\d[\d,.]*(?:\.\d{2})?\s?(?:USD|GBP|EUR))"
)
_PRODUCT_LINK_RE = re.compile(
    r"^\s*#{0,4}\s*(?:[-*]\s*)?\[(?P<name>[^\]]{3,})\]"
    r"\((?P<url>https?://[^\)]+)\)\s*$"
)
_AIO_CAPTURE_MAX_CHARS = 6_800
_AIO_CAPTURE_SPARSE_CHARS = 5_000
_AIO_CAPTURE_BLOCK_MAX_CHARS = 760
_AIO_LINK_RE = re.compile(r"!?\[(?P<label>[^\]]+)\]\((?P<url>https?://[^\)]+)\)")
_AIO_NAVIGATION_LABELS = {
    "about", "about us", "account", "blog", "cart", "checkout", "contact",
    "contact us", "faq", "faqs", "home", "log in", "login", "menu", "news",
    "privacy", "search", "shop", "shop all", "sign in", "terms",
}
_AIO_LOW_SIGNAL_SHORT_LABELS = {
    "click here", "learn more", "read more", "see more", "view all",
}
_AIO_NAVIGATION_PATH_PREFIXES = (
    "/account", "/cart", "/checkout", "/policies", "/search",
)
_AIO_SOCIAL_HOSTS = (
    "facebook.com", "instagram.com", "linkedin.com", "pinterest.com",
    "threads.net", "tiktok.com", "twitter.com", "x.com", "youtube.com",
)
_AIO_METADATA_LINE_RE = re.compile(
    r"^\s*(?:Title|URL Source|Published Time|Markdown Content):\s*",
    re.IGNORECASE,
)
_AIO_UI_NOISE_LINE_RE = re.compile(
    r"^\s*(?:Add to cart|Sale price|Regular price|Unit price|"
    r"Quantity must be|Adding product|Please allow \d|"
    r"Skip to content|Log in|Sign in|"
    r"Search|Menu|Close|Footer|Follow us)\s*$",
    re.IGNORECASE,
)
_AIO_BOILERPLATE_TEXT_RE = re.compile(
    r"\b(?:all rights reserved|cookie settings|follow us|newsletter|"
    r"powered by|privacy policy|subscribe|terms of service)\b",
    re.IGNORECASE,
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


def _strip_jina_diagnostics(text: str) -> tuple[str, bool]:
    lines = []
    removed = False
    for line in text.splitlines():
        if _JINA_DIAGNOSTIC_LINE_PATTERNS.match(line):
            removed = True
            continue
        lines.append(line)
    return "\n".join(lines).strip(), removed


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


def _is_navigation_link(label: str, url: str) -> bool:
    normalized_label = re.sub(r"\s+", " ", label).strip().casefold()
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/").casefold() or "/"
    hostname = (parsed.hostname or "").casefold()
    return bool(
        normalized_label in _AIO_NAVIGATION_LABELS
        or path == "/"
        or any(path == prefix or path.startswith(prefix + "/") for prefix in _AIO_NAVIGATION_PATH_PREFIXES)
        or any(
            hostname == social_host or hostname.endswith("." + social_host)
            for social_host in _AIO_SOCIAL_HOSTS
        )
    )


def _looks_like_product_url(url: str) -> bool:
    path_segments = {
        segment
        for segment in urlparse(url).path.casefold().split("/")
        if segment
    }
    return bool(path_segments.intersection({"item", "items", "p", "product", "products"}))


def _is_collection_navigation_link(label: str, url: str) -> bool:
    path_segments = {
        segment
        for segment in urlparse(url).path.casefold().split("/")
        if segment
    }
    return bool(
        _is_navigation_link(label, url)
        or (
            path_segments.intersection({"categories", "category", "collections", "collection"})
            and not _looks_like_product_url(url)
        )
    )


def _extract_aio_collection_products(lines: list[str], limit: int = 30) -> list[dict]:
    products = []
    for index, line in enumerate(lines):
        match = _PRODUCT_LINK_RE.match(line)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        url = match.group("url").strip()
        price = ""
        for following_line in lines[index + 1:index + 5]:
            price_match = _PRICE_RE.search(following_line)
            if price_match:
                price = price_match.group(0).strip()
                break
            if _PRODUCT_LINK_RE.match(following_line):
                break
        if _is_collection_navigation_link(name, url):
            continue
        if not (_looks_like_product_url(url) or price):
            continue
        if name and not any(item["name"].casefold() == name.casefold() for item in products):
            products.append({"name": name, "url": url, "price": price})
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


def _aio_visible_link_text(match: re.Match) -> str:
    return re.sub(r"\s+", " ", match.group("label")).strip()


def _split_aio_capture_block(value: str) -> list[str]:
    remaining = value.strip()
    chunks = []
    while remaining:
        if len(remaining) <= _AIO_CAPTURE_BLOCK_MAX_CHARS:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, _AIO_CAPTURE_BLOCK_MAX_CHARS + 1)
        if cut < _AIO_CAPTURE_BLOCK_MAX_CHARS // 2:
            cut = remaining.rfind(" ", 0, _AIO_CAPTURE_BLOCK_MAX_CHARS + 1)
        if cut < _AIO_CAPTURE_BLOCK_MAX_CHARS // 2:
            cut = _AIO_CAPTURE_BLOCK_MAX_CHARS
        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].lstrip()
    return chunks


def _clean_aio_capture_paragraph(paragraph: str, counters: dict) -> str:
    cleaned_lines = []
    original_had_link = False
    for raw_line in paragraph.splitlines():
        line = raw_line.strip()
        if not line or _AIO_METADATA_LINE_RE.match(line):
            continue
        if _AIO_UI_NOISE_LINE_RE.match(line):
            counters["ui_noise_lines_rejected"] += 1
            continue

        full_link = _PRODUCT_LINK_RE.match(line)
        if full_link and _is_navigation_link(
            full_link.group("name"),
            full_link.group("url"),
        ):
            counters["navigation_links_rejected"] += 1
            continue

        link_matches = list(_AIO_LINK_RE.finditer(line))
        if link_matches:
            original_had_link = True
            if all(
                _is_navigation_link(match.group("label"), match.group("url"))
                for match in link_matches
            ) and not _AIO_LINK_RE.sub("", line).strip(" -*#|:"):
                counters["navigation_links_rejected"] += len(link_matches)
                continue
            counters["visible_link_labels_retained"] += len(link_matches)
            line = _AIO_LINK_RE.sub(_aio_visible_link_text, line)

        if line:
            cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    if not cleaned:
        counters["empty_blocks_rejected"] += 1
        return ""
    if cleaned.lstrip().startswith("#"):
        return cleaned

    words = re.findall(r"[^\W_]+(?:['\u2019-][^\W_]+)*", cleaned, re.UNICODE)
    alpha_ratio = sum(character.isalpha() for character in cleaned) / max(len(cleaned), 1)
    is_list = any(
        re.match(r"^\s*(?:[-+*]|\d+[.)])\s+\S", line)
        for line in cleaned.splitlines()
    )
    has_contact_or_price = bool(_PRICE_RE.search(cleaned)) or bool(
        re.search(r"(?:\+?\d[\d\s().-]{6,}\d|\b\S+@\S+\.\S+\b)", cleaned)
    )
    clear_boilerplate = bool(
        _AIO_BOILERPLATE_TEXT_RE.search(cleaned)
        and (
            original_had_link
            or re.search(
                r"\b(?:all rights reserved|cookie settings|powered by)\b",
                cleaned,
                re.IGNORECASE,
            )
        )
    )
    if clear_boilerplate and not has_contact_or_price:
        counters["low_signal_blocks_rejected"] += 1
        return ""
    short_sentence = bool(
        len(words) >= 2
        and alpha_ratio >= 0.5
        and re.search(r"[.!?]\s*$", cleaned)
    )
    short_card = bool(
        2 <= len(words) < 8
        and len(cleaned) <= 120
        and alpha_ratio >= 0.5
        and cleaned.casefold() not in _AIO_NAVIGATION_LABELS
        and cleaned.casefold() not in _AIO_LOW_SIGNAL_SHORT_LABELS
        and not _AIO_BOILERPLATE_TEXT_RE.search(cleaned)
    )
    if (len(words) >= 3 and alpha_ratio >= 0.35) or short_sentence:
        if len(words) < 8:
            counters["short_blocks_retained"] += 1
        return cleaned
    if short_card or has_contact_or_price:
        counters["short_blocks_retained"] += 1
        return cleaned
    if (original_had_link or is_list) and any(character.isalpha() for character in cleaned):
        counters["short_blocks_retained"] += 1
        return cleaned
    counters["low_signal_blocks_rejected"] += 1
    return ""


def _aio_capture_dedupe_key(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _aio_capture_signal_score(chunk: str, heading: str) -> int:
    words = re.findall(r"[^\W_]+(?:['\u2019-][^\W_]+)*", chunk, re.UNICODE)
    list_lines = sum(
        bool(re.match(r"^\s*(?:[-+*]|\d+[.)])\s+\S", line))
        for line in chunk.splitlines()
    )
    score = min(len(chunk), 400) + min(len(words), 100) * 2
    if heading:
        score += 160
    score += min(list_lines, 6) * 35
    if _PRICE_RE.search(chunk) or re.search(
        r"(?:\+?\d[\d\s().-]{6,}\d|\b\S+@\S+\.\S+\b)",
        chunk,
    ):
        score += 240
    if _AIO_BOILERPLATE_TEXT_RE.search(chunk):
        score -= 1_000
    return score


def _render_aio_capture_candidates(candidates: list[dict], selected: set[int]) -> str:
    parts = []
    rendered_section = None
    for index, candidate in enumerate(candidates):
        if index not in selected:
            continue
        if candidate["section"] != rendered_section and candidate["heading"]:
            parts.append(candidate["heading"])
        parts.append(candidate["chunk"])
        rendered_section = candidate["section"]
    return "\n\n".join(parts).strip()


def _fit_aio_capture_to_mapping(paragraphs: list[str], max_chars: int) -> tuple[str, dict]:
    from utils.owned_page import build_owned_page_registry

    candidates = []
    current_heading = ""
    current_section = 0
    seen = set()
    duplicate_blocks_rejected = 0
    for paragraph in paragraphs:
        paragraph_lines = paragraph.splitlines()
        first_line = paragraph_lines[0].strip() if paragraph_lines else ""
        if re.match(r"^#{1,6}\s+\S", first_line):
            current_heading = first_line
            current_section += 1
            paragraph = "\n".join(paragraph_lines[1:]).strip()
            if not paragraph:
                continue
        for chunk in _split_aio_capture_block(paragraph):
            normalized_chunk = _aio_capture_dedupe_key(chunk)
            dedupe_key = (current_section, normalized_chunk)
            if normalized_chunk and dedupe_key in seen:
                duplicate_blocks_rejected += 1
                continue
            if normalized_chunk:
                seen.add(dedupe_key)
            candidates.append({
                "chunk": chunk,
                "heading": current_heading,
                "score": _aio_capture_signal_score(chunk, current_heading),
                "section": current_section,
            })

    limit = min(max_chars, _AIO_CAPTURE_MAX_CHARS)
    selected = set(range(len(candidates)))
    content = _render_aio_capture_candidates(candidates, selected)
    registry = build_owned_page_registry(content)
    if len(content) > limit or registry["truncated"] or len(registry["blocks"]) > 24:
        best_by_section = {}
        for index, candidate in enumerate(candidates):
            previous = best_by_section.get(candidate["section"])
            if previous is None or candidate["score"] > candidates[previous]["score"]:
                best_by_section[candidate["section"]] = index
        representatives = sorted(
            (
                index
                for index in best_by_section.values()
                if candidates[index]["score"] >= 0
            ),
            key=lambda index: (-candidates[index]["score"], index),
        )
        remaining = sorted(
            (index for index in range(len(candidates)) if index not in representatives),
            key=lambda index: (-candidates[index]["score"], index),
        )
        selected = set()
        for index in [*representatives, *remaining]:
            trial_selected = {*selected, index}
            trial = _render_aio_capture_candidates(candidates, trial_selected)
            if len(trial) > limit:
                continue
            trial_registry = build_owned_page_registry(trial)
            if trial_registry["truncated"] or len(trial_registry["blocks"]) > 24:
                continue
            selected = trial_selected
        content = _render_aio_capture_candidates(candidates, selected)
        registry = build_owned_page_registry(content)

    return content, {
        "duplicate_blocks_rejected": duplicate_blocks_rejected,
        "mapped_block_count": len(registry["blocks"]),
        "mapped_retained_chars": registry["retained_char_count"],
        "mapping_truncated": bool(registry["truncated"]),
    }


def _curate_aio_page_context(text: str, max_chars: int) -> tuple[str, dict]:
    counters = {
        "empty_blocks_rejected": 0,
        "low_signal_blocks_rejected": 0,
        "navigation_links_rejected": 0,
        "short_blocks_retained": 0,
        "ui_noise_lines_rejected": 0,
        "visible_link_labels_retained": 0,
    }
    text = re.sub(
        r"!\[(?P<label>[^\]]*)\]\(https?://[^\)]+\)",
        lambda match: match.group("label").strip(),
        text,
    )
    paragraphs = []
    for paragraph in re.split(r"\n[ \t]*\n+", text):
        cleaned = _clean_aio_capture_paragraph(paragraph, counters)
        if cleaned:
            paragraphs.append(cleaned)
    content, mapping = _fit_aio_capture_to_mapping(paragraphs, max_chars)
    heading_count = sum(
        line.lstrip().startswith("#")
        for line in content.splitlines()
    )
    sparse_reasons = []
    if len(content) < _AIO_CAPTURE_SPARSE_CHARS:
        sparse_reasons.append("retained_chars_below_target")
    if mapping["mapped_block_count"] < 6:
        sparse_reasons.append("few_content_blocks")
    if heading_count < 2:
        sparse_reasons.append("few_headings")
    sparse = bool(
        len(content) < _AIO_CAPTURE_SPARSE_CHARS
        and (
            mapping["mapped_block_count"] < 6
            or heading_count < 2
            or counters["navigation_links_rejected"] >= 4
        )
    )
    return content, {
        **counters,
        **mapping,
        "heading_count": heading_count,
        "retained_chars": len(content),
        "sparse": sparse,
        "sparse_reasons": sparse_reasons,
    }


def _aio_collection_excerpt(text: str, product_urls: set[str]) -> tuple[str, int]:
    lines = text.splitlines()
    kept = []
    navigation_links_rejected = 0
    skip_filter_block = False
    skip_price_for_product = False
    for line in lines:
        clean = re.sub(r"^#+\s*", "", line).strip().casefold()
        product_match = _PRODUCT_LINK_RE.match(line.strip())
        if clean.rstrip(":") == "filters":
            skip_filter_block = True
            continue
        if product_match:
            skip_filter_block = False
            url = product_match.group("url").strip()
            if url in product_urls or _is_collection_navigation_link(
                product_match.group("name"),
                url,
            ):
                if url not in product_urls:
                    navigation_links_rejected += 1
                skip_price_for_product = url in product_urls
                continue
        if skip_price_for_product and _PRICE_RE.fullmatch(line.strip()):
            skip_price_for_product = False
            continue
        skip_price_for_product = False
        if skip_filter_block:
            if line.lstrip().startswith("#"):
                skip_filter_block = False
            else:
                continue
        kept.append(line)
    return "\n".join(kept), navigation_links_rejected


def _build_aio_collection_context(text: str, max_chars: int) -> tuple[str, str, dict]:
    title = _extract_title(text)
    lines = _normalise_lines(text, _COLLECTION_NOISE_LINE_PATTERNS)
    products = _extract_aio_collection_products(lines)
    filters = _extract_collection_filters(lines)
    excerpt_source, collection_navigation_rejected = _aio_collection_excerpt(
        text,
        {item["url"] for item in products},
    )
    excerpt, excerpt_quality = _curate_aio_page_context(excerpt_source, max_chars)

    sections = []
    if products or filters or excerpt:
        sections.append("COLLECTION CONTEXT")
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
    if excerpt:
        sections.append("Page structure and copy:\n" + excerpt)

    combined_paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n[ \t]*\n+", "\n\n".join(sections))
        if paragraph.strip()
    ]
    combined, mapping = _fit_aio_capture_to_mapping(
        combined_paragraphs,
        max_chars,
    )
    quality = {
        **excerpt_quality,
        **mapping,
        "product_count": len(products),
        "filter_count": len(filters),
        "retained_chars": len(combined),
    }
    quality["duplicate_blocks_rejected"] = (
        int(excerpt_quality.get("duplicate_blocks_rejected") or 0)
        + int(mapping.get("duplicate_blocks_rejected") or 0)
    )
    quality["navigation_links_rejected"] = (
        int(quality.get("navigation_links_rejected") or 0)
        + collection_navigation_rejected
    )
    sparse_reasons = list(quality.get("sparse_reasons") or [])
    if not products:
        sparse_reasons.append("no_products_detected")
    quality["sparse_reasons"] = list(dict.fromkeys(sparse_reasons))
    collection_has_core_evidence = bool(
        len(products) >= 2
        or (products and filters)
    )
    quality["sparse"] = bool(
        not products
        or (quality.get("sparse") and not collection_has_core_evidence)
    )
    return combined, title, quality


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


def _process_reader_text(
    text: str,
    max_chars: int,
    mode: str = "default",
    capture_version: str = "",
) -> dict:
    text = (text or "").strip()
    if not text:
        return _scrape_result("", "", 0, mode, "Jina returned empty content")

    raw_chars = len(text)
    text, diagnostics_removed = _strip_jina_diagnostics(text)
    diagnostic_error = "Jina returned diagnostics without substantive page content"
    if not text:
        return _scrape_result("", "", raw_chars, mode, diagnostic_error)

    if capture_version:
        if capture_version != AIO_OWNED_PAGE_CAPTURE_VERSION:
            raise ValueError(
                f'Owned-page capture version "{capture_version}" is unavailable.'
            )
        if mode == "ecommerce_collection":
            content, title, quality = _build_aio_collection_context(text, max_chars)
        else:
            title = _extract_title(text)
            content, quality = _curate_aio_page_context(text, max_chars)
        result = _scrape_result(
            content,
            title,
            raw_chars,
            mode,
            "" if content else "No substantive content found after versioned capture",
        )
        result["capture_version"] = capture_version
        quality["raw_chars"] = raw_chars
        retention_ratio = round(len(content) / max(raw_chars, 1), 4)
        quality["retention_ratio"] = retention_ratio
        missing_collection_products = bool(
            mode == "ecommerce_collection"
            and int(quality.get("product_count") or 0) == 0
        )
        severe_filtering = bool(
            raw_chars >= 8_000
            and retention_ratio < 0.5
        )
        quality["sparse"] = bool(
            missing_collection_products
            or (quality.get("sparse") and severe_filtering)
        )
        result["quality_diagnostics"] = quality
        return result

    if mode == "ecommerce_collection":
        content, title = _build_collection_context(text, max_chars)
        if not content or content == "COLLECTION CONTEXT":
            return _scrape_result(
                "",
                title,
                raw_chars,
                mode,
                diagnostic_error if diagnostics_removed else "No collection products, filters, or content found",
            )
        return _scrape_result(content, title, raw_chars, mode)

    title = _extract_title(text)

    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"^\s*\*\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,4}\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    lines = [line for line in text.splitlines() if not _NOISE_LINE_PATTERNS.match(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return _scrape_result(
            "",
            title,
            raw_chars,
            mode,
            diagnostic_error if diagnostics_removed else "No content found after stripping boilerplate",
        )

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
        return _scrape_result(
            "",
            title,
            raw_chars,
            mode,
            diagnostic_error if diagnostics_removed else "No substantive content found after scoring",
        )
    return _scrape_result(content, title, raw_chars, mode)


def _request_live_without_selector(url: str, headers: dict):
    recovery_headers = dict(headers)
    recovery_headers.pop("X-Remove-Selector", None)
    return requests.get(
        f"{JINA_BASE}/{url}",
        headers=recovery_headers,
        timeout=_JINA_REQUEST_TIMEOUT_SECONDS,
    )


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


def _aio_capture_quality_score(result: dict) -> int:
    quality = result.get("quality_diagnostics") or {}
    return (
        int(quality.get("retained_chars") or 0)
        + int(quality.get("mapped_block_count") or 0) * 80
        + int(quality.get("heading_count") or 0) * 100
        + int(quality.get("product_count") or 0) * 100
        + int(quality.get("filter_count") or 0) * 50
    )


def _prefer_aio_recovery(primary: dict, recovery: dict) -> bool:
    if not recovery.get("success"):
        return False
    if not primary.get("success"):
        return True
    primary_quality = primary.get("quality_diagnostics") or {}
    recovery_quality = recovery.get("quality_diagnostics") or {}
    if recovery_quality.get("mapping_truncated"):
        return False
    return bool(
        int(recovery_quality.get("retained_chars") or 0)
        > int(primary_quality.get("retained_chars") or 0)
        and _aio_capture_quality_score(recovery)
        >= _aio_capture_quality_score(primary) + 400
    )


def _annotate_aio_recovery(
    selected: dict,
    primary: dict,
    recovery: dict | None,
    *,
    selected_recovery: bool,
) -> None:
    quality = selected.get("quality_diagnostics")
    if not isinstance(quality, dict):
        return
    primary_quality = primary.get("quality_diagnostics") or {}
    recovery_quality = (
        recovery.get("quality_diagnostics") or {}
        if isinstance(recovery, dict)
        else {}
    )
    quality.update({
        "recovery_attempted": True,
        "recovery_selected": selected_recovery,
        "primary_retained_chars": int(primary_quality.get("retained_chars") or 0),
        "recovery_retained_chars": int(recovery_quality.get("retained_chars") or 0),
    })


def scrape_page_context(
    api_key: str,
    url: str,
    max_chars: int = 10000,
    mode: str = "default",
    capture_version: str = "",
) -> dict:
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
    selector_recovery_attempted = False
    try:
        try:
            response = requests.get(
                f"{JINA_BASE}/{url}",
                headers=headers,
                timeout=_JINA_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in (400, 422):
                selector_recovery_attempted = True
                response_source = "live_selector_recovery"
                response = _request_live_without_selector(url, headers)
        except requests.exceptions.Timeout:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        if response_source.startswith("live") and 500 <= response.status_code < 600:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        response.raise_for_status()
        result = _process_reader_text(
            response.text,
            max_chars,
            mode,
            capture_version,
        )

        sparse_versioned_capture = bool(
            capture_version == AIO_OWNED_PAGE_CAPTURE_VERSION
            and (result.get("quality_diagnostics") or {}).get("sparse")
        )
        if (
            (not result["success"] or sparse_versioned_capture)
            and response_source == "live"
            and not selector_recovery_attempted
        ):
            selector_recovery_attempted = True
            primary_result = result
            recovery_result = None
            recovery_selected = False
            try:
                recovery_response = _request_live_without_selector(url, headers)
                recovery_response.raise_for_status()
                recovery_result = _process_reader_text(
                    recovery_response.text,
                    max_chars,
                    mode,
                    capture_version,
                )
                if (
                    _prefer_aio_recovery(primary_result, recovery_result)
                    if capture_version == AIO_OWNED_PAGE_CAPTURE_VERSION
                    else recovery_result["success"]
                ):
                    result = recovery_result
                    response_source = "live_selector_recovery"
                    recovery_selected = True
            except requests.exceptions.RequestException:
                pass
            if capture_version == AIO_OWNED_PAGE_CAPTURE_VERSION:
                _annotate_aio_recovery(
                    result,
                    primary_result,
                    recovery_result,
                    selected_recovery=recovery_selected,
                )

        if not result["success"] and response_source.startswith("live"):
            fallback_attempted = True
            cached_response = _request_cached_snapshot(url, headers)
            cached_response.raise_for_status()
            cached_result = _process_reader_text(
                cached_response.text,
                max_chars,
                mode,
                capture_version,
            )
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
    capture_version: str = "",
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
    result = _process_reader_text(
        reader_text,
        max_chars,
        mode,
        capture_version,
    )
    if metadata_title and not result.get("title"):
        result["title"] = metadata_title
    result["source"] = "firecrawl"
    return result
