import re
import time
import json


SECTION_LSI_KEYWORD_LIMIT = 3
SECTION_PAA_QUESTION_LIMIT = 5
SECTION_COMPETITOR_EXCERPT_LIMIT = 3
SECTION_EXISTING_CONTENT_CHAR_LIMIT = 400
SECTION_CLIENT_BRIEF_CHAR_LIMIT = 300
SECTION_PREVIOUS_CONTEXT_CHAR_LIMIT = 300
SECTION_AI_OVERVIEW_CHAR_LIMIT = 600
SECTION_REVIEWER_NOTE_LIMIT = 5
SECTION_REVIEWER_NOTE_CHAR_LIMIT = 300
SECTION_STRATEGY_BRIEF_CHAR_LIMIT = 1200
STRATEGY_BRIEF_MAX_TOKENS = 8192
STRATEGY_BRIEF_CONTEXT_CHAR_LIMIT = 2500


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

PAA_ANSWER_SNIPPET_CHARS = 280

_BIZ_CONTEXT_FAQ = {
    "b2b": (
        "This is a B2B page. Answers should be professional, solution-focused, and concise. "
        "No consumer CTAs. Focus on ROI, process, and expertise."
    ),
    "b2c": (
        "This is a B2C page. Answers can be conversational. Include a light CTA where it fits naturally."
    ),
    "ecommerce": (
        "This is an ecommerce page. Answers should address buying concerns, specs, compatibility, "
        "fit, materials, use cases, and product selection. Do not create policy, shipping, return, "
        "availability, pricing, or warranty FAQs."
    ),
    "service": (
        "This is a service page. Answers should build trust, clarify process, and highlight expertise."
    ),
    "local": (
        "This is a local business page. Answers should address local context, service area, "
        "and proximity where relevant."
    ),
    "general": "Write for a general audience. Keep answers clear and helpful.",
}

_UNSUPPORTED_CLAIM_GUARDRAIL = (
    "UNSUPPORTED CLAIM RULES:\n"
    "- Do not generate FAQ questions or answers about return, shipping, delivery, warranty, guarantee, "
    "eligibility, refund, exchange, availability, stock, pricing, discount, compliance, legal, medical, "
    "safety, or performance claims.\n"
    "- Exclude these topics entirely, even if they appear in PAA, AI Overview, scraped page content, "
    "or generic ecommerce expectations.\n"
    "- Prefer not to reference shipping or returns. Only use shipping or returns information when brand "
    "guidance explicitly provides the exact policy details to use.\n"
    "- Do not use PAA, AI Overview, scraped page content, or generic ecommerce assumptions as source data "
    "for shipping or returns.\n"
    "- Treat AI Overview and PAA as research signals, not proof of this business's actual policies, "
    "inventory, pricing, warranties, guarantees, or eligibility rules.\n"
    "- Do not use neutral fallback wording for these topics.\n"
    "- Do not tell readers to check the policy page, contact customer service, review terms, or confirm "
    "availability, pricing, shipping, returns, refunds, exchanges, warranties, guarantees, or eligibility.\n"
    "- Replace risky policy or claim questions with safer page-specific questions about product purpose, "
    "features, materials, fit considerations, compatibility, use cases, care, selection, or comparisons."
)


_ECOMMERCE_COLLECTION_GUARDRAIL = (
    "ECOMMERCE COLLECTION FAQ RULES:\n"
    "- Do not mention exact prices, sale prices, price ranges, or currency amounts from scraped products.\n"
    "- Do not mention exact product counts or imply a fixed number of products in the collection.\n"
    "- Do not mention exact sizes, filter values, inventory levels, SKU details, or availability claims.\n"
    "- Do not mention exact variant counts or imply a fixed number of variants.\n"
    "- Do not quote exact product names from the scraped collection unless the target keyword or page H1 is that exact product name.\n"
    "- Prefer stable category-level language such as selection, format, flavor, fit, use case, material, compatibility, or other supported category attributes.\n"
    "- Treat product cards and filters as navigation signals, not proof for durable claims."
)


_PRODUCT_NAME_NATURALNESS_GUARDRAIL = (
    "PRODUCT NAME NATURALNESS RULES:\n"
    "- Use the product name 2 or 3 times max across all questions and answers. Across the full FAQ set for this page, count every mention in both questions and answers.\n"
    "- This limit includes exact names, shortened product-name variations, reordered names, and partial names that still point to the same specific item.\n"
    "- Do not replace the full product name with half-name variations, such as using only the flavor, model, collection phrase, or distinctive modifier, unless that phrase describes the general item category rather than this specific product.\n"
    "- Prefer natural generic references such as 'this product', 'this item', 'it', 'this option', or a concise category phrase when the meaning is clear.\n"
    "- Do not force any product-name wording into every FAQ. Keep the language conversational and natural."
)


_BRAND_NAME_NATURALNESS_GUARDRAIL = (
    "BRAND NAME NATURALNESS RULES:\n"
    "- Use the brand name 2 or 3 times max across all questions and answers. Across the full FAQ set for this page, count every mention in both questions and answers.\n"
    "- Do not force the brand name into every FAQ, every answer, or repeated sentence openings.\n"
    "- Prefer natural references such as 'the team', 'the service', 'this company', 'this provider', 'it', or a concise page-specific phrase when the meaning is clear.\n"
    "- Use exact brand casing when the brand name appears, but do not add the brand name where it would sound repetitive or unnatural."
)


_MAIN_KEYWORD_NATURALNESS_GUARDRAIL = (
    "MAIN KEYWORD NATURALNESS RULES:\n"
    "- Use the main keyword 1 or 2 times across the full FAQ set for this page, counting both questions and answers.\n"
    "- Do not force the keyword into every FAQ, every answer, or repeated sentence openings.\n"
    "- Use close, natural phrasing only when it reads better for the user and still matches the page intent."
)


def _format_paa_answer_snippet(answer: str, max_chars: int = PAA_ANSWER_SNIPPET_CHARS) -> str:
    answer = " ".join((answer or "").split())
    if not answer or len(answer) <= max_chars:
        return answer

    sentence_cut = -1
    for mark in (".", "!", "?"):
        idx = answer.rfind(mark, 0, max_chars + 1)
        if idx > sentence_cut:
            sentence_cut = idx
    if sentence_cut >= 0:
        return answer[:sentence_cut + 1].strip()

    cut = answer[:max_chars].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return cut.rstrip(" ,;:-") + "..."


def _bottom_funnel_product_guardrail(business_type: str, page_type: str) -> str:
    business_type_norm = (business_type or "").lower()
    page_type_norm = (page_type or "").lower()
    if business_type_norm != "ecommerce" and not any(
        token in page_type_norm for token in ("product", "collection", "category")
    ):
        return ""
    return (
        "BOTTOM-OF-FUNNEL PRODUCT FAQ RULES:\n"
        "- For product, collection, category, and ecommerce pages, prioritise pre-purchase decision barriers over broad category education.\n"
        "- Prefer commercial-intent questions that can be answered from the provided page, H1, keyword, brand profile, AI Overview, PAA, or scraped context.\n"
        "- Do not invent product facts, specifications, ingredients, compatibility, sizing, materials, performance claims, or setup details.\n"
        "- Avoid top-of-funnel category education such as \"What is [product/category]?\" unless the page is genuinely informational or the product/category is unfamiliar.\n"
        "- If context is limited, use safer decision-support questions about choosing, comparing, use cases, care considerations, or what to look for, without making unsupported claims."
    )


def _structured_no_serp_fallback(ai_overview_sections: list, paa_items: list, ai_overview_raw: str = "") -> str:
    if ai_overview_sections or paa_items or (ai_overview_raw or "").strip():
        return ""
    return (
        "STRUCTURED FALLBACK WHEN SERP SIGNALS ARE EMPTY:\n"
        "No AI Overview or PAA data is available for this keyword. Build FAQs from the page, H1, keyword, brand profile, business type, and scraped context only.\n"
        "Choose the most relevant question categories for this page:\n"
        "- Decision fit: who this is for, when it is a good fit, when another option may be better.\n"
        "- Selection criteria: what to compare, what to check, what matters before choosing.\n"
        "- Process or next step: how to get started, what happens next, how to prepare.\n"
        "- Use case or application: practical scenarios, common needs, suitable situations.\n"
        "- Trust and expertise: proof points, experience, service approach, quality signals.\n"
        "- Care, setup, compatibility, or maintenance only if supported by the provided context.\n"
        "Do not invent facts. Do not create generic category education unless the page is informational. Every FAQ should connect back to this specific page."
    )


def _is_ecommerce_collection_context(business_type: str, page_type: str, page_context: str = "") -> bool:
    business_type_norm = (business_type or "").strip().lower()
    page_type_norm = (page_type or "").strip().lower()
    if business_type_norm != "ecommerce":
        return False
    return (
        "category" in page_type_norm
        or "collection" in page_type_norm
        or "COLLECTION CONTEXT" in (page_context or "")
    )


def _is_product_page(page_type: str) -> bool:
    return "product" in (page_type or "").strip().lower()


def _product_name_naturalness_guardrail(page_type: str) -> str:
    return _PRODUCT_NAME_NATURALNESS_GUARDRAIL if _is_product_page(page_type) else ""


def _brand_name_naturalness_guardrail(brand_name: str) -> str:
    return _BRAND_NAME_NATURALNESS_GUARDRAIL if (brand_name or "").strip() else ""


def _main_keyword_naturalness_guardrail(keyword: str) -> str:
    return _MAIN_KEYWORD_NATURALNESS_GUARDRAIL if (keyword or "").strip() else ""


_STRATEGY_FIELD_LABELS = {
    "search_intent": "Search intent",
    "page_goal": "Page goal",
    "audience_need": "Audience need",
    "recommended_angle": "Recommended angle",
    "brand_positioning": "Brand positioning",
    "proof_points_to_use": "Proof points to use",
    "claims_to_avoid": "Claims to avoid",
    "competitor_gaps": "Competitor gaps",
    "meta_direction": "Meta direction",
    "faq_direction": "FAQ direction",
    "section_guidance": "Section guidance",
}


def _clean_strategy_text(value, max_chars: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return sanitise(str(value))[:max_chars].strip()


def _clean_strategy_list(value, max_items: int = 6) -> list[str]:
    if not value:
        return []
    candidates = value if isinstance(value, list) else [value]
    items = []
    for item in candidates[:max_items]:
        text = _clean_strategy_text(item, 300)
        if text:
            items.append(text)
    return items


def _normalise_strategy_brief(data: dict) -> dict:
    brief = {}
    for key in (
        "search_intent",
        "page_goal",
        "audience_need",
        "recommended_angle",
        "brand_positioning",
        "meta_direction",
        "faq_direction",
    ):
        text = _clean_strategy_text(data.get(key), 700)
        if text:
            brief[key] = text

    for key in ("proof_points_to_use", "claims_to_avoid", "competitor_gaps"):
        items = _clean_strategy_list(data.get(key))
        if items:
            brief[key] = items

    section_items = []
    raw_sections = data.get("section_guidance") or []
    if not isinstance(raw_sections, list):
        raw_sections = [raw_sections]
    for item in raw_sections[:10]:
        if isinstance(item, dict):
            section = _clean_strategy_text(item.get("section") or item.get("name") or item.get("label"), 80)
            guidance = _clean_strategy_text(item.get("guidance") or item.get("direction") or item.get("notes"), 400)
            if guidance:
                section_items.append({"section": section, "guidance": guidance})
        else:
            text = _clean_strategy_text(item, 400)
            if text:
                section_items.append({"section": "", "guidance": text})
    if section_items:
        brief["section_guidance"] = section_items

    return brief


def format_strategy_brief_for_prompt(strategy_brief: dict | None) -> str:
    if not strategy_brief:
        return ""

    lines = []
    for key in _STRATEGY_FIELD_LABELS:
        value = strategy_brief.get(key)
        if not value:
            continue
        label = _STRATEGY_FIELD_LABELS[key]
        if key == "section_guidance" and isinstance(value, list):
            section_lines = []
            for item in value[:10]:
                if isinstance(item, dict):
                    section = _clean_strategy_text(item.get("section"), 80)
                    guidance = _clean_strategy_text(item.get("guidance"), 400)
                    if guidance:
                        prefix = f"{section}: " if section else ""
                        section_lines.append(f"- {prefix}{guidance}")
                else:
                    text = _clean_strategy_text(item, 400)
                    if text:
                        section_lines.append(f"- {text}")
            if section_lines:
                lines.append(f"{label}:\n" + "\n".join(section_lines))
        elif isinstance(value, list):
            item_lines = [f"- {_clean_strategy_text(item, 300)}" for item in value[:6] if _clean_strategy_text(item, 300)]
            if item_lines:
                lines.append(f"{label}:\n" + "\n".join(item_lines))
        else:
            text = _clean_strategy_text(value, 700)
            if text:
                lines.append(f"{label}: {text}")

    if not lines:
        return ""
    return "STRATEGY BRIEF:\n" + "\n".join(lines)[:SECTION_STRATEGY_BRIEF_CHAR_LIMIT]


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
    forbidden_phrases: str = "",
    reviewer_corrections: list[str] | None = None,
    strategy_brief: dict | None = None,
) -> str:
    kw_slot = section.get("keyword_slot", "none")
    wc_min, wc_max = section.get("word_count", [150, 250])

    if kw_slot == "primary":
        keyword_instruction = f"Include this keyword naturally: {primary_keyword}" if primary_keyword else ""
    elif kw_slot == "supporting":
        keyword_instruction = f"Include this keyword naturally: {supporting_keyword}" if supporting_keyword else ""
    elif kw_slot == "lsi":
        lsi_str = ", ".join(lsi_keywords[:SECTION_LSI_KEYWORD_LIMIT]) if lsi_keywords else ""
        keyword_instruction = f"Naturally cover these related terms where relevant: {lsi_str}" if lsi_str else ""
    else:
        keyword_instruction = ""

    paa_block = ""
    if paa_questions and section["name"] == "faq":
        paa_lines = "\n".join(f"- {q['question']}" for q in paa_questions[:SECTION_PAA_QUESTION_LIMIT])
        paa_block = f"\nPeople Also Ask questions to draw from:\n{paa_lines}"

    competitor_block = ""
    if competitor_excerpts:
        excerpts = "\n".join(f"- {e}" for e in competitor_excerpts[:SECTION_COMPETITOR_EXCERPT_LIMIT] if e.strip())
        if excerpts:
            competitor_block = f"\nWhat competitors cover in this section (use as context, not as copy):\n{excerpts}"

    existing_block = ""
    if client_existing_content and client_existing_content.strip():
        existing_block = f"\nClient's existing content on this topic (extract useful facts or claims, do not copy):\n{client_existing_content[:SECTION_EXISTING_CONTENT_CHAR_LIMIT]}"

    brief_block = ""
    if client_brief and client_brief.strip():
        brief_block = f"\nClient brief notes:\n{client_brief[:SECTION_CLIENT_BRIEF_CHAR_LIMIT]}"

    strategy_block = ""
    formatted_strategy = format_strategy_brief_for_prompt(strategy_brief)
    if formatted_strategy:
        strategy_block = f"\n{formatted_strategy}"

    prev_block = ""
    if previous_section_text and previous_section_text.strip():
        prev_block = f"\nPrevious section (for context and coherence, do not repeat):\n{previous_section_text[-SECTION_PREVIOUS_CONTEXT_CHAR_LIMIT:]}"

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
        ai_overview_block = f"\nGoogle AI Overview for this topic (use as reference for what topics to cover, do not copy):\n{ai_overview[:SECTION_AI_OVERVIEW_CHAR_LIMIT]}"

    forbidden_block = ""
    if forbidden_phrases and forbidden_phrases.strip():
        forbidden_block = f"- Never use these phrases: {forbidden_phrases.strip()}\n"

    brand_rule = (
        f"- If the brand name appears, use exact casing: {brand_name}. Do not force it into every section, paragraph, or sentence opening.\n"
        if brand_name
        else "- No brand name required.\n"
    )

    correction_block = ""
    cleaned_corrections = [str(note).strip() for note in (reviewer_corrections or []) if str(note).strip()]
    if cleaned_corrections:
        correction_lines = "\n".join(
            f"- {note[:SECTION_REVIEWER_NOTE_CHAR_LIMIT]}"
            for note in cleaned_corrections[-SECTION_REVIEWER_NOTE_LIMIT:]
        )
        correction_block = (
            "\nReviewer correction notes for this rerun:\n"
            f"{correction_lines}\n"
            "Treat the latest correction as highest priority while still following all hard rules."
        )

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
- No generic AI openings like 'In today's world', 'Great question', 'Finding the right', 'When it comes to', 'Choosing the right', 'Looking for', 'There are many', 'It can be difficult to', 'If you are searching for', 'Whether you need', or 'In the world of'
{forbidden_block}
- You may adjust word order, add small connecting words, or use a close grammatical variation when the exact keyword phrase would sound awkward.
- Do not force the keyword at the beginning of the first sentence.
- A keyword used awkwardly is worse than not using it. Quality of integration matters more than quantity.
- The first sentence must communicate the core topic, benefit, or value of the section. Do not warm up or establish generic context first.
- Do not write phrases like 'this page', 'this collection', 'this category', 'this range', or 'on this page'. Name the product, category, service, topic, brand, or location directly.
- Do not invent product groupings, package sizes, event scales, audience segments, delivery, returns, guarantees, pricing, availability, materials, ingredients, compatibility, or performance claims unless they are supported by client existing content, client brief, or brand context.
- Competitor context is topic inspiration, not proof of client facts.
- No fluff. Every sentence must add information or move the argument forward
{brand_rule.strip()}
- Return only the section copy. No preamble, no notes, no explanations.
{paa_block}{ai_overview_block}{competitor_block}{existing_block}{brief_block}{strategy_block}{prev_block}{correction_block}"""

    return prompt.strip()


# ── Provider functions ────────────────────────────────────────────────────────

def _extract_anthropic_text(content) -> str:
    """Return concatenated text blocks from an Anthropic Messages response."""
    text = "\n".join(
        str(block.text)
        for block in (content or [])
        if getattr(block, "type", "text") == "text" and getattr(block, "text", None)
    ).strip()
    if not text:
        raise RuntimeError("AI provider returned an empty text response")
    return text


def _anthropic_request_options(model: str, max_tokens: int) -> dict:
    return {"model": model, "max_tokens": max_tokens}


CLAUDE_STREAMING_TOKEN_THRESHOLD = 21000


def _extract_anthropic_stream_text(stream) -> str:
    chunks = []
    text_stream = getattr(stream, "text_stream", None)
    if text_stream is not None:
        for chunk in text_stream:
            chunks.append(str(chunk))
        return "".join(chunks).strip()

    for event in stream:
        if getattr(event, "type", "") != "content_block_delta":
            continue
        delta = getattr(event, "delta", None)
        if getattr(delta, "type", "") == "text_delta" and getattr(delta, "text", None):
            chunks.append(str(delta.text))

    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("AI provider returned an empty text response")
    return text


def _call_claude(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resolved_model = model or DEFAULT_MODELS["Claude"]
    request = {
        **_anthropic_request_options(resolved_model, max_tokens),
        "messages": [{"role": "user", "content": prompt}],
    }
    if max_tokens > CLAUDE_STREAMING_TOKEN_THRESHOLD:
        with client.messages.stream(**request) as stream:
            return _extract_anthropic_stream_text(stream)

    msg = client.messages.create(**request)
    return _extract_anthropic_text(msg.content)


def _call_openai(api_key: str, prompt: str, max_tokens: int = 1500, model: str = None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resolved_model = model or DEFAULT_MODELS["OpenAI"]
    token_limit = (
        {"max_completion_tokens": max_tokens}
        if resolved_model.startswith("gpt-5")
        else {"max_tokens": max_tokens}
    )
    resp = client.chat.completions.create(
        model=resolved_model,
        messages=[{"role": "user", "content": prompt}],
        **token_limit,
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
    "Claude": "claude-sonnet-5",
    "OpenAI": "gpt-5.5",
    "Gemini": "gemini-2.0-flash",
    "Gemini (free)": "gemini-2.0-flash",
    "Mistral": "mistral-small-latest",
    "Mistral (free tier)": "mistral-small-latest",
    "Groq": "llama-3.3-70b-versatile",
    "Groq (free tier)": "llama-3.3-70b-versatile",
}

PAGE_SECTION_MAX_TOKENS = 49152
FAQ_MAX_TOKENS = 16384
META_MAX_TOKENS = 8192
DIAGNOSTIC_MAX_TOKENS = 3000

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
    model: str = None,
    forbidden_phrases: str = "",
    progress_callback=None,
    strategy_brief: dict | None = None,
) -> dict:
    """
    Runs the section-by-section generation loop.
    Returns: { section_name: text, "_full_page": assembled markdown, "_word_count": int }
    """
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = model or DEFAULT_MODELS.get(provider)
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
            ai_overview=ai_overview,
            forbidden_phrases=forbidden_phrases,
            strategy_brief=strategy_brief,
        )

        try:
            raw = fn(api_key, prompt, max_tokens=PAGE_SECTION_MAX_TOKENS, model=resolved_model)
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
    brand_profile: dict = None,
    strategy_brief: dict | None = None,
) -> str:
    biz_ctx = _BIZ_CONTEXT_FAQ.get(business_type, _BIZ_CONTEXT_FAQ["general"])
    bp = brand_profile or {}
    bp_avoid = bp.get("words_to_avoid", "")
    combined_forbidden = ", ".join(filter(None, [(forbidden_phrases or "").strip(), bp_avoid.strip()]))
    forbidden_line = f"Never use these phrases: {combined_forbidden}" if combined_forbidden else ""
    collection_guardrail = (
        _ECOMMERCE_COLLECTION_GUARDRAIL
        if _is_ecommerce_collection_context(business_type, page_type, page_context)
        else ""
    )
    bottom_funnel_guardrail = _bottom_funnel_product_guardrail(business_type, page_type)
    product_name_guardrail = _product_name_naturalness_guardrail(page_type)
    brand_name_guardrail = _brand_name_naturalness_guardrail(brand_name)
    main_keyword_guardrail = _main_keyword_naturalness_guardrail(keyword)

    bp_lines = []
    if bp:
        if bp.get("brand_voice"):      bp_lines.append(f"Brand voice: {bp['brand_voice']}")
        if bp.get("tone"):             bp_lines.append(f"Tone: {bp['tone']}")
        if bp.get("target_audience"):  bp_lines.append(f"Target audience: {bp['target_audience']}")
        if bp.get("usps"):             bp_lines.append(f"Unique selling points: {bp['usps']}")
        if bp.get("key_messages"):     bp_lines.append(f"Key messages to reinforce: {bp['key_messages']}")
        if bp.get("competitors"):      bp_lines.append(f"Competitors (differentiate from): {bp['competitors']}")
        if bp.get("products_services"):bp_lines.append(f"Products/services: {bp['products_services']}")
        if bp.get("example_copy"):     bp_lines.append(f"Example copy to emulate in style (not content):\n{bp['example_copy']}")
    brand_profile_block = ("BRAND CONTEXT:\n" + "\n".join(bp_lines)) if bp_lines else ""
    strategy_block = format_strategy_brief_for_prompt(strategy_brief)

    paa_lines = []
    for item in paa_items[:num_faqs + 3]:
        question = item.get("question", "") if isinstance(item, dict) else str(item)
        if question:
            line = f"- Q: {question}"
            if isinstance(item, dict) and item.get("answer"):
                line += f" | Snippet: {_format_paa_answer_snippet(item['answer'])}"
            paa_lines.append(line)

    overview = ai_overview_raw or "\n".join(
        str(section.get("content") or section.get("title") or "")
        for section in ai_overview_sections
        if isinstance(section, dict)
    )
    serp_fallback_block = _structured_no_serp_fallback(ai_overview_sections, paa_items, ai_overview_raw)
    serp_fallback_block_str = f"\n{serp_fallback_block}\n" if serp_fallback_block else ""

    return f"""You are an expert SEO copywriter writing FAQ content for a web page. Your job is to generate questions that real buyers or visitors would ask about THIS SPECIFIC PAGE, then answer them in a way that could rank in Google AI Overviews.

Target keyword: {keyword}
Page type: {page_type}
Business type context: {biz_ctx}
Brand name: {brand_name or "N/A"}. When used, use exact casing.
Page H1 (context only, do not copy verbatim): {h1 or "Not provided"}
{forbidden_line}
{brand_profile_block}
{strategy_block}
{_UNSUPPORTED_CLAIM_GUARDRAIL}
{collection_guardrail}
{bottom_funnel_guardrail}
{product_name_guardrail}
{brand_name_guardrail}
{main_keyword_guardrail}
Page context: {page_context or "Not available"}
AI Overview: {overview or "Not available"}
People Also Ask:
{chr(10).join(paa_lines) or "Not available"}
{serp_fallback_block_str}

Rules:
- Questions and answers must be specific to this page.
- Use AI Overview and PAA data as research signals, but do not copy or rephrase questions verbatim.
- Only use AIO/PAA questions if genuinely relevant to that specific page.
- Never use forbidden phrases or em dashes.
- Lead each answer with a direct, complete response in the first sentence.
- Match answer length to question complexity:
  - Simple yes/no or definition questions: 1-2 direct sentences, about 20-45 words.
  - Comparison, selection, fit, material, compatibility, or use-case questions: about 45-80 words.
  - Complex how, why, or process questions: about 70-120 words when needed.
  - Do not pad short answers to hit a minimum. Do not cut complex answers before they are complete.
- Vary question starter types across the FAQ set. Do not let most questions start with the same word.
- For a 5-question set, use a natural mix such as What, How, Which, Can, Does, Is, When, or Why where relevant.
- Avoid using more than 2 questions with the same starter word in one page's FAQ set.
- Do not force awkward starters. Choose starters that match the page, search intent, and answer type.
- No filler openers (never: "Great question", "Certainly", "Of course", "Absolutely").
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
    strategy_brief: dict | None = None,
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
        brand_profile=brand_profile,
        strategy_brief=strategy_brief,
    )

    raw = fn(api_key, prompt, max_tokens=FAQ_MAX_TOKENS, model=resolved_model)
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
        business_type = p.get("business_type", "general")
        page_type = p.get("page_type", "general")
        biz_ctx = _BIZ_CONTEXT_FAQ.get(business_type, _BIZ_CONTEXT_FAQ["general"])
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
        collection_guardrail = (
            _ECOMMERCE_COLLECTION_GUARDRAIL
            if _is_ecommerce_collection_context(business_type, page_type, page_context)
            else ""
        )
        bottom_funnel_guardrail = _bottom_funnel_product_guardrail(business_type, page_type)
        product_name_guardrail = _product_name_naturalness_guardrail(page_type)
        brand_name_guardrail = _brand_name_naturalness_guardrail(brand_name)
        main_keyword_guardrail = _main_keyword_naturalness_guardrail(keyword)

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
                    line += f" | A: {_format_paa_answer_snippet(p2['answer'])}"
                paa_lines.append(line)
            paa_block = "PAA:\n" + "\n".join(paa_lines)
        else:
            paa_block = "PAA: not available"

        ai_overview_raw = p.get("ai_overview_raw", "")
        serp_fallback_block = _structured_no_serp_fallback(ao_sections, paa_items, ai_overview_raw)
        serp_fallback_block_str = f"\n{serp_fallback_block}\n" if serp_fallback_block else ""

        if used_patterns:
            patterns = "\n".join(f"- {p3}" for p3 in used_patterns[:15])
            used_block = f"Avoid repeating these question patterns from other pages where possible:\n{patterns}"
        else:
            used_block = ""

        block = f"""--- PAGE {i} ---
Keyword: {keyword}
Page type: {page_type}
Business type: {biz_ctx}
{h1_line}
{brand_line}
{forbidden_line}
{brand_profile_block}
{_UNSUPPORTED_CLAIM_GUARDRAIL}
{collection_guardrail}
{bottom_funnel_guardrail}
{product_name_guardrail}
{brand_name_guardrail}
{main_keyword_guardrail}

{ctx}

{ao_block}

{paa_block}
{serp_fallback_block_str}

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
- Match answer length to question complexity:
  - Simple yes/no or definition questions: 1-2 direct sentences, about 20-45 words.
  - Comparison, selection, fit, material, compatibility, or use-case questions: about 45-80 words.
  - Complex how, why, or process questions: about 70-120 words when needed.
  - Do not pad short answers to hit a minimum. Do not cut complex answers before they are complete.
- Use AI Overview sections as priority 1 signal, PAA as priority 2, page content as fallback
- Only use AIO/PAA questions if genuinely relevant to that specific page
- No em dashes. No filler openers ("Great question", "Certainly", "Of course", "Absolutely")
- Where possible, avoid repeating question patterns already used on other pages
- Vary question starter types across the FAQ set. Do not let most questions start with the same word.
- For a 5-question set, use a natural mix such as What, How, Which, Can, Does, Is, When, or Why where relevant.
- Avoid using more than 2 questions with the same starter word in one page's FAQ set.
- Do not force awkward starters. Choose starters that match the page, search intent, and answer type.
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


def _parse_json_object(raw: str, error_message: str) -> dict:
    raw = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        result = json.loads(raw[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError(error_message)
    return result


def _strategy_brief_paa_block(paa_questions: list) -> str:
    lines = []
    for item in (paa_questions or [])[:6]:
        question = item.get("question", "") if isinstance(item, dict) else str(item)
        if question:
            lines.append(f"- {question}")
    return "\n".join(lines) or "Not available"


def _strategy_brief_competitor_block(competitor_section_map: dict) -> str:
    lines = []
    for section, excerpts in (competitor_section_map or {}).items():
        for excerpt in (excerpts or [])[:2]:
            text = _clean_strategy_text(excerpt, 350)
            if text:
                lines.append(f"- {section}: {text}")
    return "\n".join(lines[:12]) or "Not available"


def _strategy_brief_template_block(template_sections: list) -> str:
    lines = []
    for section in (template_sections or [])[:12]:
        if not isinstance(section, dict):
            continue
        name = section.get("name") or ""
        label = section.get("label") or name
        purpose = section.get("purpose") or ""
        lines.append(f"- {name}: {label}. {purpose}".strip())
    return "\n".join(lines) or "Not available"


def generate_strategy_brief(
    provider: str,
    api_key: str,
    *,
    url: str,
    keyword: str,
    page_type: str,
    business_type: str,
    brand_name: str,
    h1: str = "",
    brand_context: str = "",
    client_brief: str = "",
    page_context: str = "",
    ai_overview: str = "",
    paa_questions: list | None = None,
    competitor_section_map: dict | None = None,
    template_sections: list | None = None,
    model: str = None,
) -> dict:
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = model or DEFAULT_MODELS.get(provider)
    brand_context_block = brand_context or "BRAND CONTEXT:\nNone"
    prompt = f"""Create a page-level strategy brief before writing copy.

This brief will be passed into meta, FAQ, and page-copy prompts. It must align all outputs around the same search intent, brand positioning, and page angle.

URL: {url}
Target keyword: {keyword}
Page type: {page_type}
Business type: {business_type}
Brand name: {brand_name or "N/A"}
Page H1: {h1 or "Not provided"}

{brand_context_block}

Client brief:
{client_brief[:STRATEGY_BRIEF_CONTEXT_CHAR_LIMIT] or "Not available"}

Current page context:
{page_context[:STRATEGY_BRIEF_CONTEXT_CHAR_LIMIT] or "Not available"}

Google AI Overview:
{ai_overview[:STRATEGY_BRIEF_CONTEXT_CHAR_LIMIT] or "Not available"}

People Also Ask:
{_strategy_brief_paa_block(paa_questions or [])}

Competitor section signals:
{_strategy_brief_competitor_block(competitor_section_map or {})}

Template sections:
{_strategy_brief_template_block(template_sections or [])}

Rules:
- Do not invent facts, policies, guarantees, pricing, certifications, availability, outcomes, or performance claims.
- Use competitors as gap/context signals only, not as proof about this client.
- If proof is missing, say what kind of proof is needed instead of inventing it.
- Keep the brief tactical and usable by copywriters.
- Return only strict JSON.

JSON schema:
{{
  "search_intent": "one sentence",
  "page_goal": "one sentence",
  "audience_need": "one sentence",
  "recommended_angle": "one sentence",
  "brand_positioning": "one sentence",
  "proof_points_to_use": ["supported proof point", "..."],
  "claims_to_avoid": ["risky or unsupported claim", "..."],
  "competitor_gaps": ["gap or opportunity", "..."],
  "meta_direction": "one sentence",
  "faq_direction": "one sentence",
  "section_guidance": [
    {{"section": "section_name", "guidance": "specific instruction for this section"}}
  ]
}}
"""

    raw = fn(api_key, prompt, max_tokens=STRATEGY_BRIEF_MAX_TOKENS, model=resolved_model)
    result = _parse_json_object(raw, "Strategy brief response must be a JSON object")
    return _normalise_strategy_brief(result)


def generate_copy(provider: str, api_key: str, **kwargs) -> dict:
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = kwargs.get("model") or DEFAULT_MODELS.get(provider)
    brand_context = kwargs.get("brand_context", "") or "BRAND CONTEXT:\nNone"
    strategy_block = format_strategy_brief_for_prompt(kwargs.get("strategy_brief"))
    prompt = f"""Write SEO metadata for this page.

URL: {kwargs.get("url", "")}
Target keyword: {kwargs.get("keyword", "")}
Page type: {kwargs.get("page_type", "general")}
Business type: {kwargs.get("business_type", "general")}
Brand name: {kwargs.get("brand_name", "") or "N/A"}
Current H1: {kwargs.get("h1", "") or "Not provided"}
Forbidden phrases: {kwargs.get("forbidden_phrases", "") or "None"}
Additional context: {kwargs.get("context", "") or "None"}

{brand_context}
{strategy_block}

Rules:
- Title should aim for up to 90 characters.
- Meta description should aim for up to 200 characters.
- H1 has no hard character limit but should aim for under 80 characters.
- Prioritise strong, natural copy over mechanically forcing the old 60/155-character limits.
- Include the target keyword naturally, ideally near the start where it fits.
- Never use forbidden phrases or em dashes.
- Return only a JSON object with keys: title, description, h1_optimised.
"""
    raw = fn(api_key, prompt, max_tokens=META_MAX_TOKENS, model=resolved_model)
    result = _parse_json_object(raw, "Meta response must be a JSON object")

    brand_name = kwargs.get("brand_name", "")
    return {
        "title": sanitise(result.get("title", ""), brand_name),
        "description": sanitise(result.get("description", ""), brand_name),
        "h1_optimised": sanitise(result.get("h1_optimised", ""), brand_name),
    }


def score_brand_consistency(
    provider: str,
    api_key: str,
    model: str = None,
    brand_profile: dict | None = None,
    outputs: dict | None = None,
) -> dict:
    fn = PROVIDER_FN.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")

    resolved_model = model or DEFAULT_MODELS.get(provider)
    profile = brand_profile or {}
    output_lines = []
    for label, value in (outputs or {}).items():
        text = str(value or "").strip()
        if text:
            output_lines.append(f"{label.upper()}:\n{text[:2500]}")

    prompt = f"""Score how closely the generated copy matches the brand profile.

BRAND PROFILE:
- Voice: {profile.get("brand_voice") or profile.get("voice") or "Not specified"}
- Tone: {profile.get("tone") or profile.get("tone_of_voice") or "Not specified"}
- Target audience: {profile.get("target_audience") or "Not specified"}
- USPs: {profile.get("usps") or "Not specified"}
- Key messages: {profile.get("key_messages") or "Not specified"}
- Words to avoid: {profile.get("words_to_avoid") or "Not specified"}
- Guidelines: {profile.get("guidelines") or "Not specified"}
- Example copy style: {profile.get("example_copy") or "Not specified"}

GENERATED OUTPUTS:
{chr(10).join(output_lines) or "No generated outputs provided."}

Return strict JSON with:
{{"score": 0-100, "reason": "one short sentence"}}

Score based only on brand voice, tone, avoided words, and alignment with the profile. Do not judge SEO quality or factual completeness.
"""

    raw = fn(api_key, prompt, max_tokens=DIAGNOSTIC_MAX_TOKENS, model=resolved_model)
    result = _parse_json_object(raw, "Brand consistency response must be a JSON object")

    try:
        score = int(result.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    reason = sanitise(str(result.get("reason", "")).strip())[:240]
    return {"score": score, "reason": reason}
