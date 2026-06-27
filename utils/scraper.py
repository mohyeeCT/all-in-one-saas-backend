import requests
import re


JINA_BASE = "https://r.jina.ai/"

# Headings that indicate page chrome, not editorial content.
# These are matched case-insensitively against the full heading text.
CHROME_HEADING_PATTERNS = [
    r"^your cart",
    r"^cart\b",
    r"\bcart\s*[-–]\s*\d+",
    r"^checkout",
    r"^payment",
    r"^payment method",
    r"^shipping",
    r"^delivery",
    r"^nyc delivery",
    r"^follow us",
    r"^follow\s+\w+\s+on",
    r"^subscribe",
    r"^newsletter",
    r"^sign up",
    r"^login",
    r"^log in",
    r"^create account",
    r"^my account",
    r"^navigation",
    r"^menu\b",
    r"^footer",
    r"^header",
    r"^search\b",
    r"^recently viewed",
    r"^you may also like",
    r"^related products",
    r"^customers also",
    r"^free shipping",
    r"^return policy",
    r"^refund",
    r"^cookie",
    r"^privacy policy",
    r"^terms",
    r"^social media",
    r"^share this",
    r"^tags\b",
    r"^categories\b",
    r"^contact us",
    r"^get in touch\b",
    r"^about us\b",
    r"^welcome to\s+\w+\s*$",
    r"^copyright",
    r"^all rights",
    r"^powered by",
]

# URL patterns that identify non-editorial pages - discard as competitors
BLOCKED_COMPETITOR_URL_PATTERNS = [
    r"amazon\.com",
    r"amazon\.",
    r"ebay\.com",
    r"walmart\.com",
    r"etsy\.com",
    r"/collections/",
    r"/products/",
    r"/shop/",
    r"/cart",
    r"/checkout",
    r"/account",
    r"/search\?",
    r"\?s=",
    r"&q=",
    r"/category/",
    r"/tag/",
    r"/author/",
    r"/page/\d+",
    r"pinterest\.com",
    r"instagram\.com",
    r"facebook\.com",
    r"twitter\.com",
    r"youtube\.com",
    r"reddit\.com",
    r"quora\.com",
]

# Minimum word count for a page to be considered editorial content
MIN_EDITORIAL_WORD_COUNT = 400


def _is_chrome_heading(text: str) -> bool:
    """Returns True if this heading is page chrome, not editorial content."""
    t = text.strip().lower()
    for pattern in CHROME_HEADING_PATTERNS:
        if re.search(pattern, t):
            return True
    return False


def _normalise_page_type(page_type: str) -> str:
    pt = (page_type or "").lower()
    if pt in {"product_page", "product"}:
        return "product"
    if pt in {"collection_page", "collection", "category"}:
        return "collection"
    return pt


def _is_blocked_url(url: str, allow_ecommerce_paths: bool = False) -> bool:
    """Returns True if this URL should never be used as a competitor."""
    u = url.lower()
    for pattern in BLOCKED_COMPETITOR_URL_PATTERNS:
        if allow_ecommerce_paths and pattern in {r"/collections/", r"/products/", r"/shop/"}:
            continue
        if re.search(pattern, u):
            return True
    return False


def _clean_markdown(markdown: str) -> str:
    """
    Remove page chrome sections from scraped markdown.
    Drops everything from a chrome heading until the next non-chrome heading.
    """
    lines = markdown.splitlines()
    cleaned = []
    skip = False

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            if _is_chrome_heading(heading_text):
                skip = True
                continue
            else:
                skip = False

        if not skip:
            cleaned.append(line)

    return "\n".join(cleaned)


def scrape_url(url: str, timeout: int = 25, api_key: str = "") -> dict:
    """
    Scrapes a URL via Jina Reader and strips page chrome (cart, nav, footer etc).
    Returns:
    {
        url, title, headings, body_text, word_count, success, error
    }
    X-Target-Selector intentionally omitted - causes 422 errors.
    """
    try:
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "markdown",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = requests.get(
            f"{JINA_BASE}{url}",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.text
    except Exception as e:
        return {
            "url": url, "title": "", "headings": [],
            "body_text": "", "word_count": 0,
            "success": False, "error": str(e),
        }

    # Strip chrome sections before processing
    text = _clean_markdown(raw)

    headings = _extract_headings(text)
    # Filter chrome headings from the heading list too
    headings = [h for h in headings if not _is_chrome_heading(h["text"])]

    title = headings[0]["text"] if headings and headings[0]["level"] == 1 else _extract_title(text)
    body = _strip_markdown_syntax(text)
    word_count = len(body.split())

    return {
        "url": url,
        "title": title,
        "headings": headings,
        "body_text": body,
        "word_count": word_count,
        "success": True,
        "error": "",
    }


def _extract_headings(markdown: str) -> list:
    headings = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            headings.append({"level": len(m.group(1)), "text": m.group(2).strip()})
    return headings


def _extract_title(markdown: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line and not line.startswith("[") and not line.startswith("!"):
            return re.sub(r"^#+\s*", "", line)
    return ""


def _strip_markdown_syntax(markdown: str) -> str:
    """Strip heading markers and markdown links, return plain text."""
    lines = []
    for line in markdown.splitlines():
        # Remove heading markers
        line = re.sub(r"^#{1,6}\s+", "", line)
        # Remove markdown links but keep link text
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        # Remove image tags
        line = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", line)
        lines.append(line)
    return " ".join(l for l in lines if l.strip())


def infer_template_from_scrape(scraped: dict) -> list:
    """
    Infer section structure from a scraped page's H2s.
    Chrome headings are already filtered before this runs.
    Returns a list of section dicts compatible with the template format.
    """
    h2s = [h for h in scraped.get("headings", []) if h["level"] == 2]
    if not h2s:
        return []

    total_words = scraped.get("word_count", 1000)
    per_section = max(100, total_words // max(len(h2s), 1))
    wc_min = max(80, int(per_section * 0.7))
    wc_max = int(per_section * 1.3)

    sections = []
    for i, h in enumerate(h2s):
        name_slug = re.sub(r"[^a-z0-9]+", "_", h["text"].lower()).strip("_")
        is_faq = any(x in h["text"].lower() for x in ["faq", "question", "asked"])
        is_cta = any(x in h["text"].lower() for x in ["get started", "next step", "ready to", "start today"])

        sections.append({
            "name": name_slug,
            "label": h["text"],
            "purpose": f"Cover the topic: {h['text']}",
            "word_count": [wc_min, wc_max],
            "keyword_slot": "primary" if i == 0 else ("lsi" if is_faq else "supporting"),
            "heading_level": "none" if i == 0 else "h2",
            "prompt_rules": (
                "Answer reader questions using PAA data provided. 2 to 4 sentences each." if is_faq
                else "Short closing with a natural next action. Business-type-aware. No em dashes." if is_cta
                else f"Write this section covering: {h['text']}. Be specific and informative. No em dashes."
            ),
        })

    return sections


def is_editorial_competitor(scrape: dict, page_type: str) -> bool:
    """
    Hard filter: returns False for any page that is clearly not editorial content.
    This runs before classify_competitor_relevance.

    Rejects:
    - Pages under MIN_EDITORIAL_WORD_COUNT words
    - URLs matching blocked patterns (product pages, collection pages, marketplaces)
    - Pages with more than 3 chrome headings (indicates heavy ecommerce template)
    """
    page_type_norm = _normalise_page_type(page_type)
    ecommerce_context = page_type_norm in {"product", "collection"}

    url = scrape.get("url", "")
    if _is_blocked_url(url, allow_ecommerce_paths=ecommerce_context):
        return False

    min_words = 120 if ecommerce_context else MIN_EDITORIAL_WORD_COUNT
    if scrape.get("word_count", 0) < min_words:
        return False

    # Count chrome headings - if more than 2, page is mostly chrome
    chrome_count = sum(
        1 for h in scrape.get("headings", [])
        if _is_chrome_heading(h["text"])
    )
    if chrome_count > 2:
        return False

    return True


def classify_competitor_relevance(scrape: dict, business_type: str, page_type: str) -> float:
    """
    Score a scraped competitor for relevance to the target page type.
    Returns 0.0 to 1.0. Run after is_editorial_competitor passes.
    """
    score = 0.0
    page_type_norm = _normalise_page_type(page_type)
    body = (scrape.get("body_text", "") + " " + scrape.get("title", "")).lower()
    url = scrape.get("url", "").lower()
    headings = [h["text"].lower() for h in scrape.get("headings", [])]
    all_text = body + " " + " ".join(headings)

    # Page type signals
    page_type_signals = {
        "blog": ["author", "published", "min read", "posted", "updated", "written by", "last updated"],
        "case_study": ["case study", "client", "results", "challenge", "solution", "outcome"],
        "glossary": ["definition", "what is", "refers to", "meaning", "glossary"],
        "product": ["add to cart", "product details", "specifications", "materials", "reviews", "size", "variant"],
        "collection": ["shop", "collection", "category", "filter", "sort", "compare", "buying guide"],
    }
    for signal in page_type_signals.get(page_type_norm, []):
        if signal in all_text:
            score += 0.2

    # URL structure - strongest signal
    url_page_signals = {
        "blog": ["/blog/", "/post/", "/article/", "/news/", "/learn/", "/guide/", "/resources/"],
        "case_study": ["/case-study", "/case-studies", "/customer-stories", "/success-stories"],
        "glossary": ["/glossary/", "/define/", "/what-is/", "/wiki/"],
        "product": ["/products/", "/product/", "/p/"],
        "collection": ["/collections/", "/category/", "/categories/"],
    }
    for signal in url_page_signals.get(page_type_norm, []):
        if signal in url:
            score += 0.3

    # Word count bonus - longer pages are more likely editorial
    wc = scrape.get("word_count", 0)
    if wc > 1000:
        score += 0.2
    elif wc > 600:
        score += 0.1

    # H2 count - editorial pages have structured headings
    h2_count = sum(1 for h in scrape.get("headings", []) if h["level"] == 2)
    if h2_count >= 3:
        score += 0.1

    return min(score, 1.0)


def map_competitor_sections(competitor_scrapes: list, template_sections: list) -> dict:
    """
    Maps competitor content to template sections by keyword overlap.
    Returns: { section_name: [excerpt_1, excerpt_2, ...] }
    """
    def tokens(text):
        words = re.findall(r"[a-z]+", text.lower())
        return set(w for w in words if len(w) > 3)

    section_context = {s["name"]: [] for s in template_sections}

    for scrape in competitor_scrapes:
        if not scrape.get("success"):
            continue

        competitor_h2s = [h for h in scrape.get("headings", []) if h["level"] == 2]
        body = scrape.get("body_text", "")

        for comp_h2 in competitor_h2s:
            comp_tokens = tokens(comp_h2["text"])
            best_section = None
            best_score = 0

            for section in template_sections:
                sec_tokens = tokens(section["label"] + " " + section["purpose"])
                overlap = len(comp_tokens & sec_tokens)
                if overlap > best_score:
                    best_score = overlap
                    best_section = section["name"]

            if best_section and best_score > 0:
                excerpt = _extract_excerpt_after_heading(body, comp_h2["text"], max_words=80)
                if excerpt:
                    section_context[best_section].append(excerpt)

    return section_context


def _extract_excerpt_after_heading(body: str, heading: str, max_words: int = 80) -> str:
    heading_pattern = re.escape(heading[:30])
    match = re.search(heading_pattern, body, re.IGNORECASE)
    if not match:
        return ""
    start = match.end()
    excerpt_words = body[start:start + max_words * 6].split()[:max_words]
    return " ".join(excerpt_words)
