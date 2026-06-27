import time
import uuid
import base64
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from google.auth.exceptions import RefreshError
from pydantic import BaseModel

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
from utils.copy_gen import (
    generate_page, generate_faq, generate_copy, sanitise
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
        return res.data and res.data[0].get("status") == "cancelling"
    except Exception:
        return False


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
        (
            sb.table("jobs")
            .update(update_data)
            .eq("id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
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


def _add_qa_flag(flags: list[dict], code: str, message: str, output: str = "", phrase: str = ""):
    flag = {"code": code, "message": message}
    if output:
        flag["output"] = output
    if phrase:
        flag["phrase"] = phrase
    flags.append(flag)


def _collect_qa_flags(
    *,
    gen_meta: bool,
    gen_faqs: bool,
    gen_page_copy: bool,
    generated_title: str,
    generated_description: str,
    optimised_h1: str,
    input_h1: str,
    faq_items: list,
    section_results: dict,
    forbidden_phrases: list[str],
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

    outputs = [
        ("meta_title", generated_title or ""),
        ("meta_description", generated_description or ""),
        ("meta_h1", optimised_h1 or ""),
        ("page_copy", page_copy_text),
    ]
    for item in faq_items or []:
        if isinstance(item, dict):
            outputs.append(("faq", f"{item.get('question', '')}\n{item.get('answer', '')}"))

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
    def step(msg: str):
        _update_job(sb, job_id, user_id, {"current_step": f"Row {row_num}/{total_rows}: {msg}"})

    url          = (row.get("url") or "").strip()
    manual_kws   = [k.strip() for k in (row.get("keyword") or "").split(",") if k.strip()]
    h1_raw       = (row.get("h1") or "").strip()
    h1           = "" if h1_raw.lower() == "none" else h1_raw
    page_type    = (row.get("page_type") or settings.get("page_type", "service")).strip().lower()
    template_key = row.get("template_key") or settings.get("template_key", "service_page")

    # What to generate — from row overrides or job-level settings
    gen_page_copy = row.get("gen_page_copy", settings.get("gen_page_copy", True))
    gen_meta      = row.get("gen_meta",      settings.get("gen_meta",      True))
    gen_faqs      = row.get("gen_faqs",      settings.get("gen_faqs",      True))
    num_faqs      = int(row.get("num_faqs",  settings.get("num_faqs",      5)))

    dfs_login    = settings["dfs_login"]
    dfs_password = settings["dfs_password"]
    provider     = settings.get("provider", "Claude")
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
    client_brief = settings.get("client_brief", "") or ""
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

    def _empty(status: str) -> dict:
        return {
            "url": url, "primary_keyword": None, "keyword_source": status,
            "gsc_auth_method": gsc_auth_method,
            "generated_title": None, "generated_description": None, "optimised_h1": None,
            "faq_items": [], "faq_schema": None,
            "word_count": 0, "template_name": None,
            "competitor_urls": [], "docx_b64": None, "status": status,
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
        step("DFS ranked: " + str(len(dfs_ranked)) + " keywords found")
    except Exception as e:
        step("⚠ DFS ranked failed: " + str(e)[:60])

    # Optional GSC layer
    gsc_queries = []
    if use_gsc and gsc_client and site_url:
        step("fetching GSC queries...")
        try:
            gsc_queries = get_top_queries_for_url(gsc_client, site_url, url, top_n=10)
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
    pool   = [k for k in pool if k.get("volume", 0) >= min_volume]
    ranked = rank_keywords(pool, branded_terms, h1=h1, exclude_position_one=True)
    ranked = [k for k in ranked if not k.get("branded")]

    if not ranked and manual_kws:
        ranked = [{"keyword": k, "volume": 10, "difficulty": 1, "score": 1.0} for k in manual_kws]

    keyword_source = "dfs+gsc" if gsc_queries else "dfs"
    if not ranked and not use_gsc and h1:
        ranked = [{"keyword": h1, "volume": 0, "difficulty": 50, "score": 0.0}]
        keyword_source = "h1 fallback"

    if not ranked:
        step("✗ no keywords found — skipping")
        return _empty("skipped: no keywords found")

    primary_keyword = ranked[0]["keyword"]
    if primary_keyword.lower() in used_keywords:
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
        step("SERP: " + ("AIO ✓" if ai_overview else "AIO ✗") + ", PAA: " + str(len(paa_questions)) + ", organic: " + str(len(organic_results)))
    except Exception as e:
        step("⚠ SERP failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 3 — Page context scrape (for FAQs)
    # ─────────────────────────────────────────────────────────────────────
    page_context = ""
    scraped_page_content = ""
    if (gen_faqs or gen_meta) and jina_key:
        step("scraping page context...")
        try:
            sc = scrape_page_context(jina_key, url)
            if sc.get("success"):
                scraped_page_content = sc["content"]
                page_context = scraped_page_content
                if client_brief:
                    page_context = (page_context + "\n\n" + client_brief).strip()
                step("page context: " + str(len(page_context)) + " chars")
        except Exception:
            page_context = client_brief

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
                template = get_template("service_page")

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
                if top:
                    competitor_section_map = map_competitor_sections(top, template["sections"])
                step("competitors: " + str(len(competitor_urls_used)) + " scraped")
            except Exception as e:
                step("⚠ competitor scrape failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 5 — Generate meta copy
    # ─────────────────────────────────────────────────────────────────────
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
                url=url,
                keyword=primary_keyword,
                page_type=page_type,
                brand_name=brand_name if include_brand else "",
                forbidden_phrases=forbidden_phrase_text,
                context="\n\n".join(meta_context_parts),
                brand_context=brand_context,
                business_type=business_type,
                h1=h1,
            )
            generated_title       = meta_result.get("title", "")
            generated_description = meta_result.get("description", "")
            optimised_h1          = meta_result.get("h1_optimised", "")
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
            )
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

        except Exception as e:
            step("⚠ FAQs failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 7 — Generate full page copy
    # ─────────────────────────────────────────────────────────────────────
    section_results = {}
    full_page       = ""
    word_count      = 0

    if gen_page_copy and template:
        step("generating page copy (" + str(len(template["sections"])) + " sections)...")

        # LSI keywords
        supporting_kws = list({v.get("supporting", "") for v in kw_assignment.values() if v.get("supporting")})
        for sk in supporting_kws[:3]:
            try:
                ideas = get_keyword_ideas(sk, dfs_login, dfs_password, location_code, limit=10)
                lsi_map[sk] = [i["keyword"] for i in ideas[:3]]
            except Exception as e:
                lsi_map[sk] = []
                step("DataForSEO keyword ideas failed: " + str(e)[:120])

        # Client existing content
        client_existing_content = ""
        if scraped_page_content:
            client_existing_content = scraped_page_content[:800]
        else:
            try:
                existing = scrape_url(url, api_key=jina_key)
                if existing["success"]:
                    client_existing_content = existing.get("body_text", "")[:800]
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
                forbidden_phrases=forbidden_phrase_text,
                progress_callback=on_section,
            )
            section_results = {k: v for k, v in page_result.items() if not k.startswith("_")}
            full_page       = page_result.get("_full_page", "")
            word_count      = page_result.get("_word_count", 0)
            step("✓ page copy: " + str(word_count) + " words")
        except InterruptedError:
            raise
        except Exception as e:
            step("⚠ page copy failed: " + str(e)[:80])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 8 — Build combined docx
    # ─────────────────────────────────────────────────────────────────────
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
    qa_flags = _collect_qa_flags(
        gen_meta=gen_meta,
        gen_faqs=gen_faqs,
        gen_page_copy=gen_page_copy,
        generated_title=generated_title or "",
        generated_description=generated_description or "",
        optimised_h1=optimised_h1 or "",
        input_h1=input_h1_for_qa,
        faq_items=faq_items,
        section_results=section_results,
        forbidden_phrases=forbidden_phrase_list,
    )

    return {
        "url":                  url,
        "h1":                   h1,
        "primary_keyword":      primary_keyword,
        "keyword_source":       keyword_source,
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
        "competitor_urls":      competitor_urls_used,
        "docx_b64":             docx_b64,
        "qa_flags":             qa_flags,
        "status":               "review" if qa_flags else "ok",
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
            char_note = f"  ({len(generated_title)} chars{'  ⚠ over 90' if len(generated_title) > 90 else ''})"
            p.add_run(char_note)
        if generated_description:
            p = doc.add_paragraph()
            p.add_run("Meta Description: ").bold = True
            p.add_run(generated_description)
            char_note = f"  ({len(generated_description)} chars{'  ⚠ over 200' if len(generated_description) > 200 else ''})"
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
                "failed_rows":  sum(1 for r in results if r.get("status") != "ok"),
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
                "failed_rows":  sum(1 for r in results if r.get("status") != "ok"),
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

        results.append(result)
        _update_job(sb, job_id, user_id, {"completed_rows": idx + 1, "results": results})

        if _is_cancelled(sb, job_id, user_id):
            _update_job(sb, job_id, user_id, {
                "status":       "cancelled",
                "current_step": f"Cancelled after {idx + 1}/{total} rows.",
                "failed_rows":  sum(1 for r in results if r.get("status") != "ok"),
            })
            return

    _update_job(sb, job_id, user_id, {
        "status":        "complete",
        "current_step":  "Done.",
        "completed_rows": len(results),
        "failed_rows":   sum(1 for r in results if r.get("status") != "ok"),
        "results":       results,
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
