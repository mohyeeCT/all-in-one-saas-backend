"""Evidence-aware runtime section policies for AIO page generation."""

from copy import deepcopy
import re
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
        "recap_sections": frozenset({"summary"}),
        "evidence_bounded_sections": frozenset({
            "intro",
            "context",
            "body_1",
            "body_2",
            "body_3",
            "summary",
            "faq",
            "cta",
        }),
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
        "evidence_bounded_sections": frozenset({
            "hero",
            "local_intro",
            "services_in_location",
            "why_local",
            "service_area",
            "faq",
            "cta",
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

_EVIDENCE_SPARSE_INSTRUCTION = (
    "This section has too little authored evidence for the template's normal depth. There is no "
    "authored minimum. Preserve its keyword slot and required heading, but use only the shortest "
    "complete supported treatment or one evidence-neutral transition. Numeric paragraph, block, "
    "item, and question counts are ceilings, never quotas."
)

_CLOSING_CTA_SECTION_NAMES = frozenset({
    "cta",
    "cta_close",
    "closing",
    "final_cta",
})
_EVIDENCE_EXPANSION_MULTIPLIER = 2
_EVIDENCE_TRANSITION_WORD_ALLOWANCE = 40
_EVIDENCE_SPARSE_AUTHORED_MAX = 60
_RECAP_AUTHORED_MAX = 180


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


def _authored_evidence_values(contract: dict | None) -> list[str]:
    """Return unique evidence that can authorize prose, excluding marker units."""
    values = []
    proof_facts = [
        item
        for item in (contract or {}).get("proof_facts") or []
        if isinstance(item, dict)
    ]
    proof_values = (
        [
            item.get("source_excerpt") or item.get("fact")
            for item in proof_facts
        ]
        if proof_facts
        else (contract or {}).get("proof_points") or []
    )
    if not isinstance(proof_values, list):
        proof_values = [proof_values]
    for proof_value in proof_values:
        text = str(proof_value or "").strip()
        if text:
            values.append(text)
    for asset in (contract or {}).get("source_assets") or []:
        if not isinstance(asset, dict) or asset.get("kind") != "direct_statement":
            continue
        statement = str(asset.get("statement") or "").strip()
        if statement:
            values.append(statement)
    unique_values = []
    seen = set()
    for value in values:
        key = " ".join(value.casefold().split())
        if key and key not in seen:
            seen.add(key)
            unique_values.append(value)
    return unique_values


def _authored_evidence_count(contract: dict | None) -> int:
    return len(_authored_evidence_values(contract))


def _authored_evidence_word_count(contract: dict | None) -> int:
    return sum(
        len(re.findall(r"[^\W_]+(?:[’'-][^\W_]+)*", value, re.UNICODE))
        for value in _authored_evidence_values(contract)
    )


def _structured_source_word_count(contract: dict | None) -> int:
    """Count exact named-list/testimonial words materialized by the server."""
    values = []
    for asset in (contract or {}).get("source_assets") or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("kind") == "named_list":
            values.extend(
                str(item).strip()
                for item in asset.get("items") or []
                if str(item or "").strip()
            )
        elif asset.get("kind") == "testimonial":
            values.extend(
                str(value).strip()
                for value in (
                    asset.get("quote"),
                    asset.get("attribution"),
                )
                if str(value or "").strip()
            )
    return sum(
        len(re.findall(r"[^\W_]+(?:[’'-][^\W_]+)*", value, re.UNICODE))
        for value in values
    )


def _evidence_sparse_word_count(
    word_count,
    contract: dict | None,
    *,
    authored_evidence_count: int,
    authored_evidence_word_count: int,
    recap_section: bool,
) -> list[int]:
    try:
        original_max = int(word_count[1])
    except (IndexError, TypeError, ValueError):
        original_max = 250

    source_words = _structured_source_word_count(contract)
    if recap_section:
        authored_max = _RECAP_AUTHORED_MAX
    elif authored_evidence_count:
        authored_max = max(
            _EVIDENCE_SPARSE_AUTHORED_MAX,
            (
                authored_evidence_word_count
                * _EVIDENCE_EXPANSION_MULTIPLIER
            )
            + _EVIDENCE_TRANSITION_WORD_ALLOWANCE,
        )
    else:
        authored_max = _EVIDENCE_SPARSE_AUTHORED_MAX
    visible_max = max(
        source_words,
        min(original_max, source_words + authored_max),
    )
    return [0, max(1, visible_max)]


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
    correction_evidence_contract: bool = False,
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
    recap_sections = policy.get("recap_sections", frozenset())
    evidence_bounded_sections = policy.get(
        "evidence_bounded_sections",
        frozenset(),
    )
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
        authored_evidence_count = _authored_evidence_count(contract)
        authored_evidence_word_count = _authored_evidence_word_count(contract)
        if contract and contract.get("planned_heading"):
            section["planned_heading"] = str(contract["planned_heading"])
        if contract and contract.get("coverage_points"):
            section["coverage_points"] = list(contract["coverage_points"])
        original_word_count = list(section.get("word_count") or [150, 250])
        bounded_word_count = _evidence_sparse_word_count(
            original_word_count,
            contract,
            authored_evidence_count=authored_evidence_count,
            authored_evidence_word_count=authored_evidence_word_count,
            recap_section=section_name in recap_sections,
        )
        mode = "full"
        reason = "original_structure"
        instruction = _base_instruction(family)
        depth_policy = ""
        evidence_sparse = False

        if versioned_policy:
            depth_policy = depth_policy_for_section(
                template_key,
                section_name,
                adaptive_policy_version,
            )
            depth_instruction = versioned_policy.depth_policy(depth_policy).prompt_instruction
            instruction = f"{instruction} {depth_instruction}".strip()
            evidence_sparse = bool(
                correction_evidence_contract
                and contract is not None
                and depth_policy != "proof_only"
                and (
                    (
                        section_name == "faq"
                        and authored_evidence_count <= 1
                    )
                    or (
                        section_name in recap_sections
                        and authored_evidence_count == 0
                    )
                    or (
                        section_name in responsive_sections
                        and authored_evidence_count == 0
                    )
                    or (
                        section_name in _CLOSING_CTA_SECTION_NAMES
                        and authored_evidence_count == 0
                    )
                    or (
                        depth_policy == "claim_sensitive"
                        and authored_evidence_count == 0
                    )
                    or (
                        section_name in evidence_bounded_sections
                        and bounded_word_count[1]
                        < int(original_word_count[0])
                    )
                )
            )

        if (
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
        elif contract is None:
            reason = "no_section_contract"
        elif evidence_sparse:
            mode = "compact"
            if (
                depth_policy == "claim_sensitive"
                and authored_evidence_count == 0
            ):
                reason = "unsupported_claim_areas"
            elif section_name == "faq" or section_name in recap_sections:
                reason = "limited_recap_evidence"
            else:
                reason = "insufficient_owned_evidence"
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
            if evidence_sparse:
                section["word_count"] = bounded_word_count
                section["evidence_sparse"] = True
                instruction = (
                    f"{_EVIDENCE_SPARSE_INSTRUCTION} "
                    f"{_COMPACT_INSTRUCTION} {instruction}"
                ).strip()
            else:
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
        if evidence_sparse:
            plan_item.update({
                "evidence_sparse": True,
                "authored_evidence_count": authored_evidence_count,
            })
        plan.append(plan_item)

    adapted["sections"] = adapted_sections
    adapted["_adaptive_family"] = family
    return adapted, plan
