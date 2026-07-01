import re


_KNOWN_PAGE_TYPES = {
    "blog",
    "case_study",
    "glossary",
    "homepage",
    "service",
    "local",
    "about",
    "contact",
    "product",
    "collection",
    "landing_page",
}

_ALIASES = {
    "service lp": "service",
    "service landing page": "service",
    "service landing pages": "service",
    "service page": "service",
    "service pages": "service",
    "landing page": "landing_page",
    "landing pages": "landing_page",
    "lp": "landing_page",
    "home": "homepage",
    "home page": "homepage",
    "homepage": "homepage",
    "category": "collection",
    "category page": "collection",
    "category pages": "collection",
    "collection page": "collection",
    "collection pages": "collection",
    "ecommerce category": "collection",
    "ecommerce category page": "collection",
    "product page": "product",
    "product pages": "product",
    "location": "local",
    "location page": "local",
    "local page": "local",
    "local service": "local",
    "local service page": "local",
    "city page": "local",
    "about us": "about",
    "about page": "about",
    "brand": "about",
    "brand page": "about",
    "contact page": "contact",
    "case study": "case_study",
    "case study page": "case_study",
    "glossary page": "glossary",
    "blog page": "blog",
}

_DEFAULT_TEMPLATE_BY_PAGE_TYPE = {
    "blog": "blog_standard",
    "case_study": "case_study",
    "glossary": "glossary",
    "homepage": "homepage",
    "service": "service_page",
    "local": "local_service_page",
    "about": "about_us",
    "contact": "contact_us",
    "product": "product_page",
    "collection": "collection_page",
    "landing_page": "landing_page",
}


def _clean(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[_\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_page_type(value: str, default: str = "general") -> str:
    cleaned = _clean(value)
    if not cleaned:
        return default
    normalized = _ALIASES.get(cleaned, cleaned.replace(" ", "_"))
    return normalized if normalized in _KNOWN_PAGE_TYPES else normalized


def default_template_key_for_page_type(page_type: str) -> str:
    return _DEFAULT_TEMPLATE_BY_PAGE_TYPE.get(normalize_page_type(page_type, "service"), "service_page")
