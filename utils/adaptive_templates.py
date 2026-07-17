"""Evidence-aware runtime section policies for AIO page generation."""

from copy import deepcopy


ADAPTIVE_TEMPLATE_POLICIES = {
    "blog_standard": {
        "family": "informational",
        "responsive_sections": frozenset(),
        "proof_only_sections": frozenset(),
    },
    "blog_listicle": {
        "family": "informational",
        "responsive_sections": frozenset(),
        "proof_only_sections": frozenset(),
    },
    "blog_howto": {
        "family": "informational",
        "responsive_sections": frozenset(),
        "proof_only_sections": frozenset(),
    },
    "blog_comparison": {
        "family": "informational",
        "responsive_sections": frozenset(),
        "proof_only_sections": frozenset(),
    },
    "glossary": {
        "family": "informational",
        "responsive_sections": frozenset(),
        "proof_only_sections": frozenset(),
    },
    "homepage": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "trust_bar", "services_overview", "differentiators", "social_proof",
        }),
        "proof_only_sections": frozenset({"trust_bar", "social_proof"}),
    },
    "landing_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "value_context", "decision_support", "proof_or_context", "support_notes",
        }),
        "proof_only_sections": frozenset(),
    },
    "service_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "benefits", "pain_points", "solution", "social_proof", "process", "support_notes",
        }),
        "proof_only_sections": frozenset({"social_proof"}),
    },
    "local_service_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "local_intro", "services_in_location", "why_local", "service_area",
            "local_social_proof", "support_notes",
        }),
        "proof_only_sections": frozenset({"local_social_proof"}),
    },
    "product_page": {
        "family": "ecommerce",
        "responsive_sections": frozenset({
            "benefits_features", "use_cases", "social_proof", "support_notes",
        }),
        "proof_only_sections": frozenset({"social_proof"}),
    },
    "collection_page": {
        "family": "ecommerce",
        "responsive_sections": frozenset({"collection_guidance"}),
        "proof_only_sections": frozenset(),
    },
    "about_us": {
        "family": "brand",
        "responsive_sections": frozenset({
            "company_story", "mission_values", "credibility", "team",
        }),
        "proof_only_sections": frozenset({
            "company_story", "mission_values", "credibility", "team",
        }),
    },
    "contact_us": {
        "family": "brand",
        "responsive_sections": frozenset({
            "expectations", "contact_methods", "pre_contact_faq",
        }),
        "proof_only_sections": frozenset(),
    },
    "case_study_b2b": {
        "family": "case_study",
        "responsive_sections": frozenset({
            "situation", "trigger", "barrier", "solution", "results", "quote", "support_notes",
        }),
        "proof_only_sections": frozenset({"results", "quote"}),
    },
}


_INFORMATIONAL_INSTRUCTION = (
    "Preserve this template's defining format and reader journey. Treat numeric item, example, "
    "step, or question counts as coverage guidance within the word range, not a quota to fill "
    "with repetitive material. Honor any exact number promised in the page H1 or section purpose."
)

_FLEXIBLE_STRUCTURE_INSTRUCTION = (
    "Use only as many blocks, items, examples, or steps as have distinct supported value. "
    "Treat numeric counts in the section-specific rules as ceilings rather than quotas."
)

_COMPACT_INSTRUCTION = (
    "Use compact mode. Fulfil the section responsibility in the fewest complete paragraphs or "
    "blocks supported by its owned proof points. Do not add filler or extra claims to satisfy a "
    "numeric count."
)


def _section_contracts(strategy_brief: dict | None) -> dict[str, dict]:
    contracts = {}
    for item in (strategy_brief or {}).get("section_guidance") or []:
        if not isinstance(item, dict):
            continue
        section_name = str(item.get("section") or "").strip().casefold()
        if section_name:
            contracts[section_name] = item
    return contracts


def _proof_point_count(contract: dict | None) -> int:
    proof_points = (contract or {}).get("proof_points") or []
    if not isinstance(proof_points, list):
        proof_points = [proof_points]
    return sum(
        1
        for item in proof_points
        if str(item or "").strip()
    )


def _compact_word_count(word_count) -> list[int]:
    if not isinstance(word_count, (list, tuple)) or len(word_count) != 2:
        return [150, 250]
    try:
        original_min, original_max = (int(value) for value in word_count)
    except (TypeError, ValueError):
        return [150, 250]

    compact_max = min(original_max, max(60, int(original_max * 0.8)))
    compact_min = max(35, min(int(original_min * 0.7), compact_max - 25))
    return [compact_min, compact_max]


def _base_instruction(family: str) -> str:
    if family == "informational":
        return _INFORMATIONAL_INSTRUCTION
    return _FLEXIBLE_STRUCTURE_INSTRUCTION


def adapt_template_for_generation(
    template: dict,
    template_key: str,
    strategy_brief: dict | None,
) -> tuple[dict, list[dict]]:
    """Apply conservative, evidence-aware runtime modes without mutating the registry."""
    adapted = deepcopy(template)
    policy = ADAPTIVE_TEMPLATE_POLICIES.get(template_key, {})
    family = policy.get("family", "custom")
    responsive_sections = policy.get("responsive_sections", frozenset())
    proof_only_sections = policy.get("proof_only_sections", frozenset())
    contracts = _section_contracts(strategy_brief)
    adapted_sections = []
    plan = []

    for source_section in adapted.get("sections") or []:
        section = deepcopy(source_section)
        section_name = str(section.get("name") or "").casefold()
        keyword_slot = str(section.get("keyword_slot") or "none").casefold()
        contract = contracts.get(section_name)
        proof_count = _proof_point_count(contract)
        original_word_count = list(section.get("word_count") or [150, 250])
        mode = "full"
        reason = "original_structure"
        instruction = _base_instruction(family)

        if contract is None:
            reason = "no_section_contract"
        elif section_name in proof_only_sections and proof_count == 0:
            if keyword_slot == "none":
                mode = "omit"
                reason = "no_owned_proof"
            else:
                mode = "compact"
                reason = "keyword_section_without_owned_proof"
        elif section_name in responsive_sections and proof_count == 0:
            mode = "compact"
            reason = "no_owned_proof"
        elif section_name in responsive_sections:
            reason = "sufficient_owned_proof"

        if mode == "omit":
            plan.append({
                "section": section.get("name", ""),
                "label": section.get("label", ""),
                "mode": mode,
                "reason": reason,
                "proof_point_count": proof_count,
                "original_word_count": original_word_count,
                "word_count": None,
            })
            continue

        if mode == "compact":
            section["word_count"] = _compact_word_count(original_word_count)
            instruction = _COMPACT_INSTRUCTION

        section["adaptive_mode"] = mode
        section["adaptive_instruction"] = instruction
        adapted_sections.append(section)
        plan.append({
            "section": section.get("name", ""),
            "label": section.get("label", ""),
            "mode": mode,
            "reason": reason,
            "proof_point_count": proof_count,
            "original_word_count": original_word_count,
            "word_count": list(section.get("word_count") or original_word_count),
        })

    adapted["sections"] = adapted_sections
    adapted["_adaptive_family"] = family
    return adapted, plan
