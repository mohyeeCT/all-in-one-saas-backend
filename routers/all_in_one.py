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
from utils.gsc import get_gsc_client, get_top_queries_for_url
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

_RATE_LIMITS = {
    "Claude": 0.5,
    "OpenAI": 0.5,
    "Gemini (free)": 5.0,
    "Mistral (free tier)": 2.0,
    "Groq (free tier)": 2.0,
}


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

    use_gsc  = settings.get("use_gsc", False)
    site_url = settings.get("site_url", "")
    jina_key = settings.get("jina_api_key", "")

    def _empty(status: str) -> dict:
        return {
            "url": url, "primary_keyword": None, "keyword_source": status,
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
    organic_results = []
    try:
        serp_data       = get_serp_data(dfs_login, dfs_password, primary_keyword, location_code)
        if serp_data.get("error"):
            step("DataForSEO SERP failed: " + str(serp_data["error"])[:120])
        paa_questions   = serp_data.get("paa_items") or serp_data.get("paa") or []
        ai_overview     = serp_data.get("ai_overview_raw") or serp_data.get("ai_overview") or ""
        organic_results = serp_data.get("organic") or []
        step("SERP: " + ("AIO ✓" if ai_overview else "AIO ✗") + ", PAA: " + str(len(paa_questions)) + ", organic: " + str(len(organic_results)))
    except Exception as e:
        step("⚠ SERP failed: " + str(e)[:60])

    # ─────────────────────────────────────────────────────────────────────
    # STEP 3 — Page context scrape (for FAQs)
    # ─────────────────────────────────────────────────────────────────────
    page_context = ""
    if gen_faqs and jina_key:
        step("scraping page context...")
        try:
            sc = scrape_page_context(jina_key, url)
            if sc.get("success"):
                page_context = sc["content"]
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

        section_names = [s["name"] for s in template["sections"]]
        kw_assignment = assign_keywords_to_sections(ranked, section_names)
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
                    sc = scrape_url(comp_url)
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

    if gen_meta:
        step("generating meta copy...")
        try:
            meta_result = generate_copy(
                provider=provider,
                api_key=api_key,
                url=url,
                keyword=primary_keyword,
                page_type=page_type,
                brand_name=brand_name if include_brand else "",
                forbidden_phrases=forbidden_phrases,
                context=client_brief,
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
                ai_overview_sections=[],
                ai_overview_raw=ai_ov_for_faq,
                forbidden_phrases=forbidden_phrases,
                page_context=page_context,
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
        lsi_map = {}
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
        try:
            existing = scrape_url(url)
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
        )
        docx_b64 = base64.b64encode(docx_bytes).decode("utf-8")
        step("✓ done")
    except Exception as e:
        step("⚠ docx failed: " + str(e)[:60])

    return {
        "url":                  url,
        "h1":                   h1,
        "primary_keyword":      primary_keyword,
        "keyword_source":       keyword_source,
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
        "competitor_urls":      competitor_urls_used,
        "docx_b64":             docx_b64,
        "status":               "ok",
    }


def _build_combined_docx(
    url, h1, primary_keyword, page_type, template,
    generated_title, generated_description, optimised_h1,
    faq_items, faq_schema, section_results, word_count, competitor_urls,
    gen_meta, gen_faqs, gen_page_copy,
):
    """Build a single docx with meta, FAQs, and page copy in one document."""
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
            char_note = f"  ({len(generated_title)} chars{'  ⚠ over 60' if len(generated_title) > 60 else ''})"
            p.add_run(char_note)
        if generated_description:
            p = doc.add_paragraph()
            p.add_run("Meta Description: ").bold = True
            p.add_run(generated_description)
            char_note = f"  ({len(generated_description)} chars{'  ⚠ over 155' if len(generated_description) > 155 else ''})"
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
            result = {"url": url, "error": str(e), "status": "error", "word_count": 0, "docx_b64": None}

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
