"""Bounded, deterministic owned-page block mapping.

This module is deliberately pure. It operates only on already-retained
Markdown and never performs a scrape or another network request.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from utils.page_quality import PageQualityConfigurationError


OWNED_PAGE_MAPPING_VERSION = "current-aio-owned-blocks-v1"
SOURCE_ASSET_MANIFEST_VERSION = "current-aio-source-assets-v1"

OWNED_PAGE_SOURCE_MAX_CHARS = 10_000
OWNED_PAGE_MAX_BLOCKS = 24
OWNED_PAGE_BLOCK_MAX_CHARS = 800
OWNED_PAGE_REGISTRY_MAX_CHARS = 7_200
OWNED_PAGE_HEADING_MAX_CHARS = 180
OWNED_PAGE_MAX_BLOCKS_PER_SECTION = 3
OWNED_PAGE_SECTION_MAX_CHARS = 2_400

_ATX_HEADING_RE = re.compile(
    r"^[ \t]{0,3}(?P<marks>#{1,6})[ \t]+(?P<heading>.*?)[ \t]*$"
)
_SETEXT_HEADING_RE = re.compile(r"^[ \t]{0,3}(?P<marks>=+|-+)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<marks>`{3,}|~{3,})")
_OWNED_BLOCK_ID_RE = re.compile(r"^O[1-9]\d*$")
_SOURCE_ASSET_LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]{0,3})[-+*][ \t]+(?P<item>.*?)[ \t]*$"
)
_SOURCE_ASSET_QUOTE_LINE_RE = re.compile(
    r"^[ \t]{0,3}>[ \t]?(?P<text>.*)$"
)
_SOURCE_ASSET_RICH_MARKDOWN_RE = re.compile(
    r"(?:`|!\[|\[[^\]]*\][ \t]*\(|</?[A-Za-z][^>]*>|\*\*|__)"
)
_SOURCE_ASSET_TASK_ITEM_RE = re.compile(r"^\[[ xX]\][ \t]+")
_SOURCE_ASSET_EMBEDDED_LIST_RE = re.compile(
    r"^(?:[-+*]|\d+[.)])[ \t]+"
)
_SOURCE_ASSET_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_VALID_HEADING_LEVELS = {"none", "h1", "h2", "h3", "h4", "h5", "h6"}
_TESTIMONIAL_ATTRIBUTION_MAX_CHARS = 120
_TESTIMONIAL_ATTRIBUTION_MAX_WORDS = 12


@dataclass(frozen=True, slots=True)
class OwnedPageMappingPolicy:
    version: str
    source_max_chars: int
    max_blocks: int
    block_max_chars: int
    registry_max_chars: int
    heading_max_chars: int
    max_blocks_per_section: int
    section_max_chars: int


_OWNED_PAGE_MAPPING_POLICIES = MappingProxyType({
    OWNED_PAGE_MAPPING_VERSION: OwnedPageMappingPolicy(
        version=OWNED_PAGE_MAPPING_VERSION,
        source_max_chars=OWNED_PAGE_SOURCE_MAX_CHARS,
        max_blocks=OWNED_PAGE_MAX_BLOCKS,
        block_max_chars=OWNED_PAGE_BLOCK_MAX_CHARS,
        registry_max_chars=OWNED_PAGE_REGISTRY_MAX_CHARS,
        heading_max_chars=OWNED_PAGE_HEADING_MAX_CHARS,
        max_blocks_per_section=OWNED_PAGE_MAX_BLOCKS_PER_SECTION,
        section_max_chars=OWNED_PAGE_SECTION_MAX_CHARS,
    ),
})


def get_owned_page_mapping_policy(version: str) -> OwnedPageMappingPolicy:
    normalized_version = str(version or "").strip()
    policy = _OWNED_PAGE_MAPPING_POLICIES.get(normalized_version)
    if policy is None:
        raise PageQualityConfigurationError(
            f'Owned-page mapping version "{normalized_version or "<missing>"}" is unavailable.'
        )
    return policy


def _mapping_policy_for_registry(
    registry: dict | list | None,
) -> OwnedPageMappingPolicy:
    version = (
        registry.get("version")
        if isinstance(registry, dict)
        else OWNED_PAGE_MAPPING_VERSION
    )
    return get_owned_page_mapping_policy(str(version or ""))


def _normalise_heading(value: str, policy: OwnedPageMappingPolicy) -> str:
    return re.sub(r"\s+", " ", value).strip()[:policy.heading_max_chars]


def _content_hash(heading_level: str, heading: str, excerpt: str) -> str:
    payload = f"{heading_level}\n{heading}\n{excerpt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _split_exact_chunks(
    value: str,
    policy: OwnedPageMappingPolicy,
) -> list[str]:
    """Split a source section into bounded, source-exact paragraph chunks."""
    chunks = []
    for paragraph in re.split(r"\n[ \t]*\n+", value):
        remaining = paragraph.strip()
        while remaining:
            if len(remaining) <= policy.block_max_chars:
                chunks.append(remaining)
                break

            cut = remaining.rfind("\n", 0, policy.block_max_chars + 1)
            if cut < policy.block_max_chars // 2:
                cut = remaining.rfind(" ", 0, policy.block_max_chars + 1)
            if cut < policy.block_max_chars // 2:
                cut = policy.block_max_chars

            excerpt = remaining[:cut].rstrip()
            if excerpt:
                chunks.append(excerpt)
            remaining = remaining[cut:].lstrip()
    return chunks


def _markdown_sections(
    markdown: str,
    policy: OwnedPageMappingPolicy,
) -> list[tuple[str, str, str]]:
    """Return ``(heading_level, heading, body)`` entries in source order."""
    lines = markdown.split("\n")
    sections = []
    body_lines: list[str] = []
    heading_level = "none"
    heading = ""
    fence_marker = ""

    def flush() -> None:
        nonlocal body_lines
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((heading_level, heading, body))
        body_lines = []

    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group("marks")
            marker_character = marker[0]
            if not fence_marker:
                fence_marker = marker_character
            elif fence_marker == marker_character:
                fence_marker = ""
            body_lines.append(line)
            index += 1
            continue

        if not fence_marker:
            atx_match = _ATX_HEADING_RE.match(line)
            if atx_match:
                flush()
                heading_level = f"h{len(atx_match.group('marks'))}"
                raw_heading = re.sub(
                    r"[ \t]+#+[ \t]*$", "", atx_match.group("heading")
                )
                heading = _normalise_heading(raw_heading, policy)
                index += 1
                continue

            if (
                line.strip()
                and index + 1 < len(lines)
                and (setext_match := _SETEXT_HEADING_RE.match(lines[index + 1]))
            ):
                flush()
                heading_level = "h1" if setext_match.group("marks").startswith("=") else "h2"
                heading = _normalise_heading(line, policy)
                index += 2
                continue

        body_lines.append(line)
        index += 1

    flush()
    return sections


def build_owned_page_registry(
    markdown: str | None,
    mapping_version: str = OWNED_PAGE_MAPPING_VERSION,
) -> dict:
    """Build a small server-owned registry from already-retained Markdown.

    IDs are stable for an identical capture because they follow source order.
    Excerpts remain exact substrings of the newline-normalised capture.
    """
    policy = get_owned_page_mapping_policy(mapping_version)
    source = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    source_char_count = len(source)
    source_was_truncated = source_char_count > policy.source_max_chars
    retained_source = source[:policy.source_max_chars]

    candidates: list[tuple[str, str, str]] = []
    for heading_level, heading, body in _markdown_sections(retained_source, policy):
        candidates.extend(
            (heading_level, heading, excerpt)
            for excerpt in _split_exact_chunks(body, policy)
            if excerpt
        )

    blocks = []
    retained_char_count = 0
    registry_was_truncated = False

    for heading_level, heading, candidate_excerpt in candidates:
        if len(blocks) >= policy.max_blocks:
            registry_was_truncated = True
            break

        remaining_chars = policy.registry_max_chars - retained_char_count
        if remaining_chars <= 0:
            registry_was_truncated = True
            break

        excerpt = candidate_excerpt
        if len(excerpt) > remaining_chars:
            excerpt = excerpt[:remaining_chars].rstrip()
            registry_was_truncated = True
        if not excerpt:
            registry_was_truncated = True
            break

        block_id = f"O{len(blocks) + 1}"
        block = {
            "id": block_id,
            "order": len(blocks) + 1,
            "heading_level": heading_level,
            "heading": heading,
            "excerpt": excerpt,
            "content_hash": _content_hash(heading_level, heading, excerpt),
        }
        blocks.append(block)
        retained_char_count += len(excerpt)

        if excerpt != candidate_excerpt:
            break

    if len(blocks) < len(candidates):
        registry_was_truncated = True

    return {
        "version": policy.version,
        "blocks": blocks,
        "source_char_count": source_char_count,
        "retained_char_count": retained_char_count,
        "source_truncated": source_was_truncated,
        "registry_truncated": registry_was_truncated,
        "truncated": source_was_truncated or registry_was_truncated,
    }


def _registry_blocks(registry: dict | list | None) -> list:
    if isinstance(registry, dict):
        blocks = registry.get("blocks")
    else:
        blocks = registry
    return blocks if isinstance(blocks, list) else []


def _safe_registry_lookup(registry: dict | list | None) -> dict[str, dict]:
    policy = _mapping_policy_for_registry(registry)
    lookup = {}
    for block in _registry_blocks(registry):
        if not isinstance(block, dict):
            continue

        block_id = block.get("id")
        order = block.get("order")
        heading_level = block.get("heading_level")
        heading = block.get("heading")
        excerpt = block.get("excerpt")
        content_hash = block.get("content_hash")

        if (
            not isinstance(block_id, str)
            or not _OWNED_BLOCK_ID_RE.fullmatch(block_id)
            or block_id in lookup
            or not isinstance(order, int)
            or order < 1
            or heading_level not in _VALID_HEADING_LEVELS
            or not isinstance(heading, str)
            or len(heading) > policy.heading_max_chars
            or not isinstance(excerpt, str)
            or not excerpt
            or len(excerpt) > policy.block_max_chars
            or content_hash != _content_hash(heading_level, heading, excerpt)
        ):
            continue

        lookup[block_id] = {
            "id": block_id,
            "order": order,
            "heading_level": heading_level,
            "heading": heading,
            "excerpt": excerpt,
            "content_hash": content_hash,
        }
    return lookup


def _source_manifest_registry_blocks(
    registry: dict | list | None,
) -> tuple[list, list[dict]]:
    if registry is None:
        return [], []
    if isinstance(registry, dict):
        raw_blocks = registry.get("blocks")
    elif isinstance(registry, list):
        raw_blocks = registry
    else:
        raw_blocks = None

    if not isinstance(raw_blocks, list):
        raise PageQualityConfigurationError(
            "Owned-page registry blocks are malformed for source-asset parsing."
        )

    lookup = _safe_registry_lookup(registry)
    if len(lookup) != len(raw_blocks):
        raise PageQualityConfigurationError(
            "Owned-page registry integrity failed for source-asset parsing."
        )

    blocks = list(lookup.values())
    for expected_order, block in enumerate(blocks, start=1):
        if (
            type(block["order"]) is not int
            or block["order"] != expected_order
            or block["id"] != f"O{expected_order}"
        ):
            raise PageQualityConfigurationError(
                "Owned-page registry topology failed for source-asset parsing."
            )
    return raw_blocks, blocks


def _canonical_hash(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _named_list_items(excerpt: str) -> list[str] | None:
    items = []
    list_indent = None
    for line in excerpt.splitlines():
        match = _SOURCE_ASSET_LIST_ITEM_RE.fullmatch(line)
        if match is None:
            return None
        if list_indent is None:
            list_indent = match.group("indent")
        elif match.group("indent") != list_indent:
            return None
        item = match.group("item").strip()
        if (
            not item
            or _SOURCE_ASSET_RICH_MARKDOWN_RE.search(item)
            or _SOURCE_ASSET_TASK_ITEM_RE.match(item)
            or _SOURCE_ASSET_EMBEDDED_LIST_RE.match(item)
        ):
            return None
        items.append(item)
    return items or None


def _contains_unordered_list_marker(excerpt: str) -> bool:
    return any(
        _SOURCE_ASSET_LIST_ITEM_RE.fullmatch(line) is not None
        for line in excerpt.splitlines()
    )


def _quoted_text(excerpt: str) -> str | None:
    quote_lines = []
    for line in excerpt.splitlines():
        match = _SOURCE_ASSET_QUOTE_LINE_RE.fullmatch(line)
        if match is None:
            return None
        quote_line = match.group("text")
        if quote_line.lstrip().startswith(">"):
            return None
        quote_lines.append(quote_line)
    quote = "\n".join(quote_lines).strip()
    return quote or None


def _testimonial_attribution(excerpt: str) -> str | None:
    attribution = excerpt.strip()
    if (
        not attribution
        or "\n" in attribution
        or len(attribution) > _TESTIMONIAL_ATTRIBUTION_MAX_CHARS
        or len(attribution.split()) > _TESTIMONIAL_ATTRIBUTION_MAX_WORDS
        or attribution.endswith((".", "?", "!"))
        or not any(character.isalpha() for character in attribution)
        or _SOURCE_ASSET_RICH_MARKDOWN_RE.search(attribution)
        or _SOURCE_ASSET_URL_RE.search(attribution)
        or _named_list_items(attribution) is not None
        or _quoted_text(attribution) is not None
    ):
        return None
    return attribution


def _same_source_heading(left: dict, right: dict) -> bool:
    return (
        left["heading_level"] == right["heading_level"]
        and left["heading"] == right["heading"]
    )


def _source_asset(
    *,
    asset_id: str,
    order: int,
    kind: str,
    blocks: list[dict],
    payload: dict,
) -> dict:
    first_block = blocks[0]
    asset = {
        "id": asset_id,
        "order": order,
        "kind": kind,
        "heading_level": first_block["heading_level"],
        "heading": first_block["heading"],
        "source_block_ids": [block["id"] for block in blocks],
        "source_content_hashes": [block["content_hash"] for block in blocks],
        "source_texts": [block["excerpt"] for block in blocks],
        **payload,
    }
    hash_payload = {
        key: value
        for key, value in asset.items()
        if key not in {"id", "order"}
    }
    asset["content_hash"] = _canonical_hash(hash_payload)
    return asset


def build_source_asset_manifest(
    registry: dict | list | None,
    manifest_version: str = SOURCE_ASSET_MANIFEST_VERSION,
) -> dict:
    """Inventory source-exact structures from an owned-page registry.

    The manifest classifies syntax only. It does not make an asset required,
    grant evidence authority, or authorize any factual inference. A
    version-gated generation path may inventory these units for editorial
    preservation, but it must never treat the classification as evidence.
    ``testimonial`` means a structural quote/adjacent-text candidate, and
    ``direct_statement`` is the unclassified source-block fallback; neither
    label establishes truth or claim authority.
    """
    normalized_version = str(manifest_version or "").strip()
    if normalized_version != SOURCE_ASSET_MANIFEST_VERSION:
        raise PageQualityConfigurationError(
            f'Source-asset manifest version "{normalized_version or "<missing>"}" '
            "is unavailable."
        )

    mapping_policy = _mapping_policy_for_registry(registry)
    if mapping_policy.version != OWNED_PAGE_MAPPING_VERSION:
        raise PageQualityConfigurationError(
            f'Owned-page mapping version "{mapping_policy.version}" is not '
            f'compatible with source-asset manifest "{normalized_version}".'
        )
    raw_blocks, blocks = _source_manifest_registry_blocks(registry)
    source_truncated = (
        bool(registry.get("source_truncated"))
        if isinstance(registry, dict)
        else False
    )
    registry_truncated = (
        bool(registry.get("registry_truncated"))
        if isinstance(registry, dict)
        else False
    )
    structured_assets_suppressed = source_truncated or registry_truncated

    assets = []
    consumed_block_ids = []
    ambiguous_list_block_ids = []
    unpaired_quote_block_ids = []
    block_index = 0
    active_heading_key = None
    list_heading_is_tainted = False

    while block_index < len(blocks):
        block = blocks[block_index]
        heading_key = (block["heading_level"], block["heading"])
        if heading_key != active_heading_key:
            active_heading_key = heading_key
            list_heading_is_tainted = False
        quote = _quoted_text(block["excerpt"])
        if quote is not None:
            quote_blocks = [block]
            quote_index = block_index + 1
            while quote_index < len(blocks):
                adjacent_quote_block = blocks[quote_index]
                if (
                    adjacent_quote_block["order"]
                    != quote_blocks[-1]["order"] + 1
                    or not _same_source_heading(
                        block,
                        adjacent_quote_block,
                    )
                    or _quoted_text(adjacent_quote_block["excerpt"]) is None
                ):
                    break
                quote_blocks.append(adjacent_quote_block)
                quote_index += 1

            following_block = (
                blocks[quote_index] if quote_index < len(blocks) else None
            )
            preceding_block = (
                blocks[block_index - 1] if block_index > 0 else None
            )
            preceding_chunk_is_ambiguous = (
                preceding_block is not None
                and _same_source_heading(preceding_block, block)
                and len(preceding_block["excerpt"])
                >= (mapping_policy.block_max_chars * 9) // 10
            )
            attribution = (
                _testimonial_attribution(following_block["excerpt"])
                if (
                    not structured_assets_suppressed
                    and len(quote_blocks) == 1
                    and not preceding_chunk_is_ambiguous
                    and len(block["excerpt"])
                    < (mapping_policy.block_max_chars * 9) // 10
                    and following_block is not None
                    and following_block["order"] == block["order"] + 1
                    and _same_source_heading(block, following_block)
                )
                else None
            )

            if attribution is not None:
                asset_blocks = [block, following_block]
                kind = "testimonial"
                payload = {
                    "quote": quote,
                    "attribution": attribution,
                }
                block_index = quote_index + 1
            else:
                asset_blocks = quote_blocks
                kind = "direct_statement"
                payload = {
                    "statement": "\n\n".join(
                        source_block["excerpt"]
                        for source_block in quote_blocks
                    )
                }
                unpaired_quote_block_ids.extend(
                    source_block["id"] for source_block in quote_blocks
                )
                block_index = quote_index
        else:
            items = _named_list_items(block["excerpt"])
            if items is not None:
                list_blocks = [block]
                combined_items = list(items)
                list_index = block_index + 1
                while list_index < len(blocks):
                    adjacent_list_block = blocks[list_index]
                    adjacent_items = _named_list_items(
                        adjacent_list_block["excerpt"]
                    )
                    if (
                        adjacent_items is None
                        or adjacent_list_block["order"]
                        != list_blocks[-1]["order"] + 1
                        or not _same_source_heading(
                            block,
                            adjacent_list_block,
                        )
                    ):
                        break
                    list_blocks.append(adjacent_list_block)
                    combined_items.extend(adjacent_items)
                    list_index += 1

                preceding_block = (
                    blocks[block_index - 1] if block_index > 0 else None
                )
                following_block = (
                    blocks[list_index] if list_index < len(blocks) else None
                )
                list_is_ambiguous = (
                    structured_assets_suppressed
                    or list_heading_is_tainted
                    or len(list_blocks) > 1
                    or (
                        following_block is not None
                        and _same_source_heading(block, following_block)
                    )
                    or (
                        preceding_block is not None
                        and _same_source_heading(preceding_block, block)
                        and (
                            len(preceding_block["excerpt"])
                            >= (mapping_policy.block_max_chars * 9) // 10
                            or _contains_unordered_list_marker(
                                preceding_block["excerpt"]
                            )
                        )
                    )
                )

                asset_blocks = list_blocks
                if list_is_ambiguous:
                    list_heading_is_tainted = True
                    kind = "direct_statement"
                    payload = {
                        "statement": "\n\n".join(
                            source_block["excerpt"]
                            for source_block in list_blocks
                        )
                    }
                    ambiguous_list_block_ids.extend(
                        source_block["id"] for source_block in list_blocks
                    )
                else:
                    kind = "named_list"
                    payload = {"items": combined_items}
                block_index = list_index
            else:
                asset_blocks = [block]
                kind = "direct_statement"
                payload = {"statement": block["excerpt"]}
                block_index += 1

        asset_order = len(assets) + 1
        assets.append(
            _source_asset(
                asset_id=f"A{asset_order}",
                order=asset_order,
                kind=kind,
                blocks=asset_blocks,
                payload=payload,
            )
        )
        consumed_block_ids.extend(
            source_block["id"] for source_block in asset_blocks
        )

    diagnostics = {
        "registry_block_count": len(raw_blocks),
        "valid_block_count": len(blocks),
        "invalid_block_count": len(raw_blocks) - len(blocks),
        "asset_count": len(assets),
        "consumed_block_count": len(consumed_block_ids),
        "source_truncated": source_truncated,
        "registry_truncated": registry_truncated,
        "structured_assets_suppressed": structured_assets_suppressed,
        "ambiguous_list_block_ids": ambiguous_list_block_ids,
        "unpaired_quote_block_ids": unpaired_quote_block_ids,
    }
    manifest = {
        "version": normalized_version,
        "registry_version": mapping_policy.version,
        "assets": assets,
        "diagnostics": diagnostics,
    }
    manifest["manifest_hash"] = _canonical_hash(manifest)
    return manifest


def _safe_rejected_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:32]


def validate_owned_block_ids(
    raw_ids: object,
    registry: dict | list | None,
    *,
    already_assigned_ids: Iterable[str] | None = None,
) -> tuple[list[str], list[dict]]:
    """Validate a model-returned block list against the server registry.

    The caller can pass IDs accepted for earlier sections to prevent one source
    block from being assigned across unrelated sections. The supplied iterable
    is not mutated.
    """
    if raw_ids is None:
        return [], []
    if not isinstance(raw_ids, (list, tuple)):
        return [], [{"id": None, "reason": "invalid_list"}]

    lookup = _safe_registry_lookup(registry)
    policy = _mapping_policy_for_registry(registry)
    previously_assigned = set(already_assigned_ids or ())
    seen = set()
    valid_ids = []
    rejected = []
    hydrated_chars = 0

    for raw_id in raw_ids:
        candidate = raw_id.strip() if isinstance(raw_id, str) else ""
        rejected_id = _safe_rejected_id(raw_id)

        if not candidate or not _OWNED_BLOCK_ID_RE.fullmatch(candidate):
            rejected.append({"id": rejected_id, "reason": "invalid_id"})
            continue
        if candidate in seen:
            rejected.append({"id": candidate, "reason": "duplicate_id"})
            continue
        seen.add(candidate)
        if candidate not in lookup:
            rejected.append({"id": candidate, "reason": "unknown_id"})
            continue
        if candidate in previously_assigned:
            rejected.append({"id": candidate, "reason": "already_assigned"})
            continue
        if len(valid_ids) >= policy.max_blocks_per_section:
            rejected.append({"id": candidate, "reason": "section_block_limit"})
            continue

        excerpt_chars = len(lookup[candidate]["excerpt"])
        if hydrated_chars + excerpt_chars > policy.section_max_chars:
            rejected.append({"id": candidate, "reason": "section_character_limit"})
            continue

        valid_ids.append(candidate)
        hydrated_chars += excerpt_chars

    return valid_ids, rejected


def hydrate_owned_blocks(
    block_ids: object,
    registry: dict | list | None,
) -> list[dict]:
    """Hydrate only server-registered, bounded excerpts for one section."""
    valid_ids, _ = validate_owned_block_ids(block_ids, registry)
    lookup = _safe_registry_lookup(registry)
    return [dict(lookup[block_id]) for block_id in valid_ids]
