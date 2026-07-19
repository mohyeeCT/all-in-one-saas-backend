import hashlib
from pathlib import Path

import pytest

from utils import owned_page
from utils.owned_page import (
    OWNED_PAGE_BLOCK_MAX_CHARS,
    OWNED_PAGE_MAPPING_VERSION,
    OWNED_PAGE_MAX_BLOCKS,
    OWNED_PAGE_MAX_BLOCKS_PER_SECTION,
    OWNED_PAGE_REGISTRY_MAX_CHARS,
    SOURCE_ASSET_MANIFEST_VERSION,
    build_owned_page_registry,
    build_source_asset_manifest,
    get_owned_page_mapping_policy,
    hydrate_owned_blocks,
    validate_owned_block_ids,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_Q11_SOURCE_SHA256 = (
    "a5f5e9a8b2093903c0c7ef25e150494b1df80ea9de28e9740e5c601e73d13669"
)


def _hash(block):
    value = (
        f"{block['heading_level']}\n{block['heading']}\n{block['excerpt']}"
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def test_registry_uses_stable_ordered_ids_and_exact_bounded_excerpts():
    markdown = """Intro copy that appears before a heading.

# Main page

Main-page opening copy.

## Useful service details

First useful paragraph.

Second useful paragraph with **original Markdown** preserved.
"""

    first = build_owned_page_registry(markdown)
    second = build_owned_page_registry(markdown)

    assert first == second
    assert first["version"] == OWNED_PAGE_MAPPING_VERSION
    assert [block["id"] for block in first["blocks"]] == ["O1", "O2", "O3", "O4"]
    assert [block["order"] for block in first["blocks"]] == [1, 2, 3, 4]
    assert first["blocks"][0]["heading_level"] == "none"
    assert first["blocks"][0]["heading"] == ""
    assert first["blocks"][1]["heading_level"] == "h1"
    assert first["blocks"][1]["heading"] == "Main page"
    assert first["blocks"][2]["heading_level"] == "h2"
    assert first["blocks"][2]["heading"] == "Useful service details"
    assert first["blocks"][3]["excerpt"] == (
        "Second useful paragraph with **original Markdown** preserved."
    )
    assert all(block["excerpt"] in markdown for block in first["blocks"])
    assert all(block["content_hash"] == _hash(block) for block in first["blocks"])
    assert first["truncated"] is False


def test_registry_supports_setext_headings_and_does_not_parse_fenced_headings():
    markdown = """Page title
==========

Opening.

Section title
-------------

Before code.

```markdown
# This is source code, not a new page heading
```
"""

    registry = build_owned_page_registry(markdown)
    blocks = registry["blocks"]

    assert [(block["heading_level"], block["heading"]) for block in blocks] == [
        ("h1", "Page title"),
        ("h2", "Section title"),
        ("h2", "Section title"),
    ]
    assert "# This is source code" in blocks[-1]["excerpt"]


def test_empty_input_has_an_empty_non_truncated_registry():
    assert build_owned_page_registry(None) == {
        "version": OWNED_PAGE_MAPPING_VERSION,
        "blocks": [],
        "source_char_count": 0,
        "retained_char_count": 0,
        "source_truncated": False,
        "registry_truncated": False,
        "truncated": False,
    }


def test_registry_enforces_per_block_total_and_block_count_limits():
    long_paragraphs = [
        f"Paragraph {index} " + ("word " * 220)
        for index in range(OWNED_PAGE_MAX_BLOCKS + 10)
    ]
    registry = build_owned_page_registry("\n\n".join(long_paragraphs))

    assert registry["truncated"] is True
    assert len(registry["blocks"]) <= OWNED_PAGE_MAX_BLOCKS
    assert all(
        0 < len(block["excerpt"]) <= OWNED_PAGE_BLOCK_MAX_CHARS
        for block in registry["blocks"]
    )
    assert registry["retained_char_count"] <= OWNED_PAGE_REGISTRY_MAX_CHARS


def test_validation_rejects_invented_duplicate_malformed_and_reused_ids():
    registry = build_owned_page_registry(
        "First source paragraph.\n\nSecond source paragraph.\n\nThird source paragraph."
    )

    valid_ids, rejected = validate_owned_block_ids(
        ["O1", "O1", "O99", "o2", 2, "O2"],
        registry,
        already_assigned_ids={"O2"},
    )

    assert valid_ids == ["O1"]
    assert rejected == [
        {"id": "O1", "reason": "duplicate_id"},
        {"id": "O99", "reason": "unknown_id"},
        {"id": "o2", "reason": "invalid_id"},
        {"id": None, "reason": "invalid_id"},
        {"id": "O2", "reason": "already_assigned"},
    ]


def test_validation_rejects_assignments_over_the_per_section_limit():
    registry = build_owned_page_registry(
        "\n\n".join(f"Source paragraph {index}." for index in range(8))
    )
    requested = [f"O{index}" for index in range(1, 9)]

    valid_ids, rejected = validate_owned_block_ids(requested, registry)

    assert valid_ids == requested[:OWNED_PAGE_MAX_BLOCKS_PER_SECTION]
    assert all(
        item["reason"] == "section_block_limit"
        for item in rejected
    )


def test_hydration_uses_only_registry_excerpts_and_returns_defensive_copies():
    markdown = "Owned exact wording.\n\nAnother exact source paragraph."
    registry = build_owned_page_registry(markdown)

    hydrated = hydrate_owned_blocks(["O2", "O404", "O1"], registry)

    assert [block["id"] for block in hydrated] == ["O2", "O1"]
    assert [block["excerpt"] for block in hydrated] == [
        "Another exact source paragraph.",
        "Owned exact wording.",
    ]
    assert all(block["excerpt"] in markdown for block in hydrated)

    hydrated[0]["excerpt"] = "forged model text"
    assert registry["blocks"][1]["excerpt"] == "Another exact source paragraph."


def test_hydration_rejects_tampered_or_oversized_persisted_blocks():
    registry = build_owned_page_registry("Trusted exact source.")
    tampered = {
        **registry,
        "blocks": [
            {**registry["blocks"][0], "excerpt": "forged model source"},
            {
                **registry["blocks"][0],
                "id": "O2",
                "order": 2,
                "excerpt": "x" * (OWNED_PAGE_BLOCK_MAX_CHARS + 1),
            },
        ],
    }

    assert hydrate_owned_blocks(["O1", "O2"], tampered) == []


def test_non_list_model_assignment_is_rejected_without_string_iteration():
    registry = build_owned_page_registry("Trusted exact source.")

    assert validate_owned_block_ids("O1", registry) == (
        [],
        [{"id": None, "reason": "invalid_list"}],
    )


def test_mapping_version_dispatch_is_immutable_and_rejects_unknown_versions(
    monkeypatch,
):
    monkeypatch.setattr(owned_page, "OWNED_PAGE_BLOCK_MAX_CHARS", 10)

    registry = build_owned_page_registry("x" * 50)

    assert len(registry["blocks"][0]["excerpt"]) == 50
    assert (
        get_owned_page_mapping_policy(OWNED_PAGE_MAPPING_VERSION).block_max_chars
        > 10
    )
    with pytest.raises(ValueError, match="unavailable"):
        build_owned_page_registry(
            "source",
            mapping_version="missing-owned-page-version",
        )


def test_q11_owned_page_fixture_is_byte_for_byte_frozen():
    fixture_bytes = (_FIXTURE_DIR / "q11_owned_page.md").read_bytes()

    assert hashlib.sha256(fixture_bytes).hexdigest() == _Q11_SOURCE_SHA256


def test_q11_source_asset_manifest_is_complete_exact_and_deterministic():
    source = (_FIXTURE_DIR / "q11_owned_page.md").read_text(encoding="utf-8")
    registry = build_owned_page_registry(source)

    first = build_source_asset_manifest(registry)
    second = build_source_asset_manifest(registry)

    assert first == second
    assert first["version"] == SOURCE_ASSET_MANIFEST_VERSION
    assert first["registry_version"] == OWNED_PAGE_MAPPING_VERSION
    assert (
        first["manifest_hash"]
        == "68b47e49aa1882a873ba2603cea3e90405372510695ecd0e912945fd6303e408"
    )
    assert first["diagnostics"] == {
        "registry_block_count": 15,
        "valid_block_count": 15,
        "invalid_block_count": 0,
        "asset_count": 13,
        "consumed_block_count": 15,
        "source_truncated": False,
        "registry_truncated": False,
        "structured_assets_suppressed": False,
        "ambiguous_list_block_ids": [],
        "unpaired_quote_block_ids": [],
    }
    assert [asset["kind"] for asset in first["assets"]] == [
        "direct_statement",
        "direct_statement",
        "direct_statement",
        "named_list",
        "direct_statement",
        "named_list",
        "named_list",
        "direct_statement",
        "named_list",
        "direct_statement",
        "testimonial",
        "testimonial",
        "named_list",
    ]
    assert [
        block_id
        for asset in first["assets"]
        for block_id in asset["source_block_ids"]
    ] == [f"O{index}" for index in range(1, 16)]
    assert all(
        len(asset["content_hash"]) == 64
        and len(asset["source_block_ids"]) == len(asset["source_content_hashes"])
        and len(asset["source_block_ids"]) == len(asset["source_texts"])
        and not {
            "required",
            "evidence",
            "evidence_eligible",
            "claim_authority",
        }.intersection(asset)
        for asset in first["assets"]
    )

    named_lists = {
        tuple(asset["source_block_ids"]): asset["items"]
        for asset in first["assets"]
        if asset["kind"] == "named_list"
    }
    assert named_lists == {
        ("O4",): [
            "Fabrics",
            "Custom Curtains",
            "Digital Printing",
            "Tape",
            "Curtain Track & Equipment",
            "Rentals",
        ],
        ("O6",): [
            "Theatrical Curtains",
            "Scenic Treatments",
            "Digital Printing, Projection & Event Fabrics",
            "View Portfolio",
        ],
        ("O7",): [
            "How to Specify a Stage Curtain to Obtain a Cost Estimate",
            "How to Choose a Velour Fabric for Your Main Stage Curtain",
            "Curtain Design, Specification & Build",
            "View blog",
        ],
        ("O9",): ["Commando Cloth & Duvetyn"],
        ("O15",): [
            "Contact Us",
            "Custom Curtain Quote Request",
            "Digital Printing Quote Request",
            "Fabric Finder",
            "Project Portfolio",
        ],
    }

    testimonials = [
        (
            asset["source_block_ids"],
            asset["quote"],
            asset["attribution"],
        )
        for asset in first["assets"]
        if asset["kind"] == "testimonial"
    ]
    assert testimonials == [
        (
            ["O11", "O12"],
            (
                "I have been working with Rose Brand for over 30 years and they "
                "remain my first call for theatrical fabrics and specialty scenic "
                "supplies. Rose Brand is a first-class operation with superb "
                "customer service."
            ),
            "John Murray",
        ),
        (
            ["O13", "O14"],
            (
                "Rose Brand has always supported us on our events. Lisa and the "
                "rest of the staff have great knowledge about their products and "
                "are always willing to help. Great products and Great people!"
            ),
            "Joe Russo",
        ),
    ]


def test_source_asset_manifest_uses_only_structural_syntax():
    registry = build_owned_page_registry(
        """## Reviews

> A source-exact quote.

Alex Example

> An unpaired quote.

## Mixed block

- One item
Continuation text is not a pure named list.
"""
    )

    manifest = build_source_asset_manifest(registry)

    assert [
        (asset["kind"], asset["source_block_ids"])
        for asset in manifest["assets"]
    ] == [
        ("testimonial", ["O1", "O2"]),
        ("direct_statement", ["O3"]),
        ("direct_statement", ["O4"]),
    ]
    assert manifest["diagnostics"]["unpaired_quote_block_ids"] == ["O3"]
    assert manifest["diagnostics"]["consumed_block_count"] == 4


def test_source_asset_manifest_fails_closed_for_invalid_blocks():
    registry = build_owned_page_registry("- First\n- Second\n\nExact statement.")
    tampered_registry = {
        **registry,
        "blocks": [
            registry["blocks"][0],
            {**registry["blocks"][1], "excerpt": "forged text"},
            registry["blocks"][1],
        ],
    }

    with pytest.raises(ValueError, match="integrity failed"):
        build_source_asset_manifest(tampered_registry)


def test_source_asset_manifest_has_no_list_item_cap():
    registry = build_owned_page_registry(
        "\n".join(f"- Exact label {index}" for index in range(1, 14))
    )

    manifest = build_source_asset_manifest(registry)

    assert manifest["assets"][0]["kind"] == "named_list"
    assert manifest["assets"][0]["items"] == [
        f"Exact label {index}" for index in range(1, 14)
    ]
    assert manifest["diagnostics"]["source_truncated"] is False
    assert manifest["diagnostics"]["registry_truncated"] is False


def test_source_asset_manifest_propagates_real_registry_truncation():
    registry = build_owned_page_registry(
        "\n\n".join(
            f"- Exact source label {index}"
            for index in range(OWNED_PAGE_MAX_BLOCKS + 5)
        )
    )

    manifest = build_source_asset_manifest(registry)

    assert registry["registry_truncated"] is True
    assert manifest["diagnostics"]["registry_truncated"] is True
    assert manifest["diagnostics"]["structured_assets_suppressed"] is True
    assert manifest["diagnostics"]["valid_block_count"] == len(registry["blocks"])
    assert manifest["diagnostics"]["consumed_block_count"] == len(
        registry["blocks"]
    )
    assert all(
        asset["kind"] == "direct_statement"
        for asset in manifest["assets"]
    )


def test_source_asset_manifest_rejects_unknown_version_and_nested_syntax():
    registry = build_owned_page_registry(
        """- Parent
  - Nested child

>> Nested quote

Alex Example
"""
    )

    manifest = build_source_asset_manifest(registry)

    assert [asset["kind"] for asset in manifest["assets"]] == [
        "direct_statement",
        "direct_statement",
        "direct_statement",
    ]
    assert manifest["diagnostics"]["unpaired_quote_block_ids"] == []
    with pytest.raises(ValueError, match="unavailable"):
        build_source_asset_manifest(
            registry,
            manifest_version="missing-source-asset-version",
        )


def test_source_asset_manifest_does_not_promote_split_list_blocks():
    labels = [
        f"Exact source label {index} with retained wording and punctuation"
        for index in range(1, 41)
    ]
    registry = build_owned_page_registry(
        "\n".join(f"- {label}" for label in labels)
    )

    manifest = build_source_asset_manifest(registry)

    assert len(registry["blocks"]) > 1
    assert registry["truncated"] is False
    assert len(manifest["assets"]) == 1
    assert manifest["assets"][0]["kind"] == "direct_statement"
    assert manifest["assets"][0]["source_block_ids"] == [
        block["id"] for block in registry["blocks"]
    ]
    assert manifest["diagnostics"]["ambiguous_list_block_ids"] == [
        block["id"] for block in registry["blocks"]
    ]


def test_source_asset_manifest_does_not_promote_ambiguous_or_rich_list_syntax():
    long_single_item = "- " + ("source wording " * 70)
    long_registry = build_owned_page_registry(long_single_item)
    rich_registry = build_owned_page_registry(
        "- [Contact Us](/contact)\n- **Fabric Finder**\n- [ ] Task"
    )

    long_manifest = build_source_asset_manifest(long_registry)
    rich_manifest = build_source_asset_manifest(rich_registry)

    assert len(long_registry["blocks"]) > 1
    assert not any(
        asset["kind"] == "named_list" for asset in long_manifest["assets"]
    )
    assert long_manifest["diagnostics"]["ambiguous_list_block_ids"] == ["O1"]
    assert rich_manifest["assets"][0]["kind"] == "direct_statement"


def test_source_asset_manifest_does_not_pair_only_the_tail_of_a_split_quote():
    quote_lines = [
        "> " + (f"Exact quoted line {index} " * 5)
        for index in range(1, 12)
    ]
    registry = build_owned_page_registry(
        "\n".join(quote_lines) + "\n\nAlex Example"
    )

    manifest = build_source_asset_manifest(registry)

    quote_block_ids = [
        block["id"]
        for block in registry["blocks"]
        if block["excerpt"].startswith(">")
    ]
    assert len(quote_block_ids) > 1
    assert not any(
        asset["kind"] == "testimonial" for asset in manifest["assets"]
    )
    assert manifest["diagnostics"]["unpaired_quote_block_ids"] == quote_block_ids


def test_source_asset_manifest_does_not_promote_plain_tail_after_rich_list_chunk():
    source_lines = ["- [Linked label](/linked)"] + [
        f"- Exact plain label {index} with enough retained source wording"
        for index in range(1, 36)
    ]
    registry = build_owned_page_registry("\n".join(source_lines))

    manifest = build_source_asset_manifest(registry)

    assert len(registry["blocks"]) > 1
    assert registry["truncated"] is False
    assert not any(
        asset["kind"] == "named_list" for asset in manifest["assets"]
    )
    assert manifest["diagnostics"]["ambiguous_list_block_ids"]


def test_source_asset_manifest_does_not_promote_list_after_rich_list_block():
    registry = build_owned_page_registry(
        "- [Linked label](/linked)\n\n- Exact plain label"
    )

    manifest = build_source_asset_manifest(registry)

    assert not any(
        asset["kind"] == "named_list" for asset in manifest["assets"]
    )
    assert manifest["diagnostics"]["ambiguous_list_block_ids"] == ["O2"]


def test_source_asset_manifest_taints_later_list_fragments_under_same_heading():
    source = (
        "- "
        + ("firstword " * 145).strip()
        + "\n- "
        + ("Second label wording " * 20).strip()
    )
    registry = build_owned_page_registry(source)

    manifest = build_source_asset_manifest(registry)

    assert [block["excerpt"].startswith("-") for block in registry["blocks"]] == [
        True,
        False,
        True,
    ]
    assert not any(
        asset["kind"] == "named_list" for asset in manifest["assets"]
    )
    assert manifest["diagnostics"]["ambiguous_list_block_ids"] == ["O1", "O3"]


def test_source_asset_manifest_does_not_treat_long_quote_tail_as_attribution():
    registry = build_owned_page_registry(
        "> " + ("quotedword " * 80).strip() + "\n\nAlex Example"
    )

    manifest = build_source_asset_manifest(registry)

    assert len(registry["blocks"][0]["excerpt"]) > 720
    assert not any(
        asset["kind"] == "testimonial" for asset in manifest["assets"]
    )
    assert manifest["diagnostics"]["unpaired_quote_block_ids"] == ["O1"]


@pytest.mark.parametrize(
    "attribution",
    [
        "2026",
        "https://example.com",
        "[Read more](/reviews)",
        "<strong>Customer</strong>",
    ],
)
def test_source_asset_manifest_rejects_non_plain_attribution_candidates(
    attribution,
):
    registry = build_owned_page_registry(
        f"> Exact source quote.\n\n{attribution}"
    )

    manifest = build_source_asset_manifest(registry)

    assert [asset["kind"] for asset in manifest["assets"]] == [
        "direct_statement",
        "direct_statement",
    ]
    assert manifest["diagnostics"]["unpaired_quote_block_ids"] == ["O1"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda blocks: [{**blocks[0], "id": "O2"}],
        lambda blocks: [{**blocks[0], "order": 2}],
        lambda blocks: [{**blocks[0], "order": True}],
    ],
)
def test_source_asset_manifest_rejects_noncanonical_id_and_order(mutate):
    registry = build_owned_page_registry("Exact source.")
    registry["blocks"] = mutate(registry["blocks"])

    with pytest.raises(ValueError, match="source-asset parsing"):
        build_source_asset_manifest(registry)


def test_source_asset_manifest_rejects_reordered_or_malformed_registry():
    registry = build_owned_page_registry("First source.\n\nSecond source.")
    reordered = {
        **registry,
        "blocks": list(reversed(registry["blocks"])),
    }

    with pytest.raises(ValueError, match="topology failed"):
        build_source_asset_manifest(reordered)
    with pytest.raises(ValueError, match="blocks are malformed"):
        build_source_asset_manifest(
            {
                **registry,
                "blocks": "not-a-list",
            }
        )
