"""Evidence-aware runtime section policies for AIO page generation."""

from copy import deepcopy
from types import MappingProxyType

from utils.page_quality import (
    ADAPTIVE_POLICY_VERSION,
    PageQualityConfigurationError,
    get_adaptive_policy,
)


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
        "claim_sensitive_sections": frozenset({
            "services_overview", "differentiators",
        }),
        "proof_only_sections": frozenset({"trust_bar", "social_proof"}),
    },
    "landing_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "value_context", "decision_support", "proof_or_context", "support_notes",
        }),
        "claim_sensitive_sections": frozenset({"proof_or_context"}),
        "proof_only_sections": frozenset(),
    },
    "service_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "benefits", "pain_points", "solution", "social_proof", "process", "support_notes",
        }),
        "claim_sensitive_sections": frozenset({"benefits", "solution", "process"}),
        "proof_only_sections": frozenset({"social_proof"}),
    },
    "local_service_page": {
        "family": "lead_generation",
        "responsive_sections": frozenset({
            "local_intro", "services_in_location", "why_local", "service_area",
            "local_social_proof", "support_notes",
        }),
        "claim_sensitive_sections": frozenset({
            "services_in_location", "why_local", "service_area",
        }),
        "proof_only_sections": frozenset({"local_social_proof"}),
    },
    "product_page": {
        "family": "ecommerce",
        "responsive_sections": frozenset({
            "benefits_features", "use_cases", "social_proof", "support_notes",
        }),
        "claim_sensitive_sections": frozenset({"benefits_features", "use_cases"}),
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
        "claim_sensitive_sections": frozenset(),
        "proof_only_sections": frozenset({
            "company_story", "mission_values", "credibility", "team",
        }),
    },
    "contact_us": {
        "family": "brand",
        "responsive_sections": frozenset({
            "expectations", "contact_methods", "pre_contact_faq",
        }),
        "claim_sensitive_sections": frozenset({"contact_methods"}),
        "proof_only_sections": frozenset(),
    },
    "case_study_b2b": {
        "family": "case_study",
        "responsive_sections": frozenset({
            "situation", "trigger", "barrier", "solution", "results", "quote", "support_notes",
        }),
        "claim_sensitive_sections": frozenset({
            "situation", "trigger", "barrier", "solution",
        }),
        "proof_only_sections": frozenset({"results", "quote"}),
    },
}

_ADAPTIVE_TEMPLATE_POLICIES_BY_VERSION = MappingProxyType({
    ADAPTIVE_POLICY_VERSION: MappingProxyType({
        template_key: MappingProxyType(dict(template_policy))
        for template_key, template_policy in ADAPTIVE_TEMPLATE_POLICIES.items()
    }),
})


def _versioned_template_policy(
    adaptive_policy_version: str,
    template_key: str,
):
    get_adaptive_policy(adaptive_policy_version)
    versioned_policies = _ADAPTIVE_TEMPLATE_POLICIES_BY_VERSION.get(
        adaptive_policy_version
    )
    if versioned_policies is None:
        raise PageQualityConfigurationError(
            f'Adaptive template policy version "{adaptive_policy_version}" is unavailable'
        )
    return versioned_policies.get(template_key, MappingProxyType({}))


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


def depth_policy_for_section(
    template_key: str,
    section_name: str,
    adaptive_policy_version: str,
) -> str:
    """Return a reviewed server-owned depth class for one template section."""
    template_policy = _versioned_template_policy(
        adaptive_policy_version,
        template_key,
    )
    normalized_name = str(section_name or "").strip().casefold()
    if normalized_name in template_policy.get("proof_only_sections", frozenset()):
        return "proof_only"
    if normalized_name in template_policy.get("claim_sensitive_sections", frozenset()):
        return "claim_sensitive"
    return "explanatory"


def attach_depth_policies(
    strategy_brief: dict | None,
    template_key: str,
    adaptive_policy_version: str,
) -> dict:
    """Attach reviewed depth classes after model-output normalization."""
    get_adaptive_policy(adaptive_policy_version)
    values = deepcopy(strategy_brief or {})
    section_guidance = []
    for item in values.get("section_guidance") or []:
        if not isinstance(item, dict):
            continue
        normalized_item = deepcopy(item)
        normalized_item.pop("depth_policy", None)
        normalized_item["depth_policy"] = depth_policy_for_section(
            template_key,
            normalized_item.get("section", ""),
            adaptive_policy_version,
        )
        section_guidance.append(normalized_item)
    if section_guidance:
        values["section_guidance"] = section_guidance
    return values


def adapt_template_for_generation(
    template: dict,
    template_key: str,
    strategy_brief: dict | None,
    adaptive_policy_version: str = "",
) -> tuple[dict, list[dict]]:
    """Apply conservative, evidence-aware runtime modes without mutating the registry."""
    adapted = deepcopy(template)
    versioned_policy = (
        get_adaptive_policy(adaptive_policy_version)
        if adaptive_policy_version
        else None
    )
    policy = (
        _versioned_template_policy(adaptive_policy_version, template_key)
        if versioned_policy
        else ADAPTIVE_TEMPLATE_POLICIES.get(template_key, {})
    )
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
        if contract and contract.get("planned_heading"):
            section["planned_heading"] = str(contract["planned_heading"])
        if contract and contract.get("coverage_points"):
            section["coverage_points"] = list(contract["coverage_points"])
        original_word_count = list(section.get("word_count") or [150, 250])
        mode = "full"
        reason = "original_structure"
        instruction = _base_instruction(family)
        depth_policy = ""

        if versioned_policy:
            depth_policy = depth_policy_for_section(
                template_key,
                section_name,
                adaptive_policy_version,
            )
            depth_instruction = versioned_policy.depth_policy(depth_policy).prompt_instruction
            instruction = f"{instruction} {depth_instruction}".strip()

        if contract is None:
            reason = "no_section_contract"
        elif (
            versioned_policy
            and depth_policy == "proof_only"
            and proof_count == 0
        ):
            if keyword_slot == "none":
                mode = "omit"
                reason = "no_owned_proof"
            else:
                mode = "compact"
                reason = "keyword_section_without_owned_proof"
        elif (
            versioned_policy
            and depth_policy == "claim_sensitive"
            and proof_count == 0
        ):
            reason = "unsupported_claim_areas"
        elif versioned_policy:
            reason = (
                "safe_explanatory_depth"
                if depth_policy == "explanatory"
                else "sufficient_owned_proof"
            )
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
            plan_item = {
                "section": section.get("name", ""),
                "label": section.get("label", ""),
                "mode": mode,
                "reason": reason,
                "proof_point_count": proof_count,
                "original_word_count": original_word_count,
                "word_count": None,
            }
            if versioned_policy:
                plan_item.update({
                    "depth_policy": depth_policy,
                    "adaptive_policy_version": adaptive_policy_version,
                })
            plan.append(plan_item)
            continue

        if mode == "compact":
            section["word_count"] = _compact_word_count(original_word_count)
            instruction = (
                f"{_COMPACT_INSTRUCTION} {instruction}".strip()
                if versioned_policy
                else _COMPACT_INSTRUCTION
            )

        section["adaptive_mode"] = mode
        section["adaptive_instruction"] = instruction
        if depth_policy:
            section["depth_policy"] = depth_policy
            section["adaptive_policy_version"] = adaptive_policy_version
        adapted_sections.append(section)
        plan_item = {
            "section": section.get("name", ""),
            "label": section.get("label", ""),
            "mode": mode,
            "reason": reason,
            "proof_point_count": proof_count,
            "original_word_count": original_word_count,
            "word_count": list(section.get("word_count") or original_word_count),
        }
        if versioned_policy:
            plan_item.update({
                "depth_policy": depth_policy,
                "adaptive_policy_version": adaptive_policy_version,
            })
        plan.append(plan_item)

    adapted["sections"] = adapted_sections
    adapted["_adaptive_family"] = family
    return adapted, plan
