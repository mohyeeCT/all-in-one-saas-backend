import re
import time
import json


# ── Sanitiser ─────────────────────────────────────────────────────────────────

def sanitise(text: str, brand_name: str = "") -> str:
    """Strip em dashes, fix brand casing, remove surrounding quotes."""
    if not text:
        return ""
    text = text.replace("\u2014", ",").replace("\u2013 ", ", ")
    text = text.strip().strip('"').strip("'").strip()
    if brand_name:
        text = re.sub(re.escape(brand_name), brand_name, text, flags=re.IGNORECASE)
    return text


# ── Business type context ─────────────────────────────────────────────────────

BUSINESS_TYPE_CONTEXT = {
    "b2b": (
        "This page is for a B2B business targeting other businesses. "
        "Tone: professional and direct. Focus on ROI, efficiency, and business outcomes. "
        "No consumer-facing CTAs. No exclamation marks. No lifestyle language."
    ),
    "b2c": (
        "This page is for a B2C business targeting consumers. "
        "Tone: warm, accessible, and benefit-focused. "
        "CTAs can reference product benefits and lifestyle outcomes."
    ),
    "ecommerce": (
        "This page is for an ecommerce business. "
        "Tone: direct and product-focused. "
        "Copy should support purchase decisions. Avoid vague editorial tone."
    ),
    "service": (
        "This page is for a service business. "
        "Tone: helpful and trustworthy. Focus on expertise, process, and outcomes. "
        "CTAs should invite contact or consultation."
    ),
    "local": (
        "This page is for a local service business. "
        "Tone: community-oriented and accessible. "
        "Reference the service area where natural. CTAs should invite calls or visits."
    ),
    "general": (
        "This page is for a general business. "
        "Tone: clear and professional. Adapt language to the page context."
    ),
}


# ── Prompt builder ────────────────────────────────────────────────────────────

_BIZ_CONTEXT = BUSINESS_TYPE_CONTEXT


def _build_section_prompt(
    section: dict,
    primary_keyword: str,
    supporting_keyword: str,
    lsi_keywords: list,
    business_type: str,
    brand_name: str,
    h1: str,
    page_type: str,
    paa_questions: list,
    competitor_excerpts: list,
    client_brief: str,
    previous_section_text: str,
    client_existing_content: str,
    ai_overview: str = "",
) -> str:
    kw_slot = section.get("keyword_slot", "none")
    wc_min, wc_max = section.get("word_count", [150, 250])

    if kw_slot == "primary":
        keyword_instruction = f"Include this keyword naturally: {primary_keyword}" if primary_keyword else ""
    elif kw_slot == "supporting":
        keyword_instruction = f"Include this keyword naturally: {supporting_keyword}" if supporting_keyword else ""
    elif kw_slot == "lsi":
        lsi_str = ", ".join(lsi_keywords[:3]) if lsi_keywords else ""
        keyword_instruction = f"Naturally cover these related terms where relevant: {lsi_str}" if lsi_str else ""
    else:
        keyword_instruction = ""

    paa_block = ""
    if paa_questions and section["name"] == "faq":
        paa_lines = "\n".join(f"- {q['question']}" for q in paa_questions[:5])
        paa_block = f"\nPeople Also Ask questions to draw from:\n{paa_lines}"

    competitor_block = ""
    if competitor_excerpts:
        excerpts = "\n".join(f"- {e}" for e in competitor_excerpts[:3] if e.strip())
        if excerpts:
            competitor_block = f"\nWhat competitors cover in this section (use as context, not as copy):\n{excerpts}"

    existing_block = ""
    if client_existing_content and client_existing_content.strip():
        existing_block = f"\nClient's existing content on this topic (extract useful facts or claims, do not copy):\n{client_existing_content[:400]}"

    brief_block = ""
    if client_brief and client_brief.strip():
        brief_block = f"\nClient brief notes:\n{client_brief[:300]}"

    prev_block = ""
    if previous_section_text and previous_section_text.strip():
        prev_block = f"\nPrevious section (for context and coherence, do not repeat):\n{previous_section_text[-300:]}"

    heading_instruction = ""
    heading_level = section.get("heading_level", "h2")
    if heading_level == "h2":
        heading_instruction = f"Start with an H2 heading (## in markdown). The heading should reflect the section purpose."
    elif heading_level == "h3":
        heading_instruction = f"Use H3 subheadings (### in markdown) where appropriate."
    elif heading_level == "h1":
        heading_instruction = "Start with the H1 headline (# in markdown)."
    else:
        heading_instruction = "Do not add a heading. Write body copy only."

    ai_overview_block = ""
    if ai_overview and ai_overview.strip():
        ai_overview_block = f"\nGoogle AI Overview for this topic (use as reference for what topics to cover, do not copy):\n{ai_overview[:600]}"

    prompt = f"""You are writing the '{section['label']}' section of a {page_type} page.

Page H1: {h1 or 'Not provided'}
Brand name: {brand_name or 'Not specified'}
Business context: {BUSINESS_TYPE_CONTEXT.get(business_type, BUSINESS_TYPE_CONTEXT['general'])}

Section purpose: {section['purpose']}
Word count target: {wc_min} to {wc_max} words. Stay within this range.
{keyword_instruction}
{heading_instruction}

Section-specific rules:
{section['prompt_rules']}

Hard rules for all output:
- Never use em dashes (use a comma or rewrite the sentence)
- No exclamation marks
- No generic AI openings like 'In today's world' or 'Great question'
- No fluff. Every sentence must add information or move the argument forward
- Brand name must appear exactly as: {brand_name}
- Return only the section copy. No preamble, no notes, no explanations.
{paa_block}{ai_overview_block}{competitor_block}{existing_block}{brief_block}{prev_block}"""

    return prompt.strip()


# ── Provider functions ────────────────────────────────────────────────────────

def _call_claude(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model or "claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _call_openai(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _call_gemini(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model or "gemini-2.0-flash",
        contents=prompt,
    )
    return resp.text.strip()


def _call_mistral(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    from mistralai import Mistral
    client = Mistral(api_key=api_key)
    resp = client.chat.complete(
        model=model or "mistral-small-latest",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


def _call_groq(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model or "llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


PROVIDER_FN = {
    "Claude": _call_claude,
    "OpenAI": _call_openai,
    "Gemini": _call_gemini,
    "Gemini (free)": _call_gemini,
    "Mistral": _call_mistral,
    "Mistral (free tier)": _call_mistral,
    "Groq": _call_groq,
    "Groq (free tier)": _call_groq,
}

DEFAULT_MODELS = {
    "Claude": "claude-haiku-4-5-20251001",
    "OpenAI": "gpt-4o-mini",
    "Gemini": "gemini-2.0-flash",
    "Gemini (free)": "gemini-2.0-flash",
    "Mistral": "mistral-small-latest",
    "Mistral (free tier)": "mistral-small-latest",
    "Groq": "llama-3.3-70b-versatile",
    "Groq (free tier)": "llama-3.3-70b-versatile",
}

PROVIDER_DELAY = {
    "Claude": 0.5,
    "OpenAI": 0.5,
    "Gemini": 5.0,
    "Mistral": 2.0,
    "Groq": 2.0,
}


# ── Section loop ──────────────────────────────────────────────────────────────

def generate_page(
    template: dict,
    keyword_assignment: dict,
    lsi_keywords: dict,
    business_type: str,
    brand_name: str,
    h1: str,
    page_type: str,
    paa_questions: list,
    ai_overview: str,
    competitor_section_map: dict,
    client_brief: str,
    client_existing_content: str,
    provider: str,
    api_key: str,
    progress_callback=None,
) -> dict:
    """
    Runs the section-by-section generation loop.
    Returns: { section_name: text, "_full_page": assembled markdown, "_word_count": int }
    """
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    delay = PROVIDER_DELAY.get(provider, 1.0)
    sections = template.get("sections", [])
    results = {}
    previous_text = ""

    for i, section in enumerate(sections):
        if progress_callback:
            progress_callback(i, len(sections), section["label"])

        kw_slot = section.get("keyword_slot", "none")
        sec_name = section["name"]
        assignment = keyword_assignment.get(sec_name, {})
        primary_kw = assignment.get("primary", "")
        supporting_kw = assignment.get("supporting", "")
        lsi_kws = lsi_keywords.get(supporting_kw or primary_kw, [])
        comp_excerpts = competitor_section_map.get(sec_name, [])

        prompt = _build_section_prompt(
            section=section,
            primary_keyword=primary_kw,
            supporting_keyword=supporting_kw,
            lsi_keywords=lsi_kws,
            business_type=business_type,
            brand_name=brand_name,
            h1=h1,
            page_type=page_type,
            paa_questions=paa_questions if sec_name == "faq" else [],
            competitor_excerpts=comp_excerpts,
            client_brief=client_brief,
            previous_section_text=previous_text,
            client_existing_content=client_existing_content if i == 0 else "",
        )

        try:
            raw = fn(api_key, prompt)
            text = sanitise(raw, brand_name)
        except Exception as e:
            text = f"[ERROR generating section '{section['label']}': {e}]"

        results[sec_name] = text
        previous_text = text

        if i < len(sections) - 1:
            time.sleep(delay)

    full_page = "\n\n".join(results.get(s["name"], "") for s in sections)
    word_count = len(full_page.split())

    results["_full_page"] = full_page
    results["_word_count"] = word_count

    return results


# ── FAQ generation (ported from faq-saas-backend) ──────────────────────────

def _build_faq_prompt(
    keyword: str,
    page_type: str,
    brand_name: str,
    business_type: str,
    h1: str,
    ai_overview_sections: list,
    ai_overview_raw: str,
    paa_items: list,
    num_faqs: int,
    forbidden_phrases: str,
    page_context: str,
) -> str:
    paa_lines = []
    for item in paa_items[:num_faqs + 3]:
        question = item.get("question", "") if isinstance(item, dict) else str(item)
        if question:
            paa_lines.append(f"- {question}")

    overview = ai_overview_raw or "\n".join(
        str(section.get("content") or section.get("title") or "")
        for section in ai_overview_sections
        if isinstance(section, dict)
    )

    return f"""Generate exactly {num_faqs} useful FAQs for this page.

Target keyword: {keyword}
Page type: {page_type}
Business type: {business_type}
Brand name: {brand_name or "N/A"}
Page H1: {h1 or "Not provided"}
Forbidden phrases: {forbidden_phrases or "None"}
Page context: {page_context or "Not available"}
AI Overview: {overview or "Not available"}
People Also Ask:
{chr(10).join(paa_lines) or "Not available"}

Rules:
- Questions and answers must be specific to this page.
- Do not invent pricing, availability, shipping, returns, guarantees, or other unsupported claims.
- Never use forbidden phrases or em dashes.
- Keep each answer concise and direct.
- Return only a JSON array of objects with question, answer, and source keys.
"""


def _parse_faq_json(raw: str) -> list:
    raw = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    result = json.loads(raw)
    if not isinstance(result, list):
        raise ValueError("FAQ response must be a JSON array")
    return result


def generate_faq(
    provider: str,
    api_key: str,
    keyword: str,
    page_type: str,
    brand_name: str,
    business_type: str,
    h1: str,
    ai_overview_sections: list,
    ai_overview_raw: str,
    paa_items: list,
    num_faqs: int,
    forbidden_phrases: str = "",
    page_context: str = "",
    used_question_patterns: list = None,
    model: str = None,
    brand_profile: dict = None,
) -> list:
    """Generate FAQ Q&A pairs using the selected AI provider.

    Returns a list of dicts: [{"question": str, "answer": str, "source": str}, ...]
    source: "ai_overview" | "paa" | "generated"
    Raises on API failure so callers can handle and log errors.
    """
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = model or DEFAULT_MODELS.get(provider)

    prompt = _build_faq_prompt(
        keyword=keyword,
        page_type=page_type,
        brand_name=brand_name,
        business_type=business_type,
        h1=h1,
        ai_overview_sections=ai_overview_sections,
        ai_overview_raw=ai_overview_raw,
        paa_items=paa_items,
        num_faqs=num_faqs,
        forbidden_phrases=forbidden_phrases,
        page_context=page_context,
    )

    raw = fn(api_key, prompt, model=resolved_model)
    items = _parse_faq_json(raw)

    sanitised = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sanitised.append({
            "question": sanitise(item.get("question", ""), brand_name),
            "answer": sanitise(item.get("answer", ""), brand_name),
            "source": item.get("source", "generated"),
        })

    return sanitised


_last_batch_page_blocks: list = []  # stores per-page prompt blocks from last batch call

def _build_batch_prompt(pages: list, num_faqs: int) -> str:
    """Build a single prompt for multiple pages grouped by category.

    Each page dict contains:
        keyword, page_type, brand_name, business_type, h1,
        ai_overview_sections, ai_overview_raw, paa_items,
        page_context, forbidden_phrases, used_question_patterns
    """
    blocks = []

    for i, p in enumerate(pages, start=1):
        biz_ctx = _BIZ_CONTEXT.get(p.get("business_type", "general"), _BIZ_CONTEXT["general"])
        keyword = p.get("keyword", "")
        h1 = p.get("h1", "")
        brand_name = p.get("brand_name", "")
        page_context = p.get("page_context", "")
        ao_sections = p.get("ai_overview_sections", [])
        paa_items = p.get("paa_items", [])
        forbidden = p.get("forbidden_phrases", "")
        used_patterns = p.get("used_question_patterns", [])
        bp = p.get("brand_profile") or {}

        brand_line = f"Brand name: '{brand_name}'. Use exact casing." if brand_name else ""
        h1_line = f"H1: {h1}" if h1 else ""

        # Merge forbidden phrases with brand's words_to_avoid
        bp_avoid = bp.get("words_to_avoid", "")
        combined_forbidden = ", ".join(filter(None, [forbidden.strip(), bp_avoid.strip()]))
        forbidden_line = f"Never use: {combined_forbidden}" if combined_forbidden else ""

        # Brand profile block for batch
        bp_lines = []
        if bp:
            if bp.get("brand_voice"):      bp_lines.append(f"Brand voice: {bp['brand_voice']}")
            if bp.get("tone"):             bp_lines.append(f"Tone: {bp['tone']}")
            if bp.get("target_audience"):  bp_lines.append(f"Target audience: {bp['target_audience']}")
            if bp.get("usps"):             bp_lines.append(f"USPs: {bp['usps']}")
            if bp.get("key_messages"):     bp_lines.append(f"Key messages: {bp['key_messages']}")
            if bp.get("competitors"):      bp_lines.append(f"Competitors (differentiate from): {bp['competitors']}")
        brand_profile_block = ("BRAND CONTEXT:\n" + "\n".join(bp_lines)) if bp_lines else ""

        ctx = f"Page content:\n---\n{page_context}\n---" if page_context else ""

        if ao_sections:
            ao_text = "\n".join(
                f"- {s['content']}" if s.get("content") else f"- {s.get('title', '')}"
                for s in ao_sections
            )
            ao_block = f"AI Overview:\n{ao_text}"
        else:
            ao_block = "AI Overview: not available"

        if paa_items:
            paa_lines = []
            for p2 in paa_items[:num_faqs + 3]:
                line = f"- Q: {p2['question']}"
                if p2.get("answer"):
                    line += f" | A: {p2['answer'][:100]}"
                paa_lines.append(line)
            paa_block = "PAA:\n" + "\n".join(paa_lines)
        else:
            paa_block = "PAA: not available"

        if used_patterns:
            patterns = "\n".join(f"- {p3}" for p3 in used_patterns[:15])
            used_block = f"Avoid repeating these question patterns from other pages where possible:\n{patterns}"
        else:
            used_block = ""

        block = f"""--- PAGE {i} ---
Keyword: {keyword}
Page type: {p.get("page_type", "general")}
Business type: {biz_ctx}
{h1_line}
{brand_line}
{forbidden_line}
{brand_profile_block}

{ctx}

{ao_block}

{paa_block}

{used_block}"""
        blocks.append(block.strip())

    pages_text = "\n\n".join(blocks)

    # Also return individual page blocks for per-page debug display
    global _last_batch_page_blocks
    _last_batch_page_blocks = blocks  # overwritten each call

    return f"""You are an expert SEO copywriter. Generate FAQ content for {len(pages)} web pages listed below.

For each page, generate exactly {num_faqs} FAQ questions that real visitors would ask about THAT SPECIFIC PAGE.

Rules for all pages:
- Focus on what is unique and specific to each page — not generic questions that apply to every page in the category
- Where pages are similar products, vary the questions to highlight different aspects of each
- Lead each answer with a direct, complete response in the first sentence
- Keep answers 40 to 80 words, written for featured snippet format
- Use AI Overview sections as priority 1 signal, PAA as priority 2, page content as fallback
- Only use AIO/PAA questions if genuinely relevant to that specific page
- No em dashes. No filler openers ("Great question", "Certainly", "Of course", "Absolutely")
- Where possible, avoid repeating question patterns already used on other pages
- Do not create FAQs that repeat what the page copy already covers too closely
- Do not create FAQs that feel redundant with existing copy unless the FAQ format adds clear value
- Only keep FAQ ideas that fit naturally with the page and fill a real gap or improve clarity

{pages_text}

Return a JSON object with one key per page index (1-based). Each value is an array of {num_faqs} FAQ items:
{{
  "1": [{{"question": "...", "answer": "...", "source": "ai_overview|paa|generated"}}, ...],
  "2": [{{"question": "...", "answer": "...", "source": "..."}}, ...],
  ...
}}

Return only the raw JSON object. No preamble, no markdown code fences."""


def _parse_batch_json(raw: str, num_pages: int) -> dict:
    """Parse batch JSON response. Returns dict keyed by string page index."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Return empty dicts for all pages on failure
    return {str(i): [] for i in range(1, num_pages + 1)}



def generate_faq_batch(
    provider: str,
    api_key: str,
    pages: list,
    num_faqs: int,
    model: str = None,
) -> tuple:
    """Generate FAQs for multiple pages in a single AI call.

    Returns (results, prompt_sent, page_debug_prompts):
        results: dict keyed by 0-based index -> list of faq dicts
        prompt_sent: full prompt string sent to the AI
        page_debug_prompts: dict keyed by 0-based index -> per-page context summary for debug
    """
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = model or DEFAULT_MODELS.get(provider)
    prompt = _build_batch_prompt(pages, num_faqs)
    batch_max_tokens = min(64000, max(2048, len(pages) * num_faqs * 400))
    raw = fn(api_key, prompt, max_tokens=batch_max_tokens, model=resolved_model)
    parsed = _parse_batch_json(raw, len(pages))

    # Build per-page debug summaries showing exactly what context the AI received
    page_debug_prompts = {}
    for i, page in enumerate(pages):
        biz_ctx = _BIZ_CONTEXT.get(page.get("business_type", "general"), _BIZ_CONTEXT["general"])
        ao_sections = page.get("ai_overview_sections", [])
        paa_items_p = page.get("paa_items", [])
        used = page.get("used_question_patterns", [])

        ao_text = ("\n".join(
            f"- {s.get('content', s.get('title', ''))}" for s in ao_sections
        ) if ao_sections else "Not available")

        paa_text = ("\n".join(
            f"- Q: {p['question']}" + (f"\n  A: {p['answer'][:120]}" if p.get("answer") else "")
            for p in paa_items_p[:8]
        ) if paa_items_p else "Not available")

        used_text = ("\n".join(f"- {u}" for u in used[:15]) if used else "None")

        ctx = page.get("page_context", "") or "Not scraped"

        page_debug_prompts[i] = (
            f"=== SIGNALS SENT TO AI ===\n\n"
            f"KEYWORD: {page.get('keyword', '')}\n"
            f"PAGE TYPE: {page.get('page_type', '')}\n"
            f"BUSINESS TYPE: {biz_ctx}\n"
            f"H1: {page.get('h1', '') or 'not provided'}\n"
            f"BRAND: {page.get('brand_name', '') or 'not provided'}\n\n"
            f"--- PAGE CONTENT EXCERPT ---\n{ctx}\n\n"
            f"--- AI OVERVIEW ---\n{ao_text}\n\n"
            f"--- PEOPLE ALSO ASK ---\n{paa_text}\n\n"
            f"--- USED QUESTION PATTERNS (avoid) ---\n{used_text}"
        )

    results = {}
    for i, page in enumerate(pages):
        brand_name = page.get("brand_name", "")
        raw_items = parsed.get(str(i + 1), [])
        sanitised = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            sanitised.append({
                "question": sanitise(item.get("question", ""), brand_name),
                "answer": sanitise(item.get("answer", ""), brand_name),
                "source": item.get("source", "generated"),
            })

        # Fallback: if batch parsing returned nothing for this page, retry solo
        if not sanitised:
            try:
                solo_prompt = _build_batch_prompt([page], num_faqs)
                solo_raw = fn(api_key, solo_prompt, max_tokens=16384)
                solo_parsed = _parse_batch_json(solo_raw, 1)
                for item in (solo_parsed.get("1") or []):
                    if not isinstance(item, dict):
                        continue
                    sanitised.append({
                        "question": sanitise(item.get("question", ""), brand_name),
                        "answer": sanitise(item.get("answer", ""), brand_name),
                        "source": item.get("source", "generated"),
                    })
            except Exception:
                pass

        results[i] = sanitised

    return results, prompt, page_debug_prompts


# ── Meta copy generation (ported from meta-saas-backend) ─────────────────


def generate_copy(provider: str, api_key: str, **kwargs) -> dict:
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    prompt = f"""Write SEO metadata for this page.

URL: {kwargs.get("url", "")}
Target keyword: {kwargs.get("keyword", "")}
Page type: {kwargs.get("page_type", "general")}
Business type: {kwargs.get("business_type", "general")}
Brand name: {kwargs.get("brand_name", "") or "N/A"}
Current H1: {kwargs.get("h1", "") or "Not provided"}
Forbidden phrases: {kwargs.get("forbidden_phrases", "") or "None"}
Additional context: {kwargs.get("context", "") or "None"}

Rules:
- Title maximum 60 characters.
- Meta description maximum 155 characters.
- Include the target keyword naturally.
- Never use forbidden phrases or em dashes.
- Return only a JSON object with keys: title, description, h1_optimised.
"""
    raw = fn(api_key, prompt, max_tokens=512, model=DEFAULT_MODELS.get(provider))
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Meta response must be a JSON object")

    brand_name = kwargs.get("brand_name", "")
    return {
        "title": sanitise(result.get("title", ""), brand_name),
        "description": sanitise(result.get("description", ""), brand_name),
        "h1_optimised": sanitise(result.get("h1_optimised", ""), brand_name),
    }
