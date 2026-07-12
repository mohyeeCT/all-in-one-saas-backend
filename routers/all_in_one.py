import time
import uuid
import base64
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from google.auth.exceptions import RefreshError
from pydantic import BaseModel, Field

from auth import get_current_user, get_supabase
from abuse_protection import enforce_job_start, enforce_rate_limit, execute_active_job_write
from credentials import hydrate_job_settings, mark_gsc_reconnect_required, strip_secret_fields
from utils.niches import get_niche_context
from utils.dfs import (
    get_search_volume, get_keyword_difficulty,
    get_ranked_keywords_for_url, get_keyword_ideas,
    get_serp_data,
)
from utils.keyword import rank_keywords, merge_keyword_pools, assign_keywords_to_sections
from utils.gsc import GscOAuthConfigError, get_gsc_client, get_top_queries_for_url
from utils.scraper import (
    scrape_url, map_competitor_sections,
    classify_competitor_relevance, is_editorial_competitor,
)
from utils.faq_scraper import scrape_page_context
from utils.templates import get_template, get_templates_for_page_type, parse_custom_template
from utils.page_types import default_template_key_for_page_type, normalize_page_type
from utils.copy_gen import (
    generate_page, generate_faq, generate_copy, generate_strategy_brief, repair_repeated_page_copy,
    repair_faq_items, repair_meta_copy, review_output_quality, sanitise, score_brand_consistency,
    strategy_brief_issues, META_TITLE_PREFERRED_MIN, META_TITLE_PREFERRED_MAX,
    META_DESCRIPTION_PREFERRED_MIN, META_DESCRIPTION_PREFERRED_MAX,
)
from utils.docx_export import build_docx

router = APIRouter()

_GSC_RECONNECT_ERROR = "Google Search Console reconnect required."
_GSC_UNAVAILABLE_ERROR = "Selected Google Search Console connection unavailable."
_GSC_CONFIG_ERROR = "Google Search Console OAuth configuration missing."
_GSC_METHOD_LABELS = {"google_oauth", "service_account", "disabled", "unavailable"}

_RATE_LIMITS = {
    "Claude": 0.5,
    "OpenAI": 0.5,
    "Gemini (free)": 5.0,
    "Mistral (free tier)": 2.0,
    "Groq (free tier)": 2.0,
}

_GENERIC_OPENERS = (
    "Welcome to",
    "Are you looking for",
    "In today's world",
    "Whether you are",
    "Finding the right",
    "When it comes to",
    "Choosing the right",
    "Looking for",
    "There are many",
    "It can be difficult to",
    "If you are searching for",
    "Whether you need",
    "In the world of",
)

_KEYWORD_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "into",
    "near", "of", "on", "or", "the", "to", "with", "your",
}

_CONTENT_GAP_STOPWORDS = _KEYWORD_STOPWORDS | {
    "about", "also", "buyers", "client", "competitor", "competitors", "content",
    "cover", "covers", "daily", "does", "example", "explain", "explains",
    "from", "have", "into", "more", "page", "section", "their", "this",
    "what", "when", "where", "which", "while",
}

_REPEATED_PHRASE_STOPWORDS = _CONTENT_GAP_STOPWORDS | {
    "after", "all", "because", "before", "can", "each", "every", "has",
    "help", "helps", "how", "its", "like", "make", "makes", "need", "needs",
    "our", "over", "than", "that", "these", "they", "through", "use", "used",
    "using", "was", "way", "will", "you",
}

_QA_SEVERITIES = {"warning", "review", "error"}
_B2B_CONSUMER_CTAS = (
    "shop now",
    "add to cart",
    "grab yours",
    "buy today",
    "buy now",
    "order yours",
)
_FAQ_RISKY_TOPICS = (
    "shipping",
    "delivery",
    "return policy",
    "returns",
    "refund",
    "warranty",
    "guarantee",
    "in stock",
    "availability",
    "pricing",
)


def _safe_gsc_auth_method(settings: dict, gsc_credentials: dict | None, gsc_client=None) -> str:
    if not settings.get("use_gsc"):
        return "disabled"
    if not gsc_credentials or not gsc_client:
        return "unavailable"
    method = gsc_credentials.get("method")
    return method if method in _GSC_METHOD_LABELS else "unavailable"


def _is_cancelled(sb, job_id: str, user_id: str) -> bool:
    try:
        res = (
            sb.table("jobs")
            .select("status")
            .eq("id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(res.data and res.data[0].get("status") in {"cancelling", "cancelled"})
    except Exception:
        return False


def _is_missing_internal_link_suggestions_column(exc: Exception) -> bool:
    message = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").upper()
    return "internal_link_suggestions" in message and (
        code in {"42703", "PGRST204", "PGRST205"}
        or "column" in message
        or "schema cache" in message
    )


def _execute_job_update(sb, job_id: str, user_id: str, update_data: dict):
    return (
        sb.table("jobs")
        .update(update_data)
        .eq("id", job_id)
        .eq("user_id", user_id)
        .execute()
    )


def _update_job(sb, job_id: str, user_id: str, data: dict):
    try:
        update_data = {**data, "updated_at": "now()"}
        if "current_step" in data and data["current_step"]:
            log_entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "msg": data["current_step"],
            }
            try:
                res = (
                    sb.table("jobs")
                    .select("logs")
                    .eq("id", job_id)
                    .eq("user_id", user_id)
                    .execute()
                )
                current_logs = (res.data[0].get("logs") or []) if res.data else []
                current_logs.append(log_entry)
                update_data["logs"] = current_logs
            except Exception:
                pass
        try:
            _execute_job_update(sb, job_id, user_id, update_data)
        except Exception as exc:
            if (
                "internal_link_suggestions" in update_data
                and _is_missing_internal_link_suggestions_column(exc)
            ):
                fallback_data = {**update_data}
                fallback_data.pop("internal_link_suggestions", None)
                _execute_job_update(sb, job_id, user_id, fallback_data)
            else:
                raise
    except Exception:
        pass


def _build_brand_context(brand_profile: dict | None, niche: str = "") -> str:
    lines = []
    if brand_profile:
        if brand_profile.get("brand_voice"):
            lines.append("- Voice: " + brand_profile["brand_voice"])
        tone = brand_profile.get("tone") or brand_profile.get("tone_of_voice")
        if tone:
            lines.append("- Tone: " + tone)
        if brand_profile.get("target_audience"):
            lines.append("- Target audience: " + brand_profile["target_audience"])
        if brand_profile.get("usps"):
            lines.append("- Unique selling points: " + brand_profile["usps"])
        if brand_profile.get("key_messages"):
            lines.append("- Key messages to reinforce: " + brand_profile["key_messages"])
        if brand_profile.get("competitors"):
            lines.append("- Competitors to differentiate from: " + brand_profile["competitors"])
        if brand_profile.get("products_services"):
            lines.append("- Products/services: " + brand_profile["products_services"])
        if brand_profile.get("words_to_avoid"):
            lines.append("- Words to avoid: " + brand_profile["words_to_avoid"])
        if brand_profile.get("example_copy"):
            lines.append("- Example copy to emulate in style, not content:\n" + brand_profile["example_copy"])
        if brand_profile.get("guidelines"):
            lines.append("- Additional brand guidelines:\n" + brand_profile["guidelines"])

    parts = ["BRAND CONTEXT:\n" + "\n".join(lines)] if lines else []
    niche_context = get_niche_context(niche)
    if niche_context:
        parts.append("NICHE CONTEXT:\n" + niche_context)
    return "\n".join(parts)


def _split_forbidden_phrases(*values) -> list[str]:
    phrases = []
    seen = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = re.split(r"[\n,;]+", str(value))
        for candidate in candidates:
            phrase = str(candidate).strip()
            key = phrase.lower()
            if phrase and key not in seen:
                phrases.append(phrase)
                seen.add(key)
    return phrases


def _contains_forbidden_phrase(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    phrase = phrase.strip()
    escaped = re.escape(phrase)
    if not escaped:
        return False
    left = r"(?<!\w)" if phrase[0].isalnum() else ""
    right = r"(?!\w)" if phrase[-1].isalnum() else ""
    return re.search(left + escaped + right, text, flags=re.IGNORECASE) is not None


def _normalise_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _add_qa_flag(
    flags: list[dict],
    code: str,
    message: str,
    output: str = "",
    phrase: str = "",
    severity: str = "review",
):
    flag = {
        "code": code,
        "message": message,
        "severity": severity if severity in _QA_SEVERITIES else "review",
    }
    if output:
        flag["output"] = output
    if phrase:
        flag["phrase"] = phrase
    flags.append(flag)


def _qa_status(flags: list[dict]) -> str:
    severities = {str(flag.get("severity") or "review") for flag in (flags or [])}
    if "error" in severities:
        return "error"
    if "review" in severities:
        return "review"
    if "warning" in severities:
        return "warning"
    return "ok"


def _result_failed(result: dict) -> bool:
    status = str((result or {}).get("status") or "")
    return bool((result or {}).get("error")) or status == "error" or status.startswith("skipped:")


def _add_strategy_qa_flag(
    flags: list[dict],
    strategy_status: str,
    strategy_issues: list[str] | None = None,
):
    if strategy_status == "ready" or strategy_status == "not_requested":
        return
    issues = [str(issue).strip() for issue in (strategy_issues or []) if str(issue).strip()]
    message = (
        "Strategy brief was unavailable; outputs were generated without the shared strategy layer."
        if strategy_status == "unavailable"
        else "Strategy brief is incomplete and needs review."
    )
    _add_qa_flag(flags, "strategy_brief_" + strategy_status, message, "strategy", severity="review")
    if issues:
        flags[-1]["details"] = issues[:6]


def _build_editorial_outputs(
    *,
    gen_meta: bool,
    gen_faqs: bool,
    gen_page_copy: bool,
    generated_title: str,
    generated_description: str,
    optimised_h1: str,
    faq_items: list,
    section_results: dict,
) -> dict:
    outputs = {}
    if gen_meta and any((generated_title, generated_description, optimised_h1)):
        outputs["meta"] = {
            "title": generated_title or "",
            "description": generated_description or "",
            "h1_optimised": optimised_h1 or "",
        }
    if gen_faqs and faq_items:
        outputs["faqs"] = faq_items
    if gen_page_copy and section_results:
        outputs["page_copy"] = {
            key: value
            for key, value in section_results.items()
            if not str(key).startswith("_")
        }
    return outputs


def _add_editorial_qa_flags(flags: list[dict], review: dict):
    severity_by_code = {
        "unsupported_claim": "review",
        "strategy_misalignment": "review",
        "generic_exaggeration": "warning",
    }
    for issue in (review or {}).get("issues", []):
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "").strip()
        message = str(issue.get("message") or "").strip()
        output = str(issue.get("output") or "").strip()
        claim = str(issue.get("claim") or "").strip()
        if not code or not message:
            continue
        _add_qa_flag(
            flags,
            code,
            message,
            output,
            claim,
            severity=severity_by_code.get(code, "review"),
        )
        section = str(issue.get("section") or "").strip()
        if section:
            flags[-1]["section"] = section


def _normalise_similarity_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _shingle_similarity(left: str, right: str, shingle_size: int = 3) -> float:
    left_tokens = _normalise_similarity_text(left)
    right_tokens = _normalise_similarity_text(right)
    if len(left_tokens) < 4 or len(right_tokens) < 4:
        return 0.0

    size = shingle_size if len(left_tokens) >= shingle_size and len(right_tokens) >= shingle_size else 1
    left_units = {" ".join(left_tokens[i:i + size]) for i in range(len(left_tokens) - size + 1)}
    right_units = {" ".join(right_tokens[i:i + size]) for i in range(len(right_tokens) - size + 1)}
    if not left_units or not right_units:
        return 0.0

    shingle_score = len(left_units & right_units) / len(left_units | right_units)
    token_left = set(left_tokens)
    token_right = set(right_tokens)
    token_score = len(token_left & token_right) / len(token_left | token_right) if token_left and token_right else 0.0
    return max(shingle_score, token_score)


def _first_page_copy_section(section_results: dict) -> str:
    for key, value in (section_results or {}).items():
        if not str(key).startswith("_") and str(value or "").strip():
            return str(value)
    return ""


def _full_page_copy_text(section_results: dict) -> str:
    return "\n\n".join(str(v) for k, v in (section_results or {}).items() if not str(k).startswith("_"))


def _first_sentence_for_qa(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return re.split(r"[.!?]\s+", text, maxsplit=1)[0]


def _strip_leading_markdown_headings(text: str) -> str:
    lines = str(text or "").splitlines()
    while lines and (not lines[0].strip() or re.match(r"^#{1,6}\s+", lines[0].strip())):
        lines.pop(0)
    return "\n".join(lines).strip()


def _find_generic_opener(text: str) -> str:
    first_sentence = _normalise_phrase(_first_sentence_for_qa(text))
    if not first_sentence:
        return ""
    for opener in _GENERIC_OPENERS:
        if first_sentence.startswith(_normalise_phrase(opener)):
            return opener
    return ""


def _add_generic_opener_flags(flags: list[dict], generated_description: str, section_results: dict):
    opener = _find_generic_opener(generated_description)
    if opener:
        flags.append({
            "code": "generic_opener",
            "message": f'Generic opener found: "{opener}".',
            "output": "meta_description",
            "phrase": opener,
        })

    for section_name, text in (section_results or {}).items():
        if str(section_name).startswith("_"):
            continue
        opener = _find_generic_opener(_strip_leading_markdown_headings(str(text or "")))
        if opener:
            flags.append({
                "code": "generic_opener",
                "message": f'Generic opener found in section "{section_name}": "{opener}".',
                "output": "page_copy",
                "section": section_name,
                "phrase": opener,
            })


def _extract_first_page_h1(section_results: dict) -> str:
    for text in (section_results or {}).values():
        for line in str(text or "").splitlines():
            match = re.match(r"^#\s+(.+?)\s*$", line.strip())
            if match:
                return match.group(1).strip()
    return ""


def _assemble_full_page_copy(section_results: dict, template: dict | None = None) -> str:
    if template:
        return "\n\n".join(
            str(section_results.get(section.get("name", ""), ""))
            for section in template.get("sections", [])
        )
    return _full_page_copy_text(section_results)


def _enforce_canonical_page_h1(section_results: dict, canonical_h1: str) -> tuple[dict, bool]:
    canonical_h1 = (canonical_h1 or "").strip()
    if not section_results or not canonical_h1:
        return section_results, False

    updated = dict(section_results)
    for section_name, text in (section_results or {}).items():
        if str(section_name).startswith("_"):
            continue

        lines = str(text or "").splitlines()
        for idx, line in enumerate(lines):
            match = re.match(r"^(#\s+)(.+?)\s*$", line.strip())
            if not match:
                continue
            page_h1 = match.group(2).strip()
            if _normalise_phrase(page_h1) == _normalise_phrase(canonical_h1):
                return section_results, False
            lines[idx] = f"# {canonical_h1}"
            updated[section_name] = "\n".join(lines)
            return updated, True

    return section_results, False


def _add_h1_alignment_flag(flags: list[dict], optimised_h1: str, section_results: dict):
    page_h1 = _extract_first_page_h1(section_results)
    if not page_h1 or not (optimised_h1 or "").strip():
        return
    if _normalise_phrase(page_h1) == _normalise_phrase(optimised_h1):
        return
    flags.append({
        "code": "page_h1_differs_from_meta_h1",
        "message": "Page-copy H1 differs from the optimised meta H1.",
        "output": "page_copy",
        "meta_h1": optimised_h1,
        "page_h1": page_h1,
    })


def _word_count_for_qa(text: str) -> int:
    return len(_normalise_similarity_text(text))


def _limit_faq_items(faq_items: list, requested_count: int) -> tuple[list, bool]:
    if not isinstance(faq_items, list):
        return [], False
    try:
        requested = int(requested_count)
    except (TypeError, ValueError):
        return faq_items, False
    if requested < 0:
        return faq_items, False
    if len(faq_items) > requested:
        return faq_items[:requested], True
    return faq_items, False


def _contains_ngram(container: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    if len(candidate) > len(container):
        return False
    return any(
        container[idx:idx + len(candidate)] == candidate
        for idx in range(len(container) - len(candidate) + 1)
    )


def _repeated_phrase_candidates(text: str, min_count: int = 3, max_flags: int = 3) -> list[dict]:
    tokens = _normalise_similarity_text(text)
    if len(tokens) < 8:
        return []

    counts = {}
    for size in range(4, 1, -1):
        for idx in range(len(tokens) - size + 1):
            phrase = tuple(tokens[idx:idx + size])
            if phrase[0] in _REPEATED_PHRASE_STOPWORDS or phrase[-1] in _REPEATED_PHRASE_STOPWORDS:
                continue
            meaningful = [token for token in phrase if token not in _REPEATED_PHRASE_STOPWORDS]
            if len(set(meaningful)) < 2:
                continue
            counts[phrase] = counts.get(phrase, 0) + 1

    repeated = [
        (phrase, count)
        for phrase, count in counts.items()
        if count >= min_count
    ]
    repeated.sort(key=lambda item: (-len(item[0]), -item[1], " ".join(item[0])))

    selected = []
    selected_phrases = []
    for phrase, count in repeated:
        if any(_contains_ngram(existing, phrase) for existing in selected_phrases):
            continue
        selected_phrases.append(phrase)
        selected.append({"phrase": " ".join(phrase), "count": count})
        if len(selected) >= max_flags:
            break
    return selected


def _add_repeated_phrase_flags(flags: list[dict], page_copy_text: str):
    for repeated in _repeated_phrase_candidates(page_copy_text):
        flags.append({
            "code": "repeated_phrase",
            "message": "Phrase is repeated too often in page copy.",
            "output": "page_copy",
            "phrase": repeated["phrase"],
            "count": repeated["count"],
        })


def _meaningful_keyword_tokens(keyword: str) -> list[str]:
    return [
        token for token in _normalise_similarity_text(keyword)
        if len(token) > 2 and token not in _KEYWORD_STOPWORDS
    ]


def _keyword_present(keyword: str, text: str) -> bool:
    keyword_tokens = _normalise_similarity_text(keyword)
    text_tokens = _normalise_similarity_text(text)
    if not keyword_tokens or not text_tokens:
        return False

    keyword_phrase = " ".join(keyword_tokens)
    text_phrase = " ".join(text_tokens)
    if f" {keyword_phrase} " in f" {text_phrase} ":
        return True

    meaningful_tokens = _meaningful_keyword_tokens(keyword)
    if not meaningful_tokens:
        return False
    if len(meaningful_tokens) == 1:
        return meaningful_tokens[0] in set(text_tokens)

    matched = sum(1 for token in meaningful_tokens if token in set(text_tokens))
    required = len(meaningful_tokens) if len(meaningful_tokens) <= 3 else int(len(meaningful_tokens) * 0.75 + 0.999)
    return matched >= required


def _keyword_token_root(token: str) -> str:
    token = str(token or "").lower()
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _qa_keyword_present(keyword: str, text: str) -> bool:
    if _keyword_present(keyword, text):
        return True
    meaningful_tokens = _meaningful_keyword_tokens(keyword)
    text_tokens = _normalise_similarity_text(text)
    if not meaningful_tokens or not text_tokens:
        return False
    text_roots = {_keyword_token_root(token) for token in text_tokens}
    matched = sum(1 for token in meaningful_tokens if _keyword_token_root(token) in text_roots)
    required = len(meaningful_tokens) if len(meaningful_tokens) <= 3 else int(len(meaningful_tokens) * 0.75 + 0.999)
    return matched >= required


def _generated_output_texts(
    generated_title: str,
    generated_description: str,
    optimised_h1: str,
    faq_items: list,
    section_results: dict,
) -> list[tuple[str, str]]:
    outputs = [
        ("meta_title", generated_title or ""),
        ("meta_description", generated_description or ""),
        ("meta_h1", optimised_h1 or ""),
        ("page_copy", _full_page_copy_text(section_results)),
    ]
    for index, item in enumerate(faq_items or []):
        if isinstance(item, dict):
            outputs.append((f"faq_{index + 1}", f"{item.get('question', '')}\n{item.get('answer', '')}"))
    return outputs


def _add_exclamation_flags(flags: list[dict], outputs: list[tuple[str, str]]):
    for output, text in outputs:
        if "!" in text:
            _add_qa_flag(
                flags,
                "exclamation_mark_present",
                f"Exclamation mark found in {output.replace('_', ' ')}.",
                output,
                "!",
                severity="review",
            )


def _add_b2b_consumer_cta_flags(
    flags: list[dict],
    business_type: str,
    outputs: list[tuple[str, str]],
):
    if str(business_type or "").casefold() != "b2b":
        return
    for output, text in outputs:
        for phrase in _B2B_CONSUMER_CTAS:
            if _contains_forbidden_phrase(text, phrase):
                _add_qa_flag(
                    flags,
                    "b2b_consumer_cta",
                    f'Consumer CTA "{phrase}" found in B2B {output.replace("_", " ")}.',
                    output,
                    phrase,
                    severity="review",
                )


def _add_brand_in_h1_flags(
    flags: list[dict],
    brand_name: str,
    gen_meta: bool,
    gen_page_copy: bool,
    optimised_h1: str,
    section_results: dict,
):
    brand = str(brand_name or "").strip()
    if not brand:
        return
    candidates = []
    if gen_meta and str(optimised_h1 or "").strip():
        candidates.append(("meta_h1", str(optimised_h1)))
    page_h1 = _extract_first_page_h1(section_results) if gen_page_copy else ""
    if page_h1:
        candidates.append(("page_h1", page_h1))
    for output, h1 in candidates:
        if _contains_forbidden_phrase(h1, brand):
            _add_qa_flag(
                flags,
                "brand_name_in_h1",
                "H1 contains the brand name, which conflicts with the AIO headline rule.",
                output,
                brand,
                severity="review",
            )


def _add_meta_length_flags(
    flags: list[dict],
    gen_meta: bool,
    generated_title: str,
    generated_description: str,
):
    if not gen_meta:
        return
    title = str(generated_title or "").strip()
    description = str(generated_description or "").strip()
    if title and not META_TITLE_PREFERRED_MIN <= len(title) <= META_TITLE_PREFERRED_MAX:
        severity = "review" if len(title) > 90 else "warning"
        _add_qa_flag(
            flags,
            "meta_title_outside_preferred_range",
            f"Meta title is {len(title)} characters; the preferred range is "
            f"{META_TITLE_PREFERRED_MIN} to {META_TITLE_PREFERRED_MAX}.",
            "meta_title",
            severity=severity,
        )
        flags[-1].update({
            "actual_length": len(title),
            "preferred_min": META_TITLE_PREFERRED_MIN,
            "preferred_max": META_TITLE_PREFERRED_MAX,
        })
    if description and not META_DESCRIPTION_PREFERRED_MIN <= len(description) <= META_DESCRIPTION_PREFERRED_MAX:
        severity = "review" if len(description) > 200 else "warning"
        _add_qa_flag(
            flags,
            "meta_description_outside_preferred_range",
            f"Meta description is {len(description)} characters; the preferred range is "
            f"{META_DESCRIPTION_PREFERRED_MIN} to {META_DESCRIPTION_PREFERRED_MAX}.",
            "meta_description",
            severity=severity,
        )
        flags[-1].update({
            "actual_length": len(description),
            "preferred_min": META_DESCRIPTION_PREFERRED_MIN,
            "preferred_max": META_DESCRIPTION_PREFERRED_MAX,
        })


def _add_keyword_placement_flags(
    flags: list[dict],
    primary_keyword: str,
    gen_meta: bool,
    gen_page_copy: bool,
    optimised_h1: str,
    section_results: dict,
):
    keyword = str(primary_keyword or "").strip()
    if not keyword:
        return
    if gen_meta and str(optimised_h1 or "").strip() and not _qa_keyword_present(keyword, optimised_h1):
        _add_qa_flag(
            flags,
            "target_keyword_missing_from_h1",
            "Target keyword or a close grammatical variant was not found in the optimised H1.",
            "meta_h1",
            severity="warning",
        )
        flags[-1]["keyword"] = keyword

    if not gen_page_copy:
        return
    page_copy = _full_page_copy_text(section_results)
    body_lines = [
        line
        for line in page_copy.splitlines()
        if not re.match(r"^\s*#{1,6}\s+", line)
    ]
    first_words = _normalise_similarity_text("\n".join(body_lines))[:100]
    if first_words and not _qa_keyword_present(keyword, " ".join(first_words)):
        _add_qa_flag(
            flags,
            "target_keyword_missing_from_first_100_words",
            "Target keyword or a close grammatical variant was not found in the first 100 body words.",
            "page_copy",
            severity="warning",
        )
        flags[-1]["keyword"] = keyword

    h2_labels = []
    for line in page_copy.splitlines():
        match = re.match(r"^\s*##\s+(.+?)\s*$", line)
        if match:
            h2_labels.append(match.group(1))
    if h2_labels and not any(_qa_keyword_present(keyword, label) for label in h2_labels):
        _add_qa_flag(
            flags,
            "target_keyword_missing_from_h2",
            "Target keyword or a close grammatical variant was not found in any H2 heading.",
            "page_copy",
            severity="warning",
        )
        flags[-1]["keyword"] = keyword


def _add_faq_quality_flags(flags: list[dict], faq_items: list):
    seen_questions = set()
    for index, item in enumerate(faq_items or []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        output = f"faq_{index + 1}"
        normalised_question = _normalise_phrase(question.rstrip("?"))
        if normalised_question and normalised_question in seen_questions:
            _add_qa_flag(flags, "duplicate_faq_question", "Duplicate FAQ question found.", output, severity="review")
        seen_questions.add(normalised_question)
        if question and not question.endswith("?"):
            _add_qa_flag(flags, "faq_question_missing_question_mark", "FAQ question does not end with a question mark.", output, severity="warning")
        if question and not answer:
            _add_qa_flag(flags, "faq_answer_missing", "FAQ question has no answer.", output, severity="review")
        combined = f"{question}\n{answer}"
        for topic in _FAQ_RISKY_TOPICS:
            if _contains_forbidden_phrase(combined, topic):
                _add_qa_flag(
                    flags,
                    "faq_risky_mutable_topic",
                    f'FAQ discusses mutable or policy-sensitive topic "{topic}" and needs evidence review.',
                    output,
                    topic,
                    severity="warning",
                )
                break


def _meta_repair_issues(
    generated_title: str,
    generated_description: str,
    optimised_h1: str,
    input_h1: str,
    brand_name: str,
    business_type: str,
    forbidden_phrases: list[str],
) -> list[str]:
    issues = []
    if not str(generated_title or "").strip():
        issues.append("Title is missing.")
    if not str(generated_description or "").strip():
        issues.append("Meta description is missing.")
    if not str(optimised_h1 or "").strip():
        issues.append("Optimised H1 is missing.")
    title_length = len(str(generated_title or ""))
    description_length = len(str(generated_description or ""))
    if generated_title and not META_TITLE_PREFERRED_MIN <= title_length <= META_TITLE_PREFERRED_MAX:
        issues.append(
            f"Title is {title_length} characters; target "
            f"{META_TITLE_PREFERRED_MIN} to {META_TITLE_PREFERRED_MAX}."
        )
    if generated_description and not META_DESCRIPTION_PREFERRED_MIN <= description_length <= META_DESCRIPTION_PREFERRED_MAX:
        issues.append(
            f"Meta description is {description_length} characters; target "
            f"{META_DESCRIPTION_PREFERRED_MIN} to {META_DESCRIPTION_PREFERRED_MAX}."
        )
    if input_h1 and _normalise_phrase(generated_title) == _normalise_phrase(input_h1):
        issues.append("Title duplicates the input H1.")
    if brand_name and _contains_forbidden_phrase(optimised_h1, brand_name):
        issues.append("Optimised H1 contains the brand name.")

    outputs = {
        "title": str(generated_title or ""),
        "description": str(generated_description or ""),
        "H1": str(optimised_h1 or ""),
    }
    for label, text in outputs.items():
        if "!" in text:
            issues.append(f"{label} contains an exclamation mark.")
        for phrase in forbidden_phrases:
            if _contains_forbidden_phrase(text, phrase):
                issues.append(f'{label} contains forbidden phrase "{phrase}".')
        if str(business_type or "").casefold() == "b2b":
            for phrase in _B2B_CONSUMER_CTAS:
                if _contains_forbidden_phrase(text, phrase):
                    issues.append(f'{label} contains B2B-inappropriate CTA "{phrase}".')
    return list(dict.fromkeys(issues))


def _faq_repair_issues(
    faq_items: list,
    business_type: str,
    forbidden_phrases: list[str],
) -> list[str]:
    issues = []
    seen_questions = set()
    for index, item in enumerate(faq_items or []):
        if not isinstance(item, dict):
            issues.append(f"FAQ {index + 1} is not a valid object.")
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        normalised = _normalise_phrase(question.rstrip("?"))
        if not question or not answer:
            issues.append(f"FAQ {index + 1} is missing a question or answer.")
        if question and not question.endswith("?"):
            issues.append(f"FAQ {index + 1} question has no question mark.")
        if normalised and normalised in seen_questions:
            issues.append(f"FAQ {index + 1} duplicates an earlier question.")
        seen_questions.add(normalised)
        combined = f"{question}\n{answer}"
        if "!" in combined:
            issues.append(f"FAQ {index + 1} contains an exclamation mark.")
        for phrase in forbidden_phrases:
            if _contains_forbidden_phrase(combined, phrase):
                issues.append(f'FAQ {index + 1} contains forbidden phrase "{phrase}".')
        if str(business_type or "").casefold() == "b2b":
            for phrase in _B2B_CONSUMER_CTAS:
                if _contains_forbidden_phrase(combined, phrase):
                    issues.append(f'FAQ {index + 1} contains B2B-inappropriate CTA "{phrase}".')
    return list(dict.fromkeys(issues))


def _page_repair_phrases(
    page_copy_text: str,
    repeated_phrases: list[str],
    business_type: str,
    forbidden_phrases: list[str],
) -> list[str]:
    phrases = [str(phrase).strip() for phrase in repeated_phrases if str(phrase).strip()]
    if "!" in str(page_copy_text or ""):
        phrases.append("!")
    for phrase in forbidden_phrases:
        if _contains_forbidden_phrase(page_copy_text, phrase):
            phrases.append(phrase)
    if str(business_type or "").casefold() == "b2b":
        for phrase in _B2B_CONSUMER_CTAS:
            if _contains_forbidden_phrase(page_copy_text, phrase):
                phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _add_keyword_presence_flags(
    flags: list[dict],
    primary_keyword: str,
    gen_meta: bool,
    gen_page_copy: bool,
    meta_text: str,
    page_copy_text: str,
):
    keyword = (primary_keyword or "").strip()
    if not keyword:
        return

    if gen_meta and meta_text.strip() and not _keyword_present(keyword, meta_text):
        flags.append({
            "code": "target_keyword_missing_from_meta",
            "message": "Target keyword was not found in the generated meta output.",
            "output": "meta",
            "keyword": keyword,
        })

    if gen_page_copy and page_copy_text.strip() and not _keyword_present(keyword, page_copy_text):
        flags.append({
            "code": "target_keyword_missing_from_page_copy",
            "message": "Target keyword was not found in the generated page copy.",
            "output": "page_copy",
            "keyword": keyword,
        })


def _add_section_word_count_flags(flags: list[dict], section_results: dict, template: dict | None):
    if not section_results or not template:
        return

    for section in template.get("sections", []):
        section_name = section.get("name", "")
        text = section_results.get(section_name, "")
        if not str(text or "").strip():
            continue

        target = section.get("word_count") or []
        if len(target) != 2:
            continue
        target_min, target_max = target
        actual_words = _word_count_for_qa(str(text))
        tolerated_min = int(target_min * 0.8)
        tolerated_max = int(target_max * 1.2)

        if actual_words < tolerated_min:
            flags.append({
                "code": "section_word_count_below_target",
                "message": f"Section '{section.get('label', section_name)}' is shorter than the target range.",
                "output": "page_copy",
                "section": section_name,
                "section_label": section.get("label", section_name),
                "actual_words": actual_words,
                "target_min": target_min,
                "target_max": target_max,
            })
        elif actual_words > tolerated_max:
            flags.append({
                "code": "section_word_count_above_target",
                "message": f"Section '{section.get('label', section_name)}' is longer than the target range.",
                "output": "page_copy",
                "section": section_name,
                "section_label": section.get("label", section_name),
                "actual_words": actual_words,
                "target_min": target_min,
                "target_max": target_max,
            })


def _template_for_page_copy(template: dict, separate_faq_output_enabled: bool) -> dict:
    page_template = deepcopy(template)
    if not separate_faq_output_enabled:
        return page_template

    sections = page_template.get("sections") or []
    used_names = {section.get("name", "") for section in sections}
    adjusted_sections = []

    for section in sections:
        name = str(section.get("name", "")).lower()
        label = str(section.get("label", "")).lower()
        is_faq_section = "faq" in name or label == "frequently asked questions"
        if not is_faq_section:
            adjusted_sections.append(section)
            continue

        support_name = "support_notes"
        suffix = 2
        while support_name in used_names:
            support_name = f"support_notes_{suffix}"
            suffix += 1
        used_names.add(support_name)

        adjusted_sections.append({
            **section,
            "name": support_name,
            "label": "Final Decision Notes",
            "purpose": (
                "Short non-Q&A support section that summarises practical decision points, "
                "expectations, or next-step considerations without duplicating the separate FAQ output."
            ),
            "word_count": [80, 130],
            "keyword_slot": "lsi",
            "prompt_rules": (
                "Write one compact support section, not a FAQ. "
                "Do not use question headings, Q&A formatting, or 'Frequently Asked Questions'. "
                "Summarise practical considerations that help the reader decide what to do next, using only the available page context, brief, SERP, or competitor signals. "
                "Do not repeat the separate FAQ output. "
                "Do not invent pricing, policies, product details, ratings, guarantees, availability, or claims. "
                "Use the LSI keyword only if it reads naturally. No em dashes."
            ),
        })

    page_template["sections"] = adjusted_sections
    return page_template


def _add_similarity_flag(result: dict, code: str, message: str, output: str, previous_row: int, similarity: float):
    flags = result.setdefault("qa_flags", [])
    if any(flag.get("code") == code and flag.get("output") == output for flag in flags):
        return
    flags.append({
        "code": code,
        "message": message,
        "output": output,
        "severity": "review",
        "similar_to_row": previous_row,
        "similarity": round(similarity, 3),
    })
    if result.get("status") in {"ok", "warning"}:
        result["status"] = "review"


def _apply_cross_row_uniqueness_flags(result: dict, previous_results: list[dict]) -> dict:
    if not result or result.get("status") in {"error", "skipped: no keywords found"}:
        return result

    checks = [
        {
            "field": "generated_title",
            "output": "meta_title",
            "code": "meta_title_similar_to_row",
            "message": "Meta title is very similar to a previous row.",
            "threshold": 0.90,
            "min_tokens": 4,
        },
        {
            "field": "generated_description",
            "output": "meta_description",
            "code": "meta_description_similar_to_row",
            "message": "Meta description is very similar to a previous row.",
            "threshold": 0.82,
            "min_tokens": 8,
        },
        {
            "field": "page_intro",
            "output": "page_intro",
            "code": "page_intro_similar_to_row",
            "message": "The first page-copy section is very similar to a previous row.",
            "threshold": 0.78,
            "min_tokens": 8,
        },
        {
            "field": "page_copy",
            "output": "page_copy",
            "code": "page_copy_similar_to_row",
            "message": "Full page copy is very similar to a previous row.",
            "threshold": 0.85,
            "min_tokens": 80,
        },
    ]

    current_texts = {
        "generated_title": result.get("generated_title") or "",
        "generated_description": result.get("generated_description") or "",
        "page_intro": _first_page_copy_section(result.get("section_results") or {}),
        "page_copy": _full_page_copy_text(result.get("section_results") or {}),
    }

    for check in checks:
        current_text = current_texts[check["field"]]
        if len(_normalise_similarity_text(current_text)) < check["min_tokens"]:
            continue

        best_match = None
        for previous_index, previous in enumerate(previous_results, start=1):
            previous_texts = {
                "generated_title": previous.get("generated_title") or "",
                "generated_description": previous.get("generated_description") or "",
                "page_intro": _first_page_copy_section(previous.get("section_results") or {}),
                "page_copy": _full_page_copy_text(previous.get("section_results") or {}),
            }
            previous_text = previous_texts[check["field"]]
            if len(_normalise_similarity_text(previous_text)) < check["min_tokens"]:
                continue

            similarity = _shingle_similarity(current_text, previous_text)
            if similarity >= check["threshold"] and (best_match is None or similarity > best_match[1]):
                best_match = (previous_index, similarity)

        if best_match:
            _add_similarity_flag(
                result,
                check["code"],
                check["message"],
                check["output"],
                best_match[0],
                best_match[1],
            )

    return result


def _extract_gap_topic_candidates(text: str) -> list[str]:
    tokens = [
        token for token in _normalise_similarity_text(text)
        if len(token) > 3 and token not in _CONTENT_GAP_STOPWORDS
    ]
    candidates = []
    seen = set()
    for index in range(len(tokens) - 1):
        phrase = f"{tokens[index]} {tokens[index + 1]}"
        if phrase not in seen:
            candidates.append(phrase)
            seen.add(phrase)
    if candidates:
        return candidates
    return [token for token in tokens if token not in seen]


def _topic_present_in_text(topic: str, text: str) -> bool:
    topic_tokens = _normalise_similarity_text(topic)
    text_tokens = set(_normalise_similarity_text(text))
    if not topic_tokens or not text_tokens:
        return False
    return any(token in text_tokens for token in topic_tokens)


def _build_content_gap_summary(competitor_section_map: dict, section_results: dict) -> list[dict]:
    diagnostics = []
    if not competitor_section_map or not section_results:
        return diagnostics

    for section, excerpts in competitor_section_map.items():
        generated_text = section_results.get(section, "")
        if not str(generated_text or "").strip():
            continue

        missing_topics = []
        seen = set()
        for excerpt in excerpts or []:
            for topic in _extract_gap_topic_candidates(str(excerpt)):
                if topic in seen or _topic_present_in_text(topic, generated_text):
                    continue
                missing_topics.append(topic)
                seen.add(topic)
                if len(missing_topics) >= 3:
                    break
            if len(missing_topics) >= 3:
                break

        if missing_topics:
            diagnostics.append({
                "section": section,
                "missing_topics": missing_topics,
                "summary": (
                    "Competitors mention "
                    + ", ".join(missing_topics)
                    + " but this section does not clearly cover them."
                ),
            })

    return diagnostics


def _row_topic_text(row: dict) -> str:
    parts = [
        row.get("primary_keyword") or "",
        row.get("h1") or "",
        row.get("generated_title") or "",
        row.get("generated_description") or "",
        row.get("optimised_h1") or "",
        _full_page_copy_text(row.get("section_results") or {}),
    ]
    for item in row.get("faq_items") or []:
        if isinstance(item, dict):
            parts.append(f"{item.get('question', '')} {item.get('answer', '')}")
    return "\n".join(str(part) for part in parts if str(part or "").strip())


def _build_internal_link_suggestions(results: list[dict], max_per_source: int = 3) -> list[dict]:
    eligible = [
        row for row in results
        if (row.get("url") or "").startswith("http")
        and row.get("status") not in {"error", "skipped: invalid URL", "skipped: no keywords found"}
    ]
    suggestions = []

    for source in eligible:
        source_url = source.get("url", "")
        source_text = _row_topic_text(source)
        if not source_text.strip():
            continue

        source_suggestions = []
        source_primary = " ".join(_normalise_similarity_text(source.get("primary_keyword") or ""))
        for target in eligible:
            target_url = target.get("url", "")
            if not target_url or target_url == source_url:
                continue

            anchor = (target.get("primary_keyword") or target.get("h1") or "").strip()
            if not anchor:
                continue
            if source_primary and source_primary == " ".join(_normalise_similarity_text(anchor)):
                continue

            if _keyword_present(anchor, source_text):
                confidence = 0.9
                reason = "Target keyword appears naturally in the source page copy."
            else:
                target_tokens = _meaningful_keyword_tokens(anchor)
                source_tokens = set(_normalise_similarity_text(source_text))
                if not target_tokens:
                    continue
                matched = [token for token in target_tokens if token in source_tokens]
                if len(matched) < max(2, len(target_tokens)):
                    continue
                confidence = min(0.85, 0.6 + (0.08 * len(matched)))
                reason = "Source page topic overlaps with the target page keyword."

            source_suggestions.append({
                "source_url": source_url,
                "target_url": target_url,
                "anchor_text": anchor,
                "confidence": round(confidence, 2),
                "reason": reason,
            })

        source_suggestions.sort(key=lambda item: item["confidence"], reverse=True)
        suggestions.extend(source_suggestions[:max_per_source])

    return suggestions[:200]


def _collect_qa_flags(
    *,
    gen_meta: bool,
    gen_faqs: bool,
    gen_page_copy: bool,
    generated_title: str,
    generated_description: str,
    optimised_h1: str,
    input_h1: str,
    primary_keyword: str,
    faq_items: list,
    section_results: dict,
    forbidden_phrases: list[str],
    template: dict | None = None,
    brand_name: str = "",
    business_type: str = "general",
) -> list[dict]:
    flags = []

    if gen_meta:
        if not (generated_title or "").strip():
            _add_qa_flag(flags, "meta_missing_title", "Meta title was requested but no title was generated.", "meta")
        if not (generated_description or "").strip():
            _add_qa_flag(flags, "meta_missing_description", "Meta description was requested but no description was generated.", "meta")
        if not (optimised_h1 or "").strip():
            _add_qa_flag(flags, "meta_missing_h1", "Optimised H1 was requested but no H1 was generated.", "meta")
        if (generated_title or "").strip().lower() == (input_h1 or "").strip().lower() and (input_h1 or "").strip():
            _add_qa_flag(flags, "meta_title_matches_h1", "Generated title matches the input H1.", "meta")

    if gen_faqs and not faq_items:
        _add_qa_flag(flags, "faq_missing", "FAQs were requested but no FAQ items were generated.", "faq")

    page_copy_text = "\n\n".join(str(v) for k, v in (section_results or {}).items() if not str(k).startswith("_"))
    if gen_page_copy and not page_copy_text.strip():
        _add_qa_flag(flags, "page_copy_missing", "Page copy was requested but no page sections were generated.", "page_copy")
    elif gen_page_copy:
        _add_section_word_count_flags(flags, section_results, template)
        _add_repeated_phrase_flags(flags, page_copy_text)

    if gen_meta:
        _add_generic_opener_flags(flags, generated_description or "", section_results if gen_page_copy else {})
    elif gen_page_copy:
        _add_generic_opener_flags(flags, "", section_results)

    if gen_meta and gen_page_copy:
        _add_h1_alignment_flag(flags, optimised_h1 or "", section_results)

    meta_text = "\n".join([generated_title or "", generated_description or "", optimised_h1 or ""])
    _add_keyword_presence_flags(
        flags,
        primary_keyword=primary_keyword,
        gen_meta=gen_meta,
        gen_page_copy=gen_page_copy,
        meta_text=meta_text,
        page_copy_text=page_copy_text,
    )
    _add_keyword_placement_flags(
        flags,
        primary_keyword=primary_keyword,
        gen_meta=gen_meta,
        gen_page_copy=gen_page_copy,
        optimised_h1=optimised_h1,
        section_results=section_results,
    )
    _add_meta_length_flags(
        flags,
        gen_meta=gen_meta,
        generated_title=generated_title,
        generated_description=generated_description,
    )
    _add_brand_in_h1_flags(
        flags,
        brand_name=brand_name,
        gen_meta=gen_meta,
        gen_page_copy=gen_page_copy,
        optimised_h1=optimised_h1,
        section_results=section_results,
    )
    _add_faq_quality_flags(flags, faq_items)

    outputs = _generated_output_texts(
        generated_title,
        generated_description,
        optimised_h1,
        faq_items,
        section_results,
    )
    _add_exclamation_flags(flags, outputs)
    _add_b2b_consumer_cta_flags(flags, business_type, outputs)

    seen_matches = set()
    for output, text in outputs:
        for phrase in forbidden_phrases:
            key = (output, phrase.lower())
            if key in seen_matches:
                continue
            if _contains_forbidden_phrase(text, phrase):
                _add_qa_flag(
                    flags,
                    "forbidden_phrase",
                    f"Forbidden phrase found in {output}.",
                    output,
                    phrase,
                )
                seen_matches.add(key)

    for flag in flags:
        flag.setdefault("severity", "review")
    return flags


def _process_single_row(
    row: dict,
    settings: dict,
    gsc_client,
    branded_terms: list,
    used_keywords: set,
    sb,
    job_id: str,
    row_num: int,
    total_rows: int,
    user_id: str = "",
    brand_profile: dict = None,
    gsc_auth_method: str = "disabled",
) -> dict:
    row_started_at = time.monotonic()

    def step(msg: str):
        _update_job(sb, job_id, user_id, {"current_step": f"Row {row_num}/{total_rows}: {msg}"})

    url          = (row.get("url") or "").strip()
    manual_kws   = [k.strip() for k in (row.get("keyword") or "").split(",") if k.strip()]
    h1_raw       = (row.get("h1") or "").strip()
    h1           = "" if h1_raw.lower() == "none" else h1_raw
    page_type    = normalize_page_type(row.get("page_type") or settings.get("page_type", "service"), default="service")
    template_key = row.get("template_key") or settings.get("template_key") or default_template_key_for_page_type(page_type)
    if page_type == "landing_page" and template_key == "service_page":
        template_key = default_template_key_for_page_type(page_type)

    # What to generate — from row overrides or job-level settings
    gen_page_copy = row.get("gen_page_copy", settings.get("gen_page_copy", True))
    gen_meta      = row.get("gen_meta",      settings.get("gen_meta",      True))
    gen_faqs      = row.get("gen_faqs",      settings.get("gen_faqs",      True))
    num_faqs      = int(row.get("num_faqs",  settings.get("num_faqs",      5)))

    dfs_login    = settings["dfs_login"]
    dfs_password = settings["dfs_password"]
    provider     = settings.get("provider", "Claude")
    model        = settings.get("model") or None
    api_key      = settings.get("api_key", "")
    brand_name   = settings.get("brand_name", "")
    business_type = settings.get("business_type", "general")
    min_volume   = int(settings.get("min_volume", 10))
    location_code = int(settings.get("location_code", 2840))
    include_brand = settings.get("include_brand", True)
    forbidden_phrases = settings.get("forbidden_phrases", "")
    forbidden_phrase_list = _split_forbidden_phrases(
        forbidden_phrases,
        (brand_profile or {}).get("words_to_avoid", ""),
    )
    forbidden_phrase_text = ", ".join(forbidden_phrase_list)

    # Build client brief — merge niche context + brand profile + custom brief
    explicit_client_brief = settings.get("client_brief", "") or ""
    client_brief = explicit_client_brief
    _niche_ctx = get_niche_context(settings.get("niche", ""))
    if _niche_ctx:
        client_brief = (client_brief + "\n\n" + _niche_ctx).strip()
    if brand_profile:
        parts = []
        if brand_profile.get("tone_of_voice"):
            parts.append("Tone of voice: " + brand_profile["tone_of_voice"])
        if brand_profile.get("key_messages"):
            parts.append("Key messages: " + brand_profile["key_messages"])
        if brand_profile.get("words_to_avoid"):
            parts.append("Words to avoid: " + brand_profile["words_to_avoid"])
        if brand_profile.get("guidelines"):
            parts.append(brand_profile["guidelines"])
        if parts:
            client_brief = (client_brief + "\n\n" + "\n".join(parts)).strip()
    brand_context = _build_brand_context(brand_profile, settings.get("niche", ""))

    use_gsc  = settings.get("use_gsc", False)
    site_url = settings.get("site_url", "")
    jina_key = settings.get("jina_api_key", "")

    run_diagnostics = {
        "provider": provider,
        "model": model or "",
        "gsc_auth_method": gsc_auth_method,
        "page_type": page_type,
        "template_key": template_key,
        "generation_requested": {
            "meta": bool(gen_meta),
            "faqs": bool(gen_faqs),
            "page_copy": bool(gen_page_copy),
        },
        "input_signal_counts": {
            "manual_keywords": len(manual_kws),
            "dfs_ranked": 0,
            "gsc_queries": 0,
            "keyword_pool": 0,
            "ranked_keywords": 0,
            "serp_organic": 0,
            "paa_questions": 0,
            "ai_overview_sections": 0,
            "competitors_scraped": 0,
            "page_context_chars": 0,
            "scraped_page_chars": 0,
        },
        "scrape": {
            "page_context_success": False,
            "client_existing_content_success": False,
        },
        "output_counts": {
            "faq_items": 0,
            "sections": 0,
            "word_count": 0,
        },
        "duration_ms": 0,
    }

    def _finish_diagnostics() -> dict:
        run_diagnostics["duration_ms"] = int((time.monotonic() - row_started_at) * 1000)
        return run_diagnostics

    def _empty(status: str) -> dict:
        return {
            "url": url, "primary_keyword": None, "keyword_source": status,
            "gsc_auth_method": gsc_auth_method,
            "generated_title": None, "generated_description": None, "optimised_h1": None,
            "faq_items": [], "faq_schema": None,
            "word_count": 0, "template_name": None,
            "competitor_urls": [], "docx_b64": None, "status": status,
            "run_diagnostics": _finish_diagnostics(),
        }

    if not url or not url.startswith("http"):
        return _empty("skipped: invalid URL")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1 — Keyword pipeline (shared across all outputs)
    # ─────────────────────────────────────────────────────────────────────
    step("fetching DFS ranked keywords...")
    dfs_ranked = []
    try:
        dfs_ranked = get_ranked_keywords_for_url(url, dfs_login, dfs_password, location_code)
        run_diagnostics["input_signal_counts"]["dfs_ranked"] = len(dfs_ranked)
        step("DFS ranked: " + str(len(dfs_ranked)) + " keywords found")
    except Exception as e:
        step("⚠ DFS ranked failed: " + str(e)[:60])

    # Optional GSC layer
    gsc_queries = []
    if use_gsc and gsc_client and site_url:
        step("fetching GSC queries...")
        try:
            gsc_queries = get_top_queries_for_url(gsc_client, site_url, url, top_n=10)
            run_diagnostics["input_signal_counts"]["gsc_queries"] = len(gsc_queries)
            step("GSC: " + str(len(gsc_queries)) + " queries")
        except Exception:
            pass

    all_kws = list({r["keyword"] for r in dfs_ranked} | set(manual_kws) | {q["query"] for q in gsc_queries})
    all_kws = [k for k in all_kws if k]

    vol_map = {}
    diff_map = {}
    if all_kws:
        step("fetching keyword volumes...")
        try:
            vol_map = get_search_volume(all_kws, dfs_login, dfs_password, location_code)
        except Exception as e:
            step("DataForSEO keyword volume failed: " + str(e)[:120])
        try:
            diff_map = get_keyword_difficulty(all_kws, dfs_login, dfs_password, location_code)
        except Exception as e:
            step("DataForSEO keyword difficulty failed: " + str(e)[:120])

    pool   = merge_keyword_pools(gsc_queries, dfs_ranked, manual_kws, vol_map, diff_map)
    run_diagnostics["input_signal_counts"]["keyword_pool"] = len(pool)
    pool   = [k for k in pool if k.get("volume", 0) >= min_volume]
    ranked = rank_keywords(pool, branded_terms, h1=h1, exclude_position_one=True)
    ranked = [k for k in ranked if not k.get("branded")]
    manual_primary = manual_kws[0] if manual_kws else ""

    if manual_primary:
        manual_key = manual_primary.lower()
        existing_manual = next(
            (k for k in pool if str(k.get("keyword", "")).strip().lower() == manual_key),
            {},
        )
        manual_entry = {
            **existing_manual,
            "keyword": manual_primary,
            "volume": existing_manual.get("volume", vol_map.get(manual_primary, 0)),
            "difficulty": existing_manual.get("difficulty", diff_map.get(manual_primary, 1)),
            "score": existing_manual.get("score", 1.0),
            "branded": False,
        }
        ranked = [manual_entry] + [
            k for k in ranked
            if str(k.get("keyword", "")).strip().lower() != manual_key
        ]
        keyword_source = "manual"
    else:
        keyword_source = "dfs+gsc" if gsc_queries else "dfs"

    run_diagnostics["input_signal_counts"]["ranked_keywords"] = len(ranked)

    if not ranked and manual_kws:
        ranked = [{"keyword": k, "volume": 10, "difficulty": 1, "score": 1.0} for k in manual_kws]
        run_diagnostics["input_signal_counts"]["ranked_keywords"] = len(ranked)
        keyword_source = "manual"

    if not ranked and not use_gsc and h1:
        ranked = [{"keyword": h1, "volume": 0, "difficulty": 50, "score": 0.0}]
        run_diagnostics["input_signal_counts"]["ranked_keywords"] = len(ranked)
        keyword_source = "h1 fallback"

    if not ranked:
        step("✗ no keywords found — skipping")
        return _empty("skipped: no keywords found")

    primary_keyword = ranked[0]["keyword"]
    if not manual_primary and primary_keyword.lower() in used_keywords:
        for r in ranked[1:]:
            if r["keyword"].lower() not in used_keywords:
                primary_keyword = r["keyword"]
                break
    used_keywords.add(primary_keyword.lower())

    if not h1 and primary_keyword:
        h1 = primary_keyword.title()

    step("keyword: " + primary_keyword)

    # ─────────────────────────────────────────────────────────────────────
    # STEP 2 — SERP (shared: organic URLs + PAA + AI Overview)
    # ─────────────────────────────────────────────────────────────────────
    step("fetching SERP...")
    serp_data       = {"organic": [], "paa_items": [], "ai_overview": ""}
    paa_questions   = []
    ai_overview     = ""
    ai_overview_sections = []
    organic_results = []
    try:
        serp_data       = get_serp_data(dfs_login, dfs_password, primary_keyword, location_code)
        if serp_data.get("error"):
            step("DataForSEO SERP failed: " + str(serp_data["error"])[:120])
        paa_questions   = serp_data.get("paa_items") or serp_data.get("paa") or []
        ai_overview_sections = serp_data.get("ai_overview_sections") or []
        ai_overview     = serp_data.get("ai_overview_raw") or serp_data.get("ai_overview") or ""
        organic_results = serp_data.get("organic") or []
        run_diagnostics["input_signal_counts"]["paa_questions"] = len(paa_questions)
        run_diagnostics["input_signal_counts"]["ai_overview_sections"] = len(ai_overview_sections)
        run_diagnostics["input_signal_counts"]["serp_organic"] = len(organic_results)
        step("SERP: " + ("AIO ✓" if ai_overview else "AIO ✗") + ", PAA: " + str(len(paa_questions)) + ", organic: " + str(len(organic_results)))
    except Exception as e:
        step("⚠ SERP failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 3 — Owned-page context (shared by strategy and every output)
    # ─────────────────────────────────────────────────────────────────────
    page_context = ""
    scraped_page_content = ""
    if gen_faqs or gen_meta or gen_page_copy:
        step("scraping page context...")
        try:
            sc = scrape_page_context(jina_key, url)
            if sc.get("success"):
                scraped_page_content = sc["content"]
                run_diagnostics["scrape"]["page_context_success"] = True
                run_diagnostics["input_signal_counts"]["scraped_page_chars"] = len(scraped_page_content)
                page_context = scraped_page_content
                step("page context: " + str(len(page_context)) + " chars")
            else:
                fallback = scrape_url(url, api_key=jina_key)
                if fallback.get("success"):
                    scraped_page_content = str(fallback.get("body_text") or "")[:10000]
                    page_context = scraped_page_content
                    run_diagnostics["scrape"]["page_context_success"] = bool(scraped_page_content)
                    run_diagnostics["input_signal_counts"]["scraped_page_chars"] = len(scraped_page_content)
                    step("page context loaded with fallback scraper")
        except Exception as e:
            step("owned-page scrape unavailable: " + str(e)[:60])

        if client_brief:
            page_context = (page_context + "\n\n" + client_brief).strip()
        run_diagnostics["input_signal_counts"]["page_context_chars"] = len(page_context)

    # ─────────────────────────────────────────────────────────────────────
    # STEP 4 — Competitor scraping (for page copy)
    # ─────────────────────────────────────────────────────────────────────
    competitor_urls_used = []
    competitor_section_map = {}
    kw_assignment = {}
    lsi_map = {}
    template = None

    if gen_page_copy:
        step("resolving template...")
        custom_template_text = settings.get("custom_template_text", "").strip()
        if custom_template_text:
            template = parse_custom_template(custom_template_text, page_type)
        else:
            try:
                template = get_template(template_key)
            except ValueError:
                template = get_template(default_template_key_for_page_type(page_type))
        template = _template_for_page_copy(template, bool(gen_faqs))

        kw_assignment = assign_keywords_to_sections(ranked, template["sections"])
        competitor_section_map = {s["name"]: [] for s in template["sections"]}

        step("scraping competitors...")
        client_domain = urlparse(url).netloc
        if organic_results:
            try:
                scored = []
                for sr in organic_results[:8]:
                    comp_url = sr.get("url") or sr.get("link") or sr.get("relative_url") or ""
                    if not comp_url.startswith("http"):
                        continue
                    if client_domain and client_domain in urlparse(comp_url).netloc:
                        continue
                    sc = scrape_url(comp_url, api_key=jina_key)
                    if not sc["success"]:
                        continue
                    if not is_editorial_competitor(sc, page_type):
                        continue
                    sc["relevance"] = classify_competitor_relevance(sc, business_type, page_type)
                    sc["comp_url"]  = comp_url
                    scored.append(sc)
                scored.sort(key=lambda x: x["relevance"], reverse=True)
                top = scored[:3]
                competitor_urls_used = [c["comp_url"] for c in top]
                run_diagnostics["input_signal_counts"]["competitors_scraped"] = len(competitor_urls_used)
                if top:
                    competitor_section_map = map_competitor_sections(top, template["sections"])
                step("competitors: " + str(len(competitor_urls_used)) + " scraped")
            except Exception as e:
                step("⚠ competitor scrape failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 5 — Generate strategy brief, then meta copy
    # ─────────────────────────────────────────────────────────────────────
    strategy_brief = {}
    strategy_status = "not_requested"
    strategy_issues = []
    required_strategy_outputs = [
        output
        for output, enabled in (
            ("meta", gen_meta),
            ("faq", gen_faqs),
            ("page_copy", gen_page_copy),
        )
        if enabled
    ]
    if gen_meta or gen_faqs or gen_page_copy:
        step("building strategy brief...")
        try:
            strategy_brief = generate_strategy_brief(
                provider=provider,
                api_key=api_key,
                model=model,
                url=url,
                keyword=primary_keyword,
                page_type=page_type,
                business_type=business_type,
                brand_name=brand_name,
                h1=h1,
                brand_context=brand_context,
                client_brief=client_brief,
                evidence_client_brief=explicit_client_brief,
                page_context=scraped_page_content,
                ai_overview=ai_overview,
                paa_questions=paa_questions,
                competitor_section_map=competitor_section_map,
                template_sections=(template or {}).get("sections", []),
                required_outputs=required_strategy_outputs,
            )
            strategy_issues = strategy_brief_issues(
                strategy_brief,
                (template or {}).get("sections", []),
                required_strategy_outputs,
            )
            strategy_status = "ready" if not strategy_issues else "needs_review"
            step("strategy brief ready" if not strategy_issues else "strategy brief needs review")
        except Exception as e:
            strategy_brief = {}
            strategy_status = "unavailable"
            strategy_issues = [str(e)[:160] or "Strategy generation failed."]
            step("strategy brief unavailable: " + str(e)[:60])

    generated_title = None
    generated_description = None
    optimised_h1 = None
    input_h1_for_qa = h1

    if gen_meta:
        step("generating meta copy...")
        try:
            meta_context_parts = []
            if scraped_page_content:
                meta_context_parts.append("SCRAPED PAGE CONTENT:\n" + scraped_page_content[:10000])
            if client_brief:
                meta_context_parts.append("CLIENT BRIEF:\n" + client_brief)
            meta_result = generate_copy(
                provider=provider,
                api_key=api_key,
                model=model,
                url=url,
                keyword=primary_keyword,
                page_type=page_type,
                brand_name=brand_name if include_brand else "",
                forbidden_phrases=forbidden_phrase_text,
                context="\n\n".join(meta_context_parts),
                brand_context=brand_context,
                business_type=business_type,
                h1=h1,
                strategy_brief=strategy_brief,
            )
            generated_title       = meta_result.get("title", "")
            generated_description = meta_result.get("description", "")
            optimised_h1          = meta_result.get("h1_optimised", "")
            meta_issues = _meta_repair_issues(
                generated_title,
                generated_description,
                optimised_h1,
                input_h1_for_qa,
                brand_name if include_brand else "",
                business_type,
                forbidden_phrase_list,
            )
            if meta_issues:
                try:
                    repaired_meta = repair_meta_copy(
                        provider=provider,
                        api_key=api_key,
                        model=model,
                        current={
                            "title": generated_title,
                            "description": generated_description,
                            "h1_optimised": optimised_h1,
                        },
                        issues=meta_issues,
                        url=url,
                        keyword=primary_keyword,
                        page_type=page_type,
                        business_type=business_type,
                        brand_name=brand_name if include_brand else "",
                        input_h1=input_h1_for_qa,
                        forbidden_phrases=forbidden_phrase_text,
                        context="\n\n".join(meta_context_parts),
                        brand_context=brand_context,
                        strategy_brief=strategy_brief,
                    )
                    if all(str(repaired_meta.get(key) or "").strip() for key in ("title", "description", "h1_optimised")):
                        generated_title = repaired_meta["title"]
                        generated_description = repaired_meta["description"]
                        optimised_h1 = repaired_meta["h1_optimised"]
                        step("meta copy repaired")
                except Exception as e:
                    step("meta repair unavailable: " + str(e)[:60])
            # Use optimised H1 as page H1 if we didn't have one
            if not h1 and optimised_h1:
                h1 = optimised_h1
            step("✓ meta — title: " + str(len(generated_title or "")) + " chars, desc: " + str(len(generated_description or "")) + " chars")
        except Exception as e:
            step("⚠ meta failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 6 — Generate FAQs
    # ─────────────────────────────────────────────────────────────────────
    faq_items  = []
    faq_schema = None
    faq_script = None

    if gen_faqs:
        step("generating FAQs...")
        try:
            from utils.dfs import get_serp_data as _gsd
            ai_ov_for_faq = ai_overview
            paa_for_faq   = paa_questions

            faq_items = generate_faq(
                provider=provider,
                api_key=api_key,
                model=model,
                keyword=primary_keyword,
                page_type=page_type,
                brand_name=brand_name if include_brand else "",
                business_type=business_type,
                h1=h1,
                num_faqs=num_faqs,
                paa_items=paa_for_faq,
                ai_overview_sections=ai_overview_sections,
                ai_overview_raw=ai_ov_for_faq,
                forbidden_phrases=forbidden_phrases,
                page_context=page_context,
                brand_profile=brand_profile,
                strategy_brief=strategy_brief,
            )
            faq_items, faqs_trimmed = _limit_faq_items(faq_items, num_faqs)
            if faqs_trimmed:
                step("FAQs trimmed to requested count: " + str(len(faq_items)))
            faq_issues = _faq_repair_issues(faq_items, business_type, forbidden_phrase_list)
            if faq_issues:
                try:
                    repaired_faqs = repair_faq_items(
                        provider=provider,
                        api_key=api_key,
                        model=model,
                        faq_items=faq_items,
                        issues=faq_issues,
                        keyword=primary_keyword,
                        page_type=page_type,
                        business_type=business_type,
                        brand_name=brand_name if include_brand else "",
                        num_faqs=num_faqs,
                        page_context=page_context,
                        forbidden_phrases=forbidden_phrase_text,
                        strategy_brief=strategy_brief,
                    )
                    repaired_faqs, _ = _limit_faq_items(repaired_faqs, num_faqs)
                    if len(repaired_faqs) == num_faqs:
                        faq_items = repaired_faqs
                        step("FAQ quality issues repaired")
                except Exception as e:
                    step("FAQ repair unavailable: " + str(e)[:60])
            step("✓ FAQs: " + str(len(faq_items)) + " generated")

            # Build schema
            from utils.dfs import _extract_ai_overview_text
            try:
                from faq_saas_schema import build_faq_schema
            except ImportError:
                pass

            # Inline FAQ schema builder
            def _build_faq_schema(items):
                entities = [{"@type": "Question", "name": i["question"], "acceptedAnswer": {"@type": "Answer", "text": i["answer"]}} for i in items]
                schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}
                import json
                raw_json = json.dumps(schema, ensure_ascii=False, indent=2)
                script = f"<script type=\"application/ld+json\">\n{raw_json}\n</script>"
                return raw_json, script

            faq_schema, faq_script = _build_faq_schema(faq_items)
            run_diagnostics["output_counts"]["faq_items"] = len(faq_items)

        except Exception as e:
            step("⚠ FAQs failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 7 — Generate full page copy
    # ─────────────────────────────────────────────────────────────────────
    section_results = {}
    full_page       = ""
    word_count      = 0
    page_repair_attempts = 0
    page_repair_failed = False

    if gen_page_copy and template:
        step("generating page copy (" + str(len(template["sections"])) + " sections)...")

        # LSI keywords
        supporting_kws = list({v.get("supporting", "") for v in kw_assignment.values() if v.get("supporting")})
        for sk in supporting_kws[:3]:
            try:
                ideas = get_keyword_ideas(sk, dfs_login, dfs_password, location_code, limit=10)
                lsi_map[sk] = [i["keyword"] for i in ideas[:3]]
            except Exception:
                lsi_map[sk] = []
                step("Related keyword ideas unavailable; continuing without them.")

        # Client existing content
        client_existing_content = ""
        if scraped_page_content:
            client_existing_content = scraped_page_content[:800]
            run_diagnostics["scrape"]["client_existing_content_success"] = True
        else:
            try:
                existing = scrape_url(url, api_key=jina_key)
                if existing["success"]:
                    client_existing_content = existing.get("body_text", "")[:800]
                    run_diagnostics["scrape"]["client_existing_content_success"] = bool(client_existing_content)
            except Exception:
                pass

        try:
            def on_section(i, total, label):
                step("section " + str(i+1) + "/" + str(total) + ": " + label)
                if _is_cancelled(sb, job_id, user_id):
                    raise InterruptedError("cancelled")

            page_result = generate_page(
                template=template,
                keyword_assignment=kw_assignment,
                lsi_keywords=lsi_map,
                business_type=business_type,
                brand_name=brand_name,
                h1=h1,
                page_type=page_type,
                paa_questions=paa_questions,
                ai_overview=ai_overview,
                competitor_section_map=competitor_section_map,
                client_brief=client_brief,
                client_existing_content=client_existing_content,
                provider=provider,
                api_key=api_key,
                model=model,
                forbidden_phrases=forbidden_phrase_text,
                progress_callback=on_section,
                strategy_brief=strategy_brief,
            )
            section_results = {k: v for k, v in page_result.items() if not k.startswith("_")}
            full_page       = page_result.get("_full_page", "")
            word_count      = page_result.get("_word_count", 0)
            section_results, h1_replaced = _enforce_canonical_page_h1(section_results, optimised_h1 or "")
            if h1_replaced:
                full_page = _assemble_full_page_copy(section_results, template)
                word_count = len(full_page.split())
                step("page copy H1 aligned to meta H1")
            repeated_phrases = [
                item["phrase"]
                for item in _repeated_phrase_candidates(full_page or _full_page_copy_text(section_results))
            ]
            repair_phrases = _page_repair_phrases(
                full_page or _full_page_copy_text(section_results),
                repeated_phrases,
                business_type,
                forbidden_phrase_list,
            )
            if repair_phrases:
                page_repair_attempts += 1
                try:
                    repaired_sections = repair_repeated_page_copy(
                        section_results=section_results,
                        repeated_phrases=repair_phrases,
                        template=template,
                        strategy_brief=strategy_brief,
                        brand_name=brand_name,
                        provider=provider,
                        api_key=api_key,
                        model=model,
                    )
                    if repaired_sections != section_results:
                        section_results = repaired_sections
                        section_results, _ = _enforce_canonical_page_h1(section_results, optimised_h1 or "")
                        full_page = _assemble_full_page_copy(section_results, template)
                        word_count = len(full_page.split())
                        step("page copy quality issues repaired")
                except Exception as e:
                    page_repair_failed = True
                    step("page copy repetition repair unavailable: " + str(e)[:60])
            run_diagnostics["output_counts"]["sections"] = len(section_results)
            run_diagnostics["output_counts"]["word_count"] = word_count
            step("✓ page copy: " + str(word_count) + " words")
        except InterruptedError:
            raise
        except Exception as e:
            step("⚠ page copy failed: " + str(e)[:80])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 8 — Review assembled outputs against the evidence contract
    # ─────────────────────────────────────────────────────────────────────
    if gen_page_copy and section_results:
        section_results, _ = _enforce_canonical_page_h1(section_results, optimised_h1 or "")
        full_page = _assemble_full_page_copy(section_results, template)
        word_count = len(full_page.split())
        residual_phrases = _page_repair_phrases(
            full_page,
            [item["phrase"] for item in _repeated_phrase_candidates(full_page)],
            business_type,
            forbidden_phrase_list,
        )
        if residual_phrases and page_repair_attempts < 2 and not page_repair_failed:
            page_repair_attempts += 1
            try:
                repaired_sections = repair_repeated_page_copy(
                    section_results=section_results,
                    repeated_phrases=residual_phrases,
                    template=template,
                    strategy_brief=strategy_brief,
                    brand_name=brand_name,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                )
                if repaired_sections != section_results:
                    section_results = repaired_sections
                    section_results, _ = _enforce_canonical_page_h1(section_results, optimised_h1 or "")
                    full_page = _assemble_full_page_copy(section_results, template)
                    word_count = len(full_page.split())
                    step("page copy residual issues repaired")
            except Exception as e:
                step("page copy residual repair unavailable: " + str(e)[:60])

    if not input_h1_for_qa and optimised_h1:
        h1 = optimised_h1
    run_diagnostics["output_counts"]["faq_items"] = len(faq_items)
    run_diagnostics["output_counts"]["sections"] = len(section_results)
    run_diagnostics["output_counts"]["word_count"] = word_count

    editorial_review = {"issues": []}
    if strategy_status == "ready" and strategy_brief:
        editorial_outputs = _build_editorial_outputs(
            gen_meta=gen_meta,
            gen_faqs=gen_faqs,
            gen_page_copy=gen_page_copy,
            generated_title=generated_title or "",
            generated_description=generated_description or "",
            optimised_h1=optimised_h1 or "",
            faq_items=faq_items,
            section_results=section_results,
        )
        if editorial_outputs:
            step("reviewing evidence and strategy alignment...")
            try:
                editorial_review = review_output_quality(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    strategy_brief=strategy_brief,
                    outputs=editorial_outputs,
                )
            except Exception as e:
                step("editorial review unavailable: " + str(e)[:60])

    run_diagnostics["output_counts"]["editorial_issues"] = len(editorial_review.get("issues") or [])

    # STEP 9 — Build combined docx
    docx_b64 = None
    step("building combined docx...")
    try:
        docx_bytes = _build_combined_docx(
            url=url,
            h1=h1,
            primary_keyword=primary_keyword,
            page_type=page_type,
            template=template,
            generated_title=generated_title,
            generated_description=generated_description,
            optimised_h1=optimised_h1,
            faq_items=faq_items,
            faq_schema=faq_schema,
            section_results=section_results,
            word_count=word_count,
            competitor_urls=competitor_urls_used,
            gen_meta=gen_meta,
            gen_faqs=gen_faqs,
            gen_page_copy=gen_page_copy,
            keyword_assignment=kw_assignment,
        )
        docx_b64 = base64.b64encode(docx_bytes).decode("utf-8")
        step("✓ done")
    except Exception as e:
        step("⚠ docx failed: " + str(e)[:60])

    stored_competitor_section_map = {
        section: [str(excerpt)[:500] for excerpt in excerpts[:3] if str(excerpt).strip()]
        for section, excerpts in (competitor_section_map or {}).items()
        if excerpts
    }
    content_gap_summary = _build_content_gap_summary(stored_competitor_section_map, section_results)
    qa_flags = _collect_qa_flags(
        gen_meta=gen_meta,
        gen_faqs=gen_faqs,
        gen_page_copy=gen_page_copy,
        generated_title=generated_title or "",
        generated_description=generated_description or "",
        optimised_h1=optimised_h1 or "",
        input_h1=input_h1_for_qa,
        primary_keyword=primary_keyword,
        faq_items=faq_items,
        section_results=section_results,
        forbidden_phrases=forbidden_phrase_list,
        template=template,
        brand_name=brand_name,
        business_type=business_type,
    )
    _add_strategy_qa_flag(qa_flags, strategy_status, strategy_issues)
    _add_editorial_qa_flags(qa_flags, editorial_review)
    brand_consistency = {}
    if settings.get("brand_consistency_check") and brand_profile:
        review_outputs = {}
        if gen_meta and (generated_title or generated_description or optimised_h1):
            review_outputs["meta"] = "\n".join(
                value for value in [generated_title or "", generated_description or "", optimised_h1 or ""] if value
            )
        if gen_faqs and faq_items:
            review_outputs["faqs"] = "\n".join(
                f"{item.get('question', '')}\n{item.get('answer', '')}"
                for item in faq_items
                if isinstance(item, dict)
            )
        if gen_page_copy and (full_page or section_results):
            review_outputs["page_copy"] = full_page or "\n\n".join(str(v) for v in section_results.values())

        if review_outputs:
            try:
                brand_consistency = score_brand_consistency(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    brand_profile=brand_profile,
                    strategy_brief=strategy_brief,
                    outputs=review_outputs,
                )
                brand_consistency["evaluation_mode"] = "same_provider"
                threshold = int(settings.get("brand_consistency_threshold", 70))
                if brand_consistency.get("score", 100) < threshold:
                    _add_qa_flag(
                        qa_flags,
                        "brand_consistency_low",
                        "Brand consistency score is below the review threshold.",
                        "brand_consistency",
                    )
                    qa_flags[-1]["score"] = brand_consistency.get("score")
                    qa_flags[-1]["reason"] = brand_consistency.get("reason", "")
            except Exception:
                brand_consistency = {}

    return {
        "url":                  url,
        "h1":                   h1,
        "input_h1":             input_h1_for_qa,
        "primary_keyword":      primary_keyword,
        "keyword_source":       keyword_source,
        "model":                model or "",
        "gsc_auth_method":      gsc_auth_method,
        "kw_volume":            (ranked[0].get("volume") if ranked else None),
        "generated_title":      generated_title,
        "generated_description": generated_description,
        "optimised_h1":         optimised_h1,
        "title_length":         len(generated_title or ""),
        "description_length":   len(generated_description or ""),
        "faq_items":            faq_items,
        "faq_schema":           faq_schema,
        "faq_count":            len(faq_items),
        "word_count":           word_count,
        "template_name":        template["name"] if template else None,
        "section_results":      section_results,
        "keyword_assignment":   kw_assignment if gen_page_copy else {},
        "lsi_keywords":         lsi_map if gen_page_copy else {},
        "competitor_section_map": stored_competitor_section_map if gen_page_copy else {},
        "content_gap_summary":  content_gap_summary if gen_page_copy else [],
        "strategy_brief":       strategy_brief,
        "strategy_status":      strategy_status,
        "strategy_issues":      strategy_issues,
        "editorial_review":     editorial_review,
        "brand_consistency":    brand_consistency,
        "competitor_urls":      competitor_urls_used,
        "docx_b64":             docx_b64,
        "qa_flags":             qa_flags,
        "run_diagnostics":      _finish_diagnostics(),
        "status":               _qa_status(qa_flags),
    }


def _build_combined_docx(
    url, h1, primary_keyword, page_type, template,
    generated_title, generated_description, optimised_h1,
    faq_items, faq_schema, section_results, word_count, competitor_urls,
    gen_meta, gen_faqs, gen_page_copy,
    keyword_assignment=None,
):
    """Build a single docx with meta, FAQs, and page copy in one document."""
    keyword_assignment = keyword_assignment or {}
    if gen_page_copy and not gen_meta and not gen_faqs and template:
        return build_docx(
            url=url,
            page_type=page_type,
            template_name=template["name"],
            primary_keyword=primary_keyword,
            section_results=section_results,
            template_sections=template.get("sections", []),
            keyword_assignment=keyword_assignment,
            word_count=word_count,
            competitor_urls=competitor_urls,
            h1=h1,
        )

    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    import io

    doc = Document()

    # Title
    title_para = doc.add_heading(h1 or url, level=1)

    # Metadata table
    meta_table = doc.add_table(rows=1, cols=2)
    meta_table.style = "Table Grid"
    meta_table.rows[0].cells[0].text = "URL"
    meta_table.rows[0].cells[1].text = url

    for label, value in [
        ("Primary Keyword", primary_keyword or ""),
        ("Page Type", page_type or ""),
        ("Template", template["name"] if template else ""),
        ("Word Count", str(word_count) if word_count else ""),
    ]:
        row = meta_table.add_row()
        row.cells[0].text = label
        row.cells[1].text = value

    doc.add_paragraph("")

    # META COPY
    if gen_meta and (generated_title or generated_description or optimised_h1):
        doc.add_heading("Meta Copy", level=2)
        if generated_title:
            p = doc.add_paragraph()
            p.add_run("Title Tag: ").bold = True
            p.add_run(generated_title)
            char_note = f"  ({len(generated_title)} chars{'  ⚠ over 80' if len(generated_title) > META_TITLE_PREFERRED_MAX else ''})"
            p.add_run(char_note)
        if generated_description:
            p = doc.add_paragraph()
            p.add_run("Meta Description: ").bold = True
            p.add_run(generated_description)
            char_note = f"  ({len(generated_description)} chars{'  ⚠ over 180' if len(generated_description) > META_DESCRIPTION_PREFERRED_MAX else ''})"
            p.add_run(char_note)
        if optimised_h1:
            p = doc.add_paragraph()
            p.add_run("Optimised H1: ").bold = True
            p.add_run(optimised_h1)
        doc.add_paragraph("")

    # PAGE COPY
    if gen_page_copy and section_results and template:
        doc.add_heading("Page Copy", level=2)
        for section in template.get("sections", []):
            sec_name = section["name"]
            text = section_results.get(sec_name, "")
            if text:
                doc.add_heading(section["label"], level=3)
                for para in text.split("\n\n"):
                    if para.strip():
                        doc.add_paragraph(para.strip())
        doc.add_paragraph("")

    # FAQs
    if gen_faqs and faq_items:
        doc.add_heading("FAQs", level=2)
        for i, faq in enumerate(faq_items, 1):
            q = doc.add_paragraph()
            q.add_run(f"Q{i}: {faq['question']}").bold = True
            a = doc.add_paragraph()
            a.add_run(f"A: {faq['answer']}")
            doc.add_paragraph("")

        if faq_schema:
            doc.add_heading("FAQ Schema JSON-LD", level=3)
            doc.add_paragraph(f'<script type="application/ld+json">\n{faq_schema}\n</script>')

    # Competitors
    if competitor_urls:
        doc.add_heading("Competitors Referenced", level=3)
        for cu in competitor_urls:
            doc.add_paragraph(cu, style="List Bullet")

    if gen_page_copy and template:
        doc.add_heading("Page Copy Diagnostics", level=3)
        diag_table = doc.add_table(rows=1, cols=4)
        diag_table.style = "Table Grid"
        for i, label in enumerate(["Section", "Words", "Keyword Slot", "Keyword Used"]):
            diag_table.rows[0].cells[i].text = label

        for section in template.get("sections", []):
            sec_name = section["name"]
            assign = keyword_assignment.get(sec_name, {})
            keyword_used = assign.get("primary") or assign.get("supporting") or ""
            text = section_results.get(sec_name, "")
            row = diag_table.add_row()
            row.cells[0].text = section["label"]
            row.cells[1].text = str(len(text.split()) if text else 0)
            row.cells[2].text = section.get("keyword_slot", "none")
            row.cells[3].text = keyword_used

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _process_job(
    job_id: str,
    rows: list,
    settings: dict,
    gsc_credentials: dict | None,
    brand_profile: dict = None,
    user_id: str = "",
):
    sb    = get_supabase()
    total = len(rows)

    _update_job(sb, job_id, user_id, {
        "status":       "running",
        "total_rows":   total,
        "current_step": "Starting...",
    })

    # Init GSC
    gsc_client = None
    if settings.get("use_gsc"):
        if not gsc_credentials:
            _update_job(sb, job_id, user_id, {"error": _GSC_UNAVAILABLE_ERROR})
        else:
            try:
                gsc_client = get_gsc_client(gsc_credentials)
            except GscOAuthConfigError:
                _update_job(sb, job_id, user_id, {"error": _GSC_CONFIG_ERROR})
            except RefreshError:
                if gsc_credentials.get("method") == "google_oauth":
                    _update_job(sb, job_id, user_id, {"error": _GSC_RECONNECT_ERROR})
                    ciphertext = gsc_credentials.get("refresh_token_ciphertext")
                    if ciphertext:
                        try:
                            mark_gsc_reconnect_required(sb, user_id, ciphertext)
                        except Exception:
                            pass
                else:
                    _update_job(sb, job_id, user_id, {"error": _GSC_UNAVAILABLE_ERROR})
            except Exception:
                _update_job(sb, job_id, user_id, {"error": _GSC_UNAVAILABLE_ERROR})
    gsc_auth_method = _safe_gsc_auth_method(settings, gsc_credentials, gsc_client)
    if settings.get("use_gsc"):
        _update_job(sb, job_id, user_id, {"current_step": f"GSC auth method: {gsc_auth_method}"})

    branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
    full_brand = settings.get("full_brand_name", "").strip()
    if full_brand:
        branded_terms = list(set(branded_terms + [w.lower() for w in re.findall(r"[a-zA-Z]+", full_brand) if len(w) >= 3]))
    branded_input = settings.get("branded_terms_input", "").strip()
    if branded_input:
        branded_terms = list(set(branded_terms + [t.strip().lower() for t in branded_input.splitlines() if t.strip()]))

    used_keywords: set = set()
    results = []

    for idx, row in enumerate(rows):
        url = (row.get("url") or "").strip()
        _update_job(sb, job_id, user_id, {"current_step": f"Row {idx+1}/{total}: starting — {url}"})

        if _is_cancelled(sb, job_id, user_id):
            _update_job(sb, job_id, user_id, {
                "status":       "cancelled",
                "current_step": f"Cancelled after {idx}/{total} rows.",
                "failed_rows":  sum(1 for r in results if _result_failed(r)),
            })
            return

        try:
            result = _process_single_row(
                row=row, settings=settings, gsc_client=gsc_client,
                branded_terms=branded_terms, used_keywords=used_keywords,
                sb=sb, job_id=job_id, row_num=idx + 1, total_rows=total,
                user_id=user_id,
                brand_profile=brand_profile,
                gsc_auth_method=gsc_auth_method,
            )
        except InterruptedError:
            _update_job(sb, job_id, user_id, {
                "status":       "cancelled",
                "current_step": f"Cancelled during row {idx + 1}.",
                "failed_rows":  sum(1 for r in results if _result_failed(r)),
                "results":      results,
            })
            return
        except Exception as e:
            result = {
                "url": url,
                "error": str(e),
                "status": "error",
                "word_count": 0,
                "docx_b64": None,
                "gsc_auth_method": gsc_auth_method,
            }

        result = _apply_cross_row_uniqueness_flags(result, results)
        results.append(result)
        _update_job(sb, job_id, user_id, {"completed_rows": idx + 1, "results": results})

        if _is_cancelled(sb, job_id, user_id):
            _update_job(sb, job_id, user_id, {
                "status":       "cancelled",
                "current_step": f"Cancelled after {idx + 1}/{total} rows.",
                "failed_rows":  sum(1 for r in results if _result_failed(r)),
            })
            return

    try:
        internal_link_suggestions = _build_internal_link_suggestions(results)
        final_step = "Done."
    except Exception:
        internal_link_suggestions = []
        final_step = "Done. Internal link suggestions unavailable."
    _update_job(sb, job_id, user_id, {
        "status":        "complete",
        "current_step":  final_step,
        "completed_rows": len(results),
        "failed_rows":   sum(1 for r in results if _result_failed(r)),
        "results":       results,
        "internal_link_suggestions": internal_link_suggestions,
    })


# ── Request models ─────────────────────────────────────────────────────────────

class AIORow(BaseModel):
    url: str
    keyword: str = ""
    page_type: str = "service"
    h1: str = ""
    template_key: str = ""
    gen_page_copy: bool = True
    gen_meta: bool = True
    gen_faqs: bool = True
    num_faqs: int = 5


class AIOSettings(BaseModel):
    niche: str = ""
    provider: str = "Claude"
    model: str = ""
    api_key: str = ""
    dfs_login: str = ""
    dfs_password: str = ""
    business_type: str = "general"
    brand_name: str = ""
    full_brand_name: str = ""
    branded_terms_input: str = ""
    include_brand: bool = True
    forbidden_phrases: str = ""
    location_code: int = 2840
    min_volume: int = 10
    page_type: str = "service"
    template_key: str = "service_page"
    custom_template_text: str = ""
    client_brief: str = ""
    brand_profile_id: str = ""
    brand_consistency_check: bool = False
    brand_consistency_threshold: int = Field(default=70, ge=0, le=100)
    jina_api_key: str = ""
    use_gsc: bool = False
    site_url: str = ""
    gen_page_copy: bool = True
    gen_meta: bool = True
    gen_faqs: bool = True
    num_faqs: int = 5


class AIOJobRequest(BaseModel):
    name: str = ""
    rows: list[AIORow]
    settings: AIOSettings


@router.post("/run")
def run_aio_job(
    request: AIOJobRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    job_id = str(uuid.uuid4())
    enforce_job_start(sb, user.id, "all-in-one", len(request.rows), 50)
    enforce_rate_limit(sb, user.id, "all-in-one", "job-create", 10)
    try:
        runtime_settings = hydrate_job_settings(sb, user.id, request.settings.model_dump())
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Saved credentials are temporarily unavailable.",
        ) from None
    if not runtime_settings.get("api_key") or not runtime_settings.get("dfs_password"):
        raise HTTPException(status_code=400, detail="Saved provider credentials are incomplete. Update Settings and try again.")

    gsc_credentials = None
    if request.settings.use_gsc:
        gsc_credentials = runtime_settings.get("_gsc_credentials")

    # Brand profile
    brand_profile = None
    if request.settings.brand_profile_id:
        try:
            bp = sb.table("brand_profiles").select("data").eq("id", request.settings.brand_profile_id).eq("user_id", user.id).execute()
            if bp.data:
                brand_profile = bp.data[0].get("data") or {}
        except Exception:
            pass

    execute_active_job_write(lambda: sb.table("jobs").insert({
        "id":             job_id,
        "user_id":        user.id,
        "name":           request.name or f"All in One — {len(request.rows)} URLs",
        "tool":           "all-in-one",
        "status":         "pending",
        "total_rows":     len(request.rows),
        "completed_rows": 0,
        "failed_rows":    0,
        "results":        [],
        "logs":           [],
        "rows":           [r.model_dump() for r in request.rows],
        "settings":       strip_secret_fields(request.settings.model_dump()),
        "current_step":   "Queued...",
    }).execute(), "all-in-one")

    background_tasks.add_task(
        _process_job,
        job_id=job_id,
        rows=[r.model_dump() for r in request.rows],
        settings=runtime_settings,
        gsc_credentials=gsc_credentials,
        brand_profile=brand_profile,
        user_id=user.id,
    )

    return {"job_id": job_id, "status": "running"}


@router.get("/templates")
def list_templates():
    result = {}
    for pt in ["blog", "case_study", "glossary", "homepage", "service", "local", "about", "contact", "product", "collection"]:
        t = get_templates_for_page_type(pt)
        result[pt] = [{"key": k, "name": v["name"], "description": v.get("description", "")} for k, v in t.items()]
    return result
