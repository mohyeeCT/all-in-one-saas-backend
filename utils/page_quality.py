"""Immutable, server-owned policy registries for current-AIO page copy."""

from dataclasses import dataclass
import os
from types import MappingProxyType
from typing import Mapping


PAGE_QUALITY_POLICY_V1 = "current-aio-page-quality-v1"
PAGE_QUALITY_POLICY_VERSION = "current-aio-page-quality-v2"
CLAIM_BOUND_RENDERER_VERSION = "current-aio-claim-bound-v1"
ADAPTIVE_POLICY_V1 = "current-aio-adaptive-v1"
ADAPTIVE_POLICY_VERSION = "current-aio-adaptive-v2"
DEFAULT_GUIDANCE_PROFILE_ID = "balanced"
PAGE_QUALITY_ROLLOUT_MODE_ENV = "AIO_PAGE_COPY_QUALITY_V1_MODE"
PAGE_QUALITY_ALLOWLIST_ENV = "AIO_PAGE_COPY_QUALITY_V1_USER_IDS"
PAGE_QUALITY_RERUNS_ENV = "AIO_PAGE_COPY_QUALITY_V1_RERUNS_ENABLED"


class PageQualityConfigurationError(ValueError):
    """Base error for a missing or unsupported stored quality contract."""


class UnknownGuidanceProfileError(PageQualityConfigurationError):
    """Raised when a client submits a guidance profile outside the allowlist."""


class UnsupportedGuidanceVersionError(PageQualityConfigurationError):
    """Raised when a stored guidance profile version is unavailable."""


class UnsupportedPolicyVersionError(PageQualityConfigurationError):
    """Raised when a stored page-quality or adaptive version is unavailable."""


@dataclass(frozen=True, slots=True)
class GuidanceProfile:
    id: str
    version: str
    label: str
    description: str
    prompt_instruction: str

    def safe_metadata(self) -> dict[str, str]:
        """Return browser-safe fields without exposing the executable prompt."""
        return {
            "id": self.id,
            "version": self.version,
            "label": self.label,
            "description": self.description,
        }

    def snapshot(self) -> dict[str, str]:
        """Return the minimal safe identity persisted with a versioned job."""
        return {"id": self.id, "version": self.version}


@dataclass(frozen=True, slots=True)
class DepthPolicy:
    id: str
    unsupported_proof_behavior: str
    prompt_instruction: str


@dataclass(frozen=True, slots=True)
class AdaptivePolicy:
    version: str
    depth_policies: tuple[DepthPolicy, ...]
    compact_ecommerce_templates: bool

    def depth_policy(self, policy_id: str) -> DepthPolicy:
        normalized_id = str(policy_id or "").strip()
        for policy in self.depth_policies:
            if policy.id == normalized_id:
                return policy
        raise PageQualityConfigurationError(
            f'Unknown depth policy "{normalized_id or "<missing>"}" '
            f'for adaptive policy "{self.version}"'
        )


@dataclass(frozen=True, slots=True)
class PageQualityPolicy:
    version: str
    adaptive_policy_version: str
    default_guidance_profile_id: str
    exact_planned_headings: bool
    coverage_points: bool
    bounded_owned_page_reuse: bool
    correction_contract: bool
    ecommerce_inventory_context_only: bool
    allow_sparse_ecommerce_generation: bool


_GUIDANCE_PROFILES_BY_KEY: Mapping[tuple[str, str], GuidanceProfile] = MappingProxyType({
    (
        "balanced",
        "1",
    ): GuidanceProfile(
        id="balanced",
        version="1",
        label="Balanced",
        description="Clear, specific, useful default copy.",
        prompt_instruction=(
            "Apply balanced page-copy guidance: write clear, specific, useful copy with a "
            "natural reader journey. Lead with reader-relevant meaning, develop complete "
            "explanations, and prefer concrete language over slogans or filler. Keep the "
            "section easy to scan without making it shallow. Do not repeat points or inflate "
            "claims to fill the available word range."
        ),
    ),
    (
        "conversion",
        "1",
    ): GuidanceProfile(
        id="conversion",
        version="1",
        label="Conversion Focus",
        description=(
            "Stronger value communication, objection handling, and appropriate CTA direction."
        ),
        prompt_instruction=(
            "Apply conversion-focused page-copy guidance: connect supported capabilities to "
            "reader value, give useful decision support, and address relevant objections using "
            "only the available evidence. Use an appropriate next step for the page intent. "
            "Keep persuasion proportionate and credible. Never invent urgency, guarantees, "
            "results, proof, prices, availability, or a contact or purchase route."
        ),
    ),
    (
        "editorial_refresh",
        "1",
    ): GuidanceProfile(
        id="editorial_refresh",
        version="1",
        label="Editorial Refresh",
        description=(
            "Stronger preservation, clarity, flow, and removal of stale or generic wording."
        ),
        prompt_instruction=(
            "Apply editorial-refresh page-copy guidance: preserve distinctive useful ideas and "
            "accurate specifics from the assigned owned-page material, while improving clarity, "
            "order, transitions, precision, and readability. Remove stale, generic, duplicated, "
            "or awkward phrasing. Treat assigned material as editorial source, not text to copy "
            "blindly, and do not introduce unsupported claims or erase useful nuance."
        ),
    ),
    (
        "search_ai_readability",
        "1",
    ): GuidanceProfile(
        id="search_ai_readability",
        version="1",
        label="Search & AI Readability",
        description=(
            "Direct answers, semantic clarity, topic coverage, and scannable structure "
            "without keyword stuffing."
        ),
        prompt_instruction=(
            "Apply search-and-AI-readability page-copy guidance: answer the section intent "
            "directly, make important topic and entity relationships explicit, and cover the "
            "assigned points in a logical, scannable structure. Use concise paragraphs or lists "
            "when they genuinely improve comprehension. Avoid keyword stuffing, repetitive "
            "exact-match phrases, fragmented answer snippets, and unsupported certainty."
        ),
    ),
})

_ACTIVE_GUIDANCE_VERSIONS: Mapping[str, str] = MappingProxyType({
    "balanced": "1",
    "conversion": "1",
    "editorial_refresh": "1",
    "search_ai_readability": "1",
})
_KNOWN_GUIDANCE_PROFILE_IDS = frozenset(
    profile_id for profile_id, _version in _GUIDANCE_PROFILES_BY_KEY
)

_DEPTH_POLICIES = (
    DepthPolicy(
        id="explanatory",
        unsupported_proof_behavior="retain",
        prompt_instruction=(
            "Retain the planned explanatory depth. Explain the topic safely and "
            "usefully without converting category-level context into a client-specific "
            "claim."
        ),
    ),
    DepthPolicy(
        id="claim_sensitive",
        unsupported_proof_behavior="compact_claim_areas",
        prompt_instruction=(
            "Keep the section useful, but compact or withhold areas that would require "
            "unsupported client-specific claims. Do not replace missing proof with "
            "generic promotional filler."
        ),
    ),
    DepthPolicy(
        id="proof_only",
        unsupported_proof_behavior="omit_or_withhold",
        prompt_instruction=(
            "Include only content supported by eligible client proof. If the required "
            "proof is unavailable, omit or clearly withhold the unsupported content "
            "rather than inventing it."
        ),
    ),
)

_ADAPTIVE_POLICIES: Mapping[str, AdaptivePolicy] = MappingProxyType({
    ADAPTIVE_POLICY_V1: AdaptivePolicy(
        version=ADAPTIVE_POLICY_V1,
        depth_policies=_DEPTH_POLICIES,
        compact_ecommerce_templates=False,
    ),
    ADAPTIVE_POLICY_VERSION: AdaptivePolicy(
        version=ADAPTIVE_POLICY_VERSION,
        depth_policies=_DEPTH_POLICIES,
        compact_ecommerce_templates=True,
    ),
})

_PAGE_QUALITY_POLICIES: Mapping[str, PageQualityPolicy] = MappingProxyType({
    PAGE_QUALITY_POLICY_V1: PageQualityPolicy(
        version=PAGE_QUALITY_POLICY_V1,
        adaptive_policy_version=ADAPTIVE_POLICY_V1,
        default_guidance_profile_id=DEFAULT_GUIDANCE_PROFILE_ID,
        exact_planned_headings=True,
        coverage_points=True,
        bounded_owned_page_reuse=True,
        correction_contract=True,
        ecommerce_inventory_context_only=False,
        allow_sparse_ecommerce_generation=False,
    ),
    PAGE_QUALITY_POLICY_VERSION: PageQualityPolicy(
        version=PAGE_QUALITY_POLICY_VERSION,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        default_guidance_profile_id=DEFAULT_GUIDANCE_PROFILE_ID,
        exact_planned_headings=True,
        coverage_points=True,
        bounded_owned_page_reuse=True,
        correction_contract=True,
        ecommerce_inventory_context_only=True,
        allow_sparse_ecommerce_generation=True,
    ),
})


def select_guidance_profile(profile_id: str | None = None) -> GuidanceProfile:
    """Resolve a new job's profile from the current server-owned allowlist."""
    normalized_id = str(profile_id or "").strip() or DEFAULT_GUIDANCE_PROFILE_ID
    version = _ACTIVE_GUIDANCE_VERSIONS.get(normalized_id)
    if version is None:
        raise UnknownGuidanceProfileError(
            f'Unknown page-copy guidance profile "{normalized_id}"'
        )
    return _GUIDANCE_PROFILES_BY_KEY[(normalized_id, version)]


def get_guidance_profile(profile_id: str, version: str) -> GuidanceProfile:
    """Resolve one exact stored profile version without falling forward."""
    normalized_id = str(profile_id or "").strip()
    normalized_version = str(version or "").strip()
    if not normalized_id:
        raise PageQualityConfigurationError(
            "Stored page-copy guidance profile is missing an id"
        )
    if normalized_id not in _KNOWN_GUIDANCE_PROFILE_IDS:
        raise UnknownGuidanceProfileError(
            f'Unknown page-copy guidance profile "{normalized_id}"'
        )
    if not normalized_version:
        raise UnsupportedGuidanceVersionError(
            f'Stored page-copy guidance profile "{normalized_id}" is missing a version'
        )

    profile = _GUIDANCE_PROFILES_BY_KEY.get((normalized_id, normalized_version))
    if profile is None:
        raise UnsupportedGuidanceVersionError(
            f'Page-copy guidance profile "{normalized_id}" version '
            f'"{normalized_version}" is unavailable'
        )
    return profile


def resolve_stored_guidance_profile(
    snapshot: Mapping[str, object] | None,
    *,
    versioned_job: bool,
) -> GuidanceProfile | None:
    """Resolve a rerun profile, preserving legacy behavior for historical jobs."""
    if not versioned_job:
        return None
    if not isinstance(snapshot, Mapping):
        raise PageQualityConfigurationError(
            "Versioned job is missing its stored page-copy guidance profile"
        )
    return get_guidance_profile(
        str(snapshot.get("id") or ""),
        str(snapshot.get("version") or ""),
    )


def guidance_capability_payload(enabled: bool) -> dict[str, object]:
    """Return safe capability metadata for the current active profile versions."""
    profiles = [
        _GUIDANCE_PROFILES_BY_KEY[(profile_id, version)].safe_metadata()
        for profile_id, version in _ACTIVE_GUIDANCE_VERSIONS.items()
    ]
    return {
        "enabled": bool(enabled),
        "default_profile_id": DEFAULT_GUIDANCE_PROFILE_ID,
        "profiles": profiles,
    }


def page_quality_creation_enabled(
    user_id: object,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Resolve the server-only rollout state for one user."""
    values = os.environ if environment is None else environment
    mode = str(values.get(PAGE_QUALITY_ROLLOUT_MODE_ENV, "off")).strip().casefold()
    if mode == "on":
        return True
    if mode != "allowlist":
        return False
    allowed_user_ids = {
        value.strip()
        for value in str(values.get(PAGE_QUALITY_ALLOWLIST_ENV, "")).split(",")
        if value.strip()
    }
    return str(user_id or "").strip() in allowed_user_ids


def page_quality_reruns_enabled(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Allow emergency shutdown of v1 reruns without changing old jobs."""
    values = os.environ if environment is None else environment
    raw_value = str(values.get(PAGE_QUALITY_RERUNS_ENV, "true")).strip().casefold()
    return raw_value not in {"0", "false", "off", "no"}


def is_legacy_quality_job(page_quality_policy_version: object) -> bool:
    """Only absence of the umbrella version selects historical behavior."""
    return not str(page_quality_policy_version or "").strip()


def get_page_quality_policy(version: str) -> PageQualityPolicy:
    """Resolve an exact umbrella policy version for a versioned job."""
    normalized_version = str(version or "").strip()
    if not normalized_version:
        raise UnsupportedPolicyVersionError(
            "Versioned job is missing its page-quality policy version"
        )
    policy = _PAGE_QUALITY_POLICIES.get(normalized_version)
    if policy is None:
        raise UnsupportedPolicyVersionError(
            f'Page-quality policy version "{normalized_version}" is unavailable'
        )
    return policy


def get_adaptive_policy(version: str) -> AdaptivePolicy:
    """Resolve an exact adaptive policy version for a versioned job."""
    normalized_version = str(version or "").strip()
    if not normalized_version:
        raise UnsupportedPolicyVersionError(
            "Versioned job is missing its adaptive policy version"
        )
    policy = _ADAPTIVE_POLICIES.get(normalized_version)
    if policy is None:
        raise UnsupportedPolicyVersionError(
            f'Adaptive policy version "{normalized_version}" is unavailable'
        )
    return policy


def resolve_claim_bound_renderer_version(version: object) -> str:
    """Resolve the optional stored renderer contract without falling forward."""
    normalized_version = str(version or "").strip()
    if not normalized_version:
        return ""
    if normalized_version != CLAIM_BOUND_RENDERER_VERSION:
        raise UnsupportedPolicyVersionError(
            f'Claim-bound renderer version "{normalized_version}" is unavailable'
        )
    return normalized_version
