from copy import deepcopy
import io
from pathlib import Path

import pytest
from docx import Document

from routers import all_in_one
from utils import copy_gen
from utils.owned_page import (
    SOURCE_ASSET_MANIFEST_VERSION,
    SOURCE_BLOCK_PLAN_VERSION,
    build_owned_page_registry,
    build_source_asset_manifest,
)
from utils.page_quality import (
    CLAIM_BOUND_RENDERER_VERSION,
    PAGE_QUALITY_POLICY_VERSION,
    get_page_quality_policy,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "q11_owned_page.md"
_PAGE_POLICY = get_page_quality_policy(PAGE_QUALITY_POLICY_VERSION)


def _section(name: str, *, heading_level: str = "h2") -> dict:
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "purpose": f"Handle the {name} responsibility.",
        "heading_level": heading_level,
        "word_count": [100, 160],
        "keyword_slot": "none",
        "prompt_rules": "Write directly.",
        "depth_policy": "claim_sensitive",
        "adaptive_mode": "full",
    }


def _strategy(manifest: dict, assignments: dict[str, list[str]]) -> dict:
    assets_by_id = {asset["id"]: asset for asset in manifest["assets"]}
    assigned_ids = [
        asset_id
        for asset_ids in assignments.values()
        for asset_id in asset_ids
    ]
    return {
        "claim_bound_renderer_version": CLAIM_BOUND_RENDERER_VERSION,
        "source_block_plan_version": SOURCE_BLOCK_PLAN_VERSION,
        "source_asset_manifest_version": SOURCE_ASSET_MANIFEST_VERSION,
        "source_asset_mapping_diagnostics": {
            "active": True,
            "assigned_asset_ids": assigned_ids,
            "unassigned_asset_ids": [
                asset_id
                for asset_id in assets_by_id
                if asset_id not in assigned_ids
            ],
        },
        "section_guidance": [
            {
                "section": section_name,
                "source_asset_ids": list(asset_ids),
                "source_assets": [
                    deepcopy(assets_by_id[asset_id])
                    for asset_id in asset_ids
                ],
                "proof_facts": [],
            }
            for section_name, asset_ids in assignments.items()
        ],
    }


def _generate(
    monkeypatch,
    *,
    manifest: dict,
    strategy: dict,
    sections: list[dict],
    forbidden_phrases: str = "",
    h1: str = "Theatrical Fabrics",
    provider_name: str = "ClaimBoundPageTest",
):
    provider_calls = []

    def provider(*_args, **_kwargs):
        provider_calls.append(True)
        return "Unsupported same-team, no-outsourcing, any-budget claim."

    monkeypatch.setitem(copy_gen.PROVIDER_FN, provider_name, provider)
    keyword_assignment = {
        section["name"]: {"primary": "theatrical fabrics"}
        for section in sections
    }
    original_assignment = deepcopy(keyword_assignment)
    result = copy_gen.generate_page(
        template={"sections": sections},
        keyword_assignment=keyword_assignment,
        lsi_keywords={},
        business_type="b2b",
        brand_name="Rose Brand",
        h1=h1,
        page_type="homepage",
        paa_questions=[],
        ai_overview="",
        competitor_section_map={},
        client_brief="",
        client_existing_content="",
        provider=provider_name,
        api_key="test-key",
        model="fixed-test-model",
        forbidden_phrases=forbidden_phrases,
        strategy_brief=strategy,
        page_quality_policy=_PAGE_POLICY,
        page_copy_correction_enabled=True,
        claim_bound_renderer_version=CLAIM_BOUND_RENDERER_VERSION,
        source_block_plan_version=SOURCE_BLOCK_PLAN_VERSION,
        source_asset_manifest=manifest,
    )
    assert keyword_assignment == original_assignment
    return result, provider_calls


def test_q11_renders_every_exact_source_unit_without_section_provider_calls(
    monkeypatch,
):
    source = _FIXTURE.read_text(encoding="utf-8")
    registry = build_owned_page_registry(source)
    manifest = build_source_asset_manifest(registry)
    sections = [
        _section("hero", heading_level="h1"),
        {**_section("trust_bar", heading_level="none"), "depth_policy": "proof_only"},
        _section("services_overview"),
        _section("differentiators"),
        _section("social_proof"),
        _section("cta_close"),
    ]
    assignments = {
        "hero": ["A1"],
        "trust_bar": ["A2"],
        "services_overview": ["A3", "A4", "A8"],
        "differentiators": ["A5", "A6", "A9"],
        "social_proof": ["A10", "A11", "A12"],
        "cta_close": ["A13", "A7"],
    }
    strategy = _strategy(manifest, assignments)

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=sections,
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert "same-team" not in result["_full_page"]
    plan = result["_source_block_plan"]
    assert plan["valid"] is True
    assert plan["fallback_used"] is False
    assert plan["diagnostics"]["registry_block_count"] == 15
    assert plan["diagnostics"]["accounted_block_count"] == 15
    assert plan["diagnostics"]["unaccounted_block_ids"] == []
    assert copy_gen.page_plan_diagnostics(
        strategy,
        sections,
    )["mapped_block_count"] == 15

    assets_by_id = {asset["id"]: asset for asset in manifest["assets"]}
    for section_name, asset_ids in assignments.items():
        for asset_id in asset_ids:
            exact_text = copy_gen._claim_bound_asset_text(assets_by_id[asset_id])
            assert result[section_name].count(exact_text) == 1

    assert result["cta_close"].index(
        copy_gen._claim_bound_asset_text(assets_by_id["A7"])
    ) < result["cta_close"].index(
        copy_gen._claim_bound_asset_text(assets_by_id["A13"])
    )


def test_incomplete_assignment_is_blocked_without_first_section_dump(monkeypatch):
    source = (
        "# Existing H1\n\nExact opening statement.\n\n"
        "## Services\n\nExact service statement.\n\n"
        "## Proof\n\n> Exact customer quote.\n\nAlex Example"
    )
    registry = build_owned_page_registry(source)
    manifest = build_source_asset_manifest(registry)
    strategy = _strategy(
        manifest,
        {"hero": [manifest["assets"][-1]["id"]]},
    )
    sections = [_section("hero", heading_level="h1"), _section("proof")]

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=sections,
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert "hero" not in result
    assert "source_asset_assignment_incomplete" in result[
        "_quality_block_reasons"
    ]

    plan = result["_source_block_plan"]
    assert plan["fallback_used"] is True
    assert plan["valid"] is False
    assert plan["diagnostics"]["unassigned_asset_ids"]
    assert all(
        operation["target_section"] == ""
        for operation in plan["operations"]
        if operation["asset_id"] in plan["diagnostics"]["unassigned_asset_ids"]
    )
    assert not any(
        operation["reason_code"] == "source_order_fallback"
        for operation in plan["operations"]
    )


def test_forbidden_direct_statement_is_an_auditable_drop(monkeypatch):
    source = (
        "## About\n\nWe support any budget scale.\n\n"
        "Exact safe source statement."
    )
    registry = build_owned_page_registry(source)
    manifest = build_source_asset_manifest(registry)
    asset_ids = [asset["id"] for asset in manifest["assets"]]
    strategy = _strategy(manifest, {"about": asset_ids})

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about")],
        forbidden_phrases="any budget scale",
    )

    assert provider_calls == []
    assert "any budget scale" not in result["_full_page"].casefold()
    assert "Exact safe source statement." in result["_full_page"]
    dropped = [
        operation
        for operation in result["_source_block_plan"]["operations"]
        if operation["content_action"] == "drop"
    ]
    assert dropped[0]["reason_code"] == "forbidden_phrase_conflict"
    assert result["_source_block_plan"]["diagnostics"]["unaccounted_block_ids"] == []


def test_plan_hash_ignores_model_asset_order():
    source = "## About\n\nFirst exact statement.\n\nSecond exact statement."
    registry = build_owned_page_registry(source)
    manifest = build_source_asset_manifest(registry)
    asset_ids = [asset["id"] for asset in manifest["assets"]]
    first = _strategy(manifest, {"about": asset_ids})
    second = _strategy(manifest, {"about": list(reversed(asset_ids))})
    template = {"sections": [_section("about")]}

    first_plan = copy_gen._claim_bound_source_plan(first, manifest, template)
    second_plan = copy_gen._claim_bound_source_plan(second, manifest, template)

    assert first_plan["valid"] is True
    assert first_plan["plan_hash"] == second_plan["plan_hash"]
    assert [operation["asset_id"] for operation in first_plan["operations"]] == asset_ids


def test_invalid_manifest_is_withheld_without_provider_retry(monkeypatch):
    source = "## About\n\nExact source statement."
    registry = build_owned_page_registry(source)
    manifest = build_source_asset_manifest(registry)
    manifest["diagnostics"]["source_truncated"] = True
    strategy = _strategy(manifest, {"about": ["A1"]})

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert "source_asset_manifest_truncated" in result["_quality_block_reasons"]


def test_quality_contract_failure_has_error_severity():
    flags = all_in_one._collect_qa_flags(
        gen_meta=False,
        gen_faqs=False,
        gen_page_copy=True,
        generated_title="",
        generated_description="",
        optimised_h1="",
        input_h1="",
        primary_keyword="production supplies",
        faq_items=[],
        section_results={},
        forbidden_phrases=[],
        template={"sections": [_section("hero", heading_level="h1")]},
        strategy_brief={
            "claim_bound_renderer_version": CLAIM_BOUND_RENDERER_VERSION,
            "source_block_plan_version": SOURCE_BLOCK_PLAN_VERSION,
        },
        page_quality_policy_version=PAGE_QUALITY_POLICY_VERSION,
        page_copy_correction_enabled=True,
        page_copy_quality_block_reasons=["source_asset_manifest_truncated"],
    )

    blocked = next(
        flag
        for flag in flags
        if flag["code"] == "page_copy_quality_blocked"
    )
    assert blocked["severity"] == "error"
    assert all_in_one._qa_status(flags) == "error"


def test_forbidden_source_heading_and_proof_excerpt_are_withheld(monkeypatch):
    registry = build_owned_page_registry(
        "## Any Budget Offers\n\nExact safe source statement."
    )
    manifest = build_source_asset_manifest(registry)
    strategy = _strategy(manifest, {"about": ["A1"]})
    strategy["section_guidance"][0]["proof_facts"] = [{
        "id": "F1",
        "fact": "Unsupported model restatement.",
        "source": "current_page",
        "source_excerpt": "Any budget guarantee.",
    }]
    strategy["verified_facts"] = deepcopy(
        strategy["section_guidance"][0]["proof_facts"]
    )

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about")],
        forbidden_phrases="any budget",
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert "any budget" not in result["_full_page"].casefold()
    assert "Exact safe source statement." in result["_full_page"]
    operation = result["_source_block_plan"]["operations"][0]
    assert operation["heading_action"] == "drop"
    assert operation["heading_reason_code"] == "forbidden_phrase_conflict"


def test_forbidden_phrase_split_across_source_units_blocks_delivery(monkeypatch):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("any\n\nbudget scale.")
    )
    strategy = _strategy(
        manifest,
        {"about": [asset["id"] for asset in manifest["assets"]]},
    )

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about")],
        forbidden_phrases="any budget",
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert "rendered_forbidden_phrase_conflict" in result[
        "_quality_block_reasons"
    ]


def test_safe_source_h1_wins_and_multiline_h1_cannot_leak_body_prose(monkeypatch):
    source_manifest = build_source_asset_manifest(
        build_owned_page_registry(
            "# Existing — Source H1\n\nExact source statement."
        )
    )
    assert copy_gen._claim_bound_canonical_h1(
        source_manifest,
        "Award-Winning Target\n\nGuaranteed outcomes.",
        "neutral target",
    ) == "Existing — Source H1"

    no_h1_manifest = build_source_asset_manifest(
        build_owned_page_registry("Exact source statement.")
    )
    strategy = _strategy(no_h1_manifest, {"hero": ["A1"]})
    result, provider_calls = _generate(
        monkeypatch,
        manifest=no_h1_manifest,
        strategy=strategy,
        sections=[_section("hero", heading_level="h1")],
        h1="Award-Winning Target\n\nGuaranteed outcomes.",
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert "no_safe_canonical_h1" in result["_quality_block_reasons"]


def test_empty_source_manifest_can_render_safe_verified_excerpt(monkeypatch):
    manifest = build_source_asset_manifest(build_owned_page_registry(""))
    strategy = {
        "verified_facts": [{
            "id": "F1",
            "fact": "Model phrasing must not render.",
            "source": "brand_profile",
            "source_excerpt": "Exact approved brand fact.",
        }],
        "section_guidance": [{
            "section": "hero",
            "proof_facts": [{
                "id": "F1",
                "fact": "Model phrasing must not render.",
                "source": "brand_profile",
                "source_excerpt": "Exact approved brand fact.",
            }],
        }],
    }

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("hero", heading_level="h1")],
        h1="Neutral Service Heading",
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert result["_full_page"] == (
        "# Neutral Service Heading\n\nExact approved brand fact."
    )
    assert "Model phrasing" not in result["_full_page"]
    assert strategy["claim_bound_renderer_version"] == CLAIM_BOUND_RENDERER_VERSION
    assert strategy["source_block_plan_version"] == SOURCE_BLOCK_PLAN_VERSION


def test_strict_strategy_keeps_only_byte_exact_evidence_excerpts():
    exact_excerpt = "Approved — exact source wording."
    data = {
        "verified_facts": [{
            "id": "F1",
            "fact": "Approved source wording.",
            "source": "brand_profile",
            "source_excerpt": exact_excerpt,
        }],
        "proof_fact_ids": ["F1"],
        "section_guidance": [{
            "section": "proof",
            "responsibility": "Present the approved fact.",
            "proof_fact_ids": ["F1"],
        }],
    }
    brief = copy_gen._normalise_strategy_brief(
        data,
        evidence_sources={
            "current_page": "",
            "client_brief": "",
            "brand_profile": exact_excerpt,
        },
        template_sections=[_section("proof")],
        page_copy_correction_enabled=True,
        evidence_locked_reconstruction=True,
    )

    assert brief["verified_facts"][0]["source_excerpt"] == exact_excerpt
    assert brief["section_guidance"][0]["proof_facts"][0][
        "source_excerpt"
    ] == exact_excerpt

    data["verified_facts"][0]["source_excerpt"] = (
        "Approved, exact source wording."
    )
    rejected = copy_gen._normalise_strategy_brief(
        data,
        evidence_sources={
            "current_page": "",
            "client_brief": "",
            "brand_profile": exact_excerpt,
        },
        template_sections=[_section("proof")],
        page_copy_correction_enabled=True,
        evidence_locked_reconstruction=True,
    )
    assert "verified_facts" not in rejected


def test_overlapping_proof_excerpts_render_only_the_complete_unit(monkeypatch):
    manifest = build_source_asset_manifest(build_owned_page_registry(""))
    strategy = {
        "verified_facts": [
            {
                "id": "F1",
                "fact": "First model phrasing.",
                "source": "client_brief",
                "source_excerpt": "service with dedicated support",
            },
            {
                "id": "F2",
                "fact": "Second model phrasing.",
                "source": "client_brief",
                "source_excerpt": (
                    "Exact supported service with dedicated support."
                ),
            },
        ],
        "section_guidance": [{
            "section": "proof",
            "proof_facts": [
                {
                    "id": "F1",
                    "fact": "First model phrasing.",
                    "source": "client_brief",
                    "source_excerpt": "service with dedicated support",
                },
                {
                    "id": "F2",
                    "fact": "Second model phrasing.",
                    "source": "client_brief",
                    "source_excerpt": (
                        "Exact supported service with dedicated support."
                    ),
                },
            ],
        }],
    }

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("proof")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert result["_full_page"].count("dedicated support") == 1
    assert "Exact supported service with dedicated support." in result["_full_page"]


def test_proof_containing_a_preserved_source_unit_is_not_repeated(monkeypatch):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("Service with dedicated support.")
    )
    proof = {
        "id": "F1",
        "fact": "Model phrasing must not render.",
        "source": "client_brief",
        "source_excerpt": "Exact supported service with dedicated support.",
    }
    strategy = _strategy(manifest, {"proof": ["A1"]})
    strategy["verified_facts"] = [deepcopy(proof)]
    strategy["section_guidance"][0]["proof_facts"] = [deepcopy(proof)]

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("proof")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert result["_full_page"].count("dedicated support") == 1
    assert "Exact supported service with dedicated support." not in result[
        "_full_page"
    ]


def test_case_insensitive_section_assignment_materializes_once(monkeypatch):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("Exact source statement.")
    )
    strategy = _strategy(manifest, {"hero": ["A1"]})
    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("Hero", heading_level="h1")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is False
    assert result["Hero"].count("Exact source statement.") == 1
    assert result["_source_block_plan"]["operations"][0]["target_section"] == "Hero"


@pytest.mark.parametrize(
    "mutation, expected_reason",
    [
        (
            lambda manifest: manifest.update({"registry_version": "unknown-registry"}),
            "source_asset_registry_version_mismatch",
        ),
        (
            lambda manifest: manifest["assets"][0].update({"order": "bad"}),
            "source_asset_schema_invalid",
        ),
        (
            lambda manifest: manifest["assets"][0].update({
                "source_block_ids": 1,
            }),
            "source_asset_schema_invalid",
        ),
        (
            lambda manifest: manifest["assets"][0].update({
                "kind": "unknown_kind",
            }),
            "source_asset_schema_invalid",
        ),
        (
            lambda manifest: manifest["assets"][0].update({
                "statement": "Tampered statement.",
            }),
            "source_asset_schema_invalid",
        ),
    ],
)
def test_self_consistent_malformed_manifest_is_quality_blocked(
    monkeypatch,
    mutation,
    expected_reason,
):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("Exact source statement.")
    )
    mutation(manifest)
    manifest["manifest_hash"] = copy_gen._canonical_contract_hash({
        key: value
        for key, value in manifest.items()
        if key != "manifest_hash"
    })
    strategy = {"section_guidance": []}

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert expected_reason in result["_quality_block_reasons"]


def test_duplicate_template_section_names_are_quality_blocked(monkeypatch):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("Exact source statement.")
    )
    strategy = _strategy(manifest, {"about": ["A1"]})

    result, provider_calls = _generate(
        monkeypatch,
        manifest=manifest,
        strategy=strategy,
        sections=[_section("about"), _section("ABOUT")],
    )

    assert provider_calls == []
    assert result["_quality_blocked"] is True
    assert result["_full_page"] == ""
    assert "template_section_topology_invalid" in result["_quality_block_reasons"]


def test_strict_renderer_has_no_page_provider_dependency(monkeypatch):
    manifest = build_source_asset_manifest(
        build_owned_page_registry("Exact source statement.")
    )
    strategy = _strategy(manifest, {"about": ["A1"]})

    result = copy_gen.generate_page(
        template={"sections": [_section("about")]},
        keyword_assignment={"about": {"primary": "target"}},
        lsi_keywords={},
        business_type="b2b",
        brand_name="Example",
        h1="Neutral Heading",
        page_type="service",
        paa_questions=[],
        ai_overview="",
        competitor_section_map={},
        client_brief="",
        client_existing_content="",
        provider="Removed Provider",
        api_key="",
        strategy_brief=strategy,
        claim_bound_renderer_version=CLAIM_BOUND_RENDERER_VERSION,
        source_block_plan_version=SOURCE_BLOCK_PLAN_VERSION,
        source_asset_manifest=manifest,
    )

    assert result["_quality_blocked"] is False
    assert "Exact source statement." in result["_full_page"]


def test_claim_bound_combined_docx_uses_the_source_h1_once():
    canonical_h1 = "Existing — Source H1"
    optimised_h1 = "Model-Generated Unsupported H1"
    payload = all_in_one._build_combined_docx(
        url="https://example.com/service",
        h1="Input H1",
        primary_keyword="service support",
        page_type="service",
        template={
            "name": "Service",
            "sections": [_section("hero", heading_level="h1")],
        },
        generated_title="Service Support",
        generated_description="Service support overview.",
        optimised_h1=optimised_h1,
        faq_items=[],
        faq_schema=None,
        section_results={
            "hero": f"# {canonical_h1}\n\nExact source statement.",
        },
        word_count=6,
        competitor_urls=[],
        gen_meta=True,
        gen_faqs=False,
        gen_page_copy=True,
        page_quality_policy_version=PAGE_QUALITY_POLICY_VERSION,
        claim_bound_renderer_version=CLAIM_BOUND_RENDERER_VERSION,
        page_copy_canonical_h1=canonical_h1,
    )
    document = Document(io.BytesIO(payload))
    paragraph_text = [paragraph.text for paragraph in document.paragraphs]

    assert paragraph_text[0] == canonical_h1
    assert sum(text == canonical_h1 for text in paragraph_text) == 1
    assert sum(optimised_h1 in text for text in paragraph_text) == 1
    assert "Exact source statement." in paragraph_text
