from copy import deepcopy

from utils.adaptive_templates import (
    ADAPTIVE_TEMPLATE_POLICIES,
    _authored_evidence_values,
    adapt_template_for_generation,
    attach_depth_policies,
    depth_policy_for_section,
)
from utils.page_quality import ADAPTIVE_POLICY_VERSION
from utils.templates import TEMPLATES


def _strategy(*sections):
    return {
        "section_guidance": [
            {
                "section": name,
                "responsibility": f"Write {name}.",
                "proof_points": list(proof_points),
            }
            for name, proof_points in sections
        ]
    }


def _plan_by_section(plan):
    return {item["section"]: item for item in plan}


def test_adaptive_policies_cover_every_builtin_template():
    assert set(ADAPTIVE_TEMPLATE_POLICIES) == set(TEMPLATES)

    for template_key, template in TEMPLATES.items():
        adapted, plan = adapt_template_for_generation(template, template_key, {})

        assert adapted["_adaptive_family"]
        assert len(plan) == len(template["sections"])
        assert all(item["mode"] == "full" for item in plan)


def test_adaptation_does_not_mutate_the_template_registry():
    original = deepcopy(TEMPLATES["service_page"])

    adapt_template_for_generation(
        TEMPLATES["service_page"],
        "service_page",
        _strategy(
            ("benefits", ["Benefit one"]),
            ("social_proof", []),
        ),
    )

    assert TEMPLATES["service_page"] == original


def test_proof_only_sections_without_keywords_can_be_omitted():
    template = TEMPLATES["homepage"]
    adapted, plan = adapt_template_for_generation(
        template,
        "homepage",
        _strategy(
            ("trust_bar", []),
            ("social_proof", []),
        ),
    )

    adapted_names = [section["name"] for section in adapted["sections"]]
    plan_by_section = _plan_by_section(plan)

    assert "trust_bar" not in adapted_names
    assert "social_proof" not in adapted_names
    assert plan_by_section["trust_bar"]["mode"] == "omit"
    assert plan_by_section["social_proof"]["reason"] == "no_owned_proof"


def test_keyword_bearing_proof_section_compacts_instead_of_disappearing():
    template = TEMPLATES["case_study_b2b"]
    adapted, plan = adapt_template_for_generation(
        template,
        "case_study_b2b",
        _strategy(("results", []), ("quote", [])),
    )
    adapted_by_name = {section["name"]: section for section in adapted["sections"]}
    plan_by_section = _plan_by_section(plan)

    assert "results" in adapted_by_name
    assert adapted_by_name["results"]["keyword_slot"] == "primary"
    assert plan_by_section["results"]["mode"] == "compact"
    assert adapted_by_name["results"]["word_count"][1] < [
        section for section in template["sections"] if section["name"] == "results"
    ][0]["word_count"][1]
    assert "quote" not in adapted_by_name


def test_responsive_sections_preserve_one_proof_and_compact_zero_proof():
    template = TEMPLATES["service_page"]
    adapted, plan = adapt_template_for_generation(
        template,
        "service_page",
        _strategy(
            ("benefits", ["Proof one", "Proof two", "Proof three"]),
            ("process", ["One verified process detail"]),
            ("pain_points", []),
        ),
    )
    adapted_by_name = {section["name"]: section for section in adapted["sections"]}
    plan_by_section = _plan_by_section(plan)

    assert plan_by_section["benefits"]["mode"] == "full"
    assert adapted_by_name["benefits"]["word_count"] == [250, 430]
    assert plan_by_section["process"]["mode"] == "full"
    assert adapted_by_name["process"]["word_count"] == [300, 500]
    assert plan_by_section["pain_points"]["mode"] == "compact"
    assert adapted_by_name["pain_points"]["word_count"] == [154, 304]
    assert "fewest complete paragraphs or blocks" in adapted_by_name["pain_points"]["adaptive_instruction"]
    assert "Use only as many blocks" not in adapted_by_name["pain_points"]["adaptive_instruction"]


def test_collection_story_value_and_guidance_compact_when_evidence_is_sparse():
    template = TEMPLATES["collection_page"]
    adapted, plan = adapt_template_for_generation(
        template,
        "collection_page",
        _strategy(
            ("category_intro", []),
            ("collection_story", []),
            ("collection_value", []),
            ("collection_guidance", []),
        ),
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        correction_evidence_contract=True,
    )
    adapted_by_name = {
        section["name"]: section
        for section in adapted["sections"]
    }
    plan_by_section = _plan_by_section(plan)

    assert adapted_by_name["category_intro"]["word_count"] == [60, 80]
    for section_name in (
        "collection_story",
        "collection_value",
        "collection_guidance",
    ):
        assert plan_by_section[section_name]["mode"] == "compact"
        assert plan_by_section[section_name]["evidence_sparse"] is True
        assert adapted_by_name[section_name]["word_count"] == [0, 60]


def test_informational_templates_keep_structure_and_relax_only_fill_quotas():
    for template_key in (
        "blog_standard",
        "blog_listicle",
        "blog_howto",
        "blog_comparison",
        "glossary",
    ):
        template = TEMPLATES[template_key]
        strategy = _strategy(*((section["name"], []) for section in template["sections"]))
        adapted, plan = adapt_template_for_generation(template, template_key, strategy)

        assert [section["name"] for section in adapted["sections"]] == [
            section["name"] for section in template["sections"]
        ]
        assert [section["keyword_slot"] for section in adapted["sections"]] == [
            section["keyword_slot"] for section in template["sections"]
        ]
        assert all(item["mode"] == "full" for item in plan)
        assert all(
            "Preserve this template's defining format" in section["adaptive_instruction"]
            for section in adapted["sections"]
        )
        assert all(
            "Honor any exact number promised in the page H1" in section["adaptive_instruction"]
            for section in adapted["sections"]
        )


def test_sections_without_explicit_strategy_contracts_are_not_shrunk_or_removed():
    template = TEMPLATES["about_us"]
    adapted, plan = adapt_template_for_generation(template, "about_us", {})

    assert [section["name"] for section in adapted["sections"]] == [
        section["name"] for section in template["sections"]
    ]
    assert all(item["reason"] == "no_section_contract" for item in plan)
    assert all(item["mode"] == "full" for item in plan)
    assert [section["word_count"] for section in adapted["sections"]] == [
        section["word_count"] for section in template["sections"]
    ]


def test_v1_omits_uncontracted_proof_only_sections_when_strategy_is_unavailable():
    template = TEMPLATES["local_service_page"]
    adapted, plan = adapt_template_for_generation(
        template,
        "local_service_page",
        {},
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
    )
    plan_by_section = _plan_by_section(plan)

    assert "local_social_proof" not in {
        section["name"] for section in adapted["sections"]
    }
    assert plan_by_section["local_social_proof"]["mode"] == "omit"
    assert plan_by_section["local_social_proof"]["reason"] == "no_owned_proof"
    assert plan_by_section["local_social_proof"]["depth_policy"] == "proof_only"
    assert plan_by_section["local_intro"]["mode"] == "full"
    assert plan_by_section["local_intro"]["reason"] == "no_section_contract"


def test_v1_keeps_keyword_proof_sections_compact_when_strategy_is_unavailable():
    cases = {
        "about_us": ("company_story", "credibility"),
        "case_study_b2b": ("results",),
    }

    for template_key, section_names in cases.items():
        template = TEMPLATES[template_key]
        adapted, plan = adapt_template_for_generation(
            template,
            template_key,
            {},
            adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        )
        original_by_name = {
            section["name"]: section for section in template["sections"]
        }
        adapted_by_name = {
            section["name"]: section for section in adapted["sections"]
        }
        plan_by_section = _plan_by_section(plan)

        for section_name in section_names:
            assert plan_by_section[section_name]["depth_policy"] == "proof_only"
            assert plan_by_section[section_name]["mode"] == "compact"
            assert (
                plan_by_section[section_name]["reason"]
                == "keyword_section_without_owned_proof"
            )
            assert (
                adapted_by_name[section_name]["keyword_slot"]
                == original_by_name[section_name]["keyword_slot"]
            )


def test_v1_missing_contract_obeys_proof_policy_across_builtin_templates():
    for template_key, template in TEMPLATES.items():
        adapted, plan = adapt_template_for_generation(
            template,
            template_key,
            {},
            adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        )
        original_by_name = {
            section["name"]: section for section in template["sections"]
        }
        adapted_by_name = {
            section["name"]: section for section in adapted["sections"]
        }

        for item in plan:
            if item["depth_policy"] != "proof_only":
                continue
            section_name = item["section"]
            keyword_slot = original_by_name[section_name]["keyword_slot"]
            if keyword_slot == "none":
                assert item["mode"] == "omit"
                assert item["reason"] == "no_owned_proof"
                assert section_name not in adapted_by_name
            else:
                assert item["mode"] == "compact"
                assert item["reason"] == "keyword_section_without_owned_proof"
                assert (
                    adapted_by_name[section_name]["keyword_slot"]
                    == keyword_slot
                )


def test_empty_proof_contracts_preserve_order_and_never_omit_keyword_sections():
    for template_key, template in TEMPLATES.items():
        strategy = _strategy(*((section["name"], []) for section in template["sections"]))
        adapted, plan = adapt_template_for_generation(template, template_key, strategy)
        original_by_name = {section["name"]: section for section in template["sections"]}
        plan_by_section = _plan_by_section(plan)
        surviving_names = [section["name"] for section in adapted["sections"]]
        expected_names = [
            section["name"]
            for section in template["sections"]
            if plan_by_section[section["name"]]["mode"] != "omit"
        ]

        assert surviving_names == expected_names
        for section in adapted["sections"]:
            assert section["keyword_slot"] == original_by_name[section["name"]]["keyword_slot"]
        for item in plan:
            if item["mode"] == "omit":
                assert original_by_name[item["section"]]["keyword_slot"] == "none"


def test_ecommerce_and_brand_families_use_the_same_evidence_rules():
    product, product_plan = adapt_template_for_generation(
        TEMPLATES["product_page"],
        "product_page",
        _strategy(
            ("benefits_features", ["One verified feature"]),
            ("social_proof", []),
        ),
    )
    about, about_plan = adapt_template_for_generation(
        TEMPLATES["about_us"],
        "about_us",
        _strategy(
            ("company_story", ["One verified company fact"]),
            ("mission_values", []),
            ("team", []),
        ),
    )

    product_modes = _plan_by_section(product_plan)
    about_modes = _plan_by_section(about_plan)

    assert product["_adaptive_family"] == "ecommerce"
    assert product_modes["benefits_features"]["mode"] == "full"
    assert product_modes["social_proof"]["mode"] == "omit"
    assert about["_adaptive_family"] == "brand"
    assert about_modes["company_story"]["mode"] == "full"
    assert about_modes["mission_values"]["mode"] == "omit"
    assert about_modes["team"]["mode"] == "omit"


def test_v1_retains_section_depth_and_compacts_only_unsupported_claim_areas():
    strategy = _strategy(
        ("pain_points", []),
        ("benefits", []),
        ("social_proof", []),
    )
    strategy = attach_depth_policies(
        strategy,
        "service_page",
        ADAPTIVE_POLICY_VERSION,
    )
    adapted, plan = adapt_template_for_generation(
        TEMPLATES["service_page"],
        "service_page",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
    )
    adapted_by_name = {section["name"]: section for section in adapted["sections"]}
    plan_by_section = _plan_by_section(plan)

    assert plan_by_section["pain_points"]["depth_policy"] == "explanatory"
    assert plan_by_section["pain_points"]["mode"] == "full"
    assert adapted_by_name["pain_points"]["word_count"] == [220, 380]
    assert plan_by_section["benefits"]["depth_policy"] == "claim_sensitive"
    assert plan_by_section["benefits"]["mode"] == "full"
    assert adapted_by_name["benefits"]["word_count"] == [250, 430]
    assert "compact or withhold areas" in adapted_by_name["benefits"]["adaptive_instruction"]
    assert "fewest complete paragraphs or blocks" not in adapted_by_name["benefits"]["adaptive_instruction"]
    assert plan_by_section["social_proof"]["depth_policy"] == "proof_only"
    assert plan_by_section["social_proof"]["mode"] == "omit"


def test_correction_contract_compacts_sparse_local_sections_without_reassigning_keywords():
    rich_local_proof = " ".join(
        f"Supported local evidence detail {index}"
        for index in range(80)
    )
    strategy = _strategy(
        ("hero", ["One supported hero fact"]),
        ("local_intro", []),
        ("services_in_location", []),
        ("why_local", [rich_local_proof]),
        ("service_area", []),
        ("local_social_proof", []),
        ("faq", ["One supported legal notice"]),
        ("cta", []),
    )
    strategy = attach_depth_policies(
        strategy,
        "local_service_page",
        ADAPTIVE_POLICY_VERSION,
    )
    original = deepcopy(TEMPLATES["local_service_page"])

    legacy_adapted, legacy_plan = adapt_template_for_generation(
        TEMPLATES["local_service_page"],
        "local_service_page",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
    )
    corrected_adapted, corrected_plan = adapt_template_for_generation(
        TEMPLATES["local_service_page"],
        "local_service_page",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        correction_evidence_contract=True,
    )

    legacy_by_name = {
        section["name"]: section
        for section in legacy_adapted["sections"]
    }
    corrected_by_name = {
        section["name"]: section
        for section in corrected_adapted["sections"]
    }
    legacy_plan_by_name = _plan_by_section(legacy_plan)
    corrected_plan_by_name = _plan_by_section(corrected_plan)

    assert legacy_plan_by_name["services_in_location"]["mode"] == "full"
    assert legacy_by_name["services_in_location"]["word_count"] == [320, 560]
    assert corrected_plan_by_name["services_in_location"]["mode"] == "compact"
    assert corrected_plan_by_name["services_in_location"]["reason"] == (
        "unsupported_claim_areas"
    )
    assert corrected_by_name["services_in_location"]["word_count"] == [0, 60]
    assert corrected_by_name["services_in_location"]["evidence_sparse"] is True

    assert corrected_plan_by_name["local_intro"]["mode"] == "compact"
    assert corrected_plan_by_name["service_area"]["mode"] == "compact"
    assert corrected_plan_by_name["faq"]["mode"] == "compact"
    assert corrected_plan_by_name["cta"]["mode"] == "compact"
    assert corrected_plan_by_name["why_local"]["mode"] == "full"
    assert corrected_by_name["why_local"]["word_count"] == [250, 430]
    assert corrected_plan_by_name["local_social_proof"]["mode"] == "omit"

    assert [section["name"] for section in corrected_adapted["sections"]] == [
        section["name"]
        for section in original["sections"]
        if section["name"] != "local_social_proof"
    ]
    assert {
        section["name"]: section["keyword_slot"]
        for section in corrected_adapted["sections"]
    } == {
        section["name"]: section["keyword_slot"]
        for section in original["sections"]
        if section["name"] != "local_social_proof"
    }
    assert TEMPLATES["local_service_page"] == original


def test_correction_contract_rejects_sparse_strategy_headings_and_coverage():
    strategy = _strategy(
        ("hero", ["The page identifies named service-area communities."]),
        ("local_intro", ["The page identifies named service-area communities."]),
        ("services_in_location", []),
        ("why_local", []),
        ("service_area", []),
        ("local_social_proof", []),
        ("faq", ["Contacting the firm does not create a client relationship."]),
        ("cta", []),
    )
    unsafe_plans = {
        "services_in_location": (
            "Legal Support Available Across Greater Houston",
            ["General availability throughout the region"],
        ),
        "why_local": (
            "Working With Clients Throughout the Region",
            ["Region-wide access and consistent service"],
        ),
        "service_area": (
            "Communities We Serve Across Greater Houston",
            ["Coverage across the whole Greater Houston region"],
        ),
        "faq": (
            "Booking and Coverage Questions",
            ["How to book", "What a consultation includes"],
        ),
        "cta": (
            "Book Your Free Consultation Today",
            ["How to schedule", "Get a free quote from the local team"],
        ),
    }
    for contract in strategy["section_guidance"]:
        if contract["section"] not in unsafe_plans:
            continue
        heading, coverage = unsafe_plans[contract["section"]]
        contract["planned_heading"] = heading
        contract["coverage_points"] = coverage
    strategy = attach_depth_policies(
        strategy,
        "local_service_page",
        ADAPTIVE_POLICY_VERSION,
    )

    adapted, plan = adapt_template_for_generation(
        TEMPLATES["local_service_page"],
        "local_service_page",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        correction_evidence_contract=True,
    )
    adapted_by_name = {
        section["name"]: section
        for section in adapted["sections"]
    }
    plan_by_name = _plan_by_section(plan)

    for section_name in (
        "services_in_location",
        "why_local",
        "service_area",
        "faq",
    ):
        assert plan_by_name[section_name]["evidence_sparse"] is True
        assert "planned_heading" not in adapted_by_name[section_name]
        assert "coverage_points" not in adapted_by_name[section_name]
    assert "planned_heading" not in adapted_by_name["cta"]
    assert "coverage_points" not in adapted_by_name["cta"]
    assert {
        section["name"]: section["keyword_slot"]
        for section in adapted["sections"]
    } == {
        section["name"]: section["keyword_slot"]
        for section in TEMPLATES["local_service_page"]["sections"]
        if section["name"] != "local_social_proof"
    }


def test_correction_contract_deduplicates_overlapping_authored_evidence():
    proof_excerpt = (
        "Readers can review the finishing guide and use the custom curtain "
        "quote request path before contacting the company."
    )
    direct_statement = (
        "The article says readers can review the finishing guide and use the "
        "custom curtain quote request path before contacting the company."
    )
    contract = {
        "section": "cta",
        "proof_facts": [{
            "fact": proof_excerpt,
            "source_excerpt": proof_excerpt,
        }],
        "source_assets": [{
            "id": "A1",
            "kind": "direct_statement",
            "statement": direct_statement,
        }],
    }
    strategy = attach_depth_policies(
        {"section_guidance": [contract]},
        "blog_standard",
        ADAPTIVE_POLICY_VERSION,
    )
    cta_section = next(
        deepcopy(section)
        for section in TEMPLATES["blog_standard"]["sections"]
        if section["name"] == "cta"
    )

    adapted, plan = adapt_template_for_generation(
        {"sections": [cta_section]},
        "blog_standard",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        correction_evidence_contract=True,
    )

    assert len(_authored_evidence_values(contract)) == 1
    assert plan[0]["authored_evidence_count"] == 1
    assert plan[0]["mode"] == "compact"
    assert plan[0]["evidence_sparse"] is True
    assert adapted["sections"][0]["word_count"][0] == 0


def test_authored_evidence_containment_dedupe_remains_conservative():
    short_generic = "Free consultation"
    richer_offer = "Free consultation appointments are available on weekdays."
    shared_prefix_left = (
        "The article explains that finished dimensions affect material quantity."
    )
    shared_prefix_right = (
        "The article explains that fullness affects material quantity."
    )
    contract = {
        "proof_facts": [{
            "source_excerpt": short_generic,
        }, {
            "source_excerpt": shared_prefix_left,
        }],
        "source_assets": [{
            "kind": "direct_statement",
            "statement": richer_offer,
        }, {
            "kind": "direct_statement",
            "statement": shared_prefix_right,
        }],
    }

    assert _authored_evidence_values(contract) == [
        short_generic,
        shared_prefix_left,
        richer_offer,
        shared_prefix_right,
    ]


def test_correction_contract_evidence_bounds_blog_sections_without_changing_structure():
    rich_body_proof = " ".join(
        f"Supported body evidence detail {index}"
        for index in range(100)
    )
    strategy = _strategy(
        ("intro", ["Supported introduction fact"]),
        ("context", ["Supported context fact"]),
        ("body_1", [rich_body_proof]),
        ("body_2", ["Supported body two fact"]),
        ("body_3", ["Supported body three fact"]),
        ("summary", []),
        ("faq", []),
        ("cta", ["Supported next step"]),
    )
    strategy = attach_depth_policies(
        strategy,
        "blog_standard",
        ADAPTIVE_POLICY_VERSION,
    )

    adapted, plan = adapt_template_for_generation(
        TEMPLATES["blog_standard"],
        "blog_standard",
        strategy,
        adaptive_policy_version=ADAPTIVE_POLICY_VERSION,
        correction_evidence_contract=True,
    )
    adapted_by_name = {
        section["name"]: section
        for section in adapted["sections"]
    }
    plan_by_name = _plan_by_section(plan)

    assert plan_by_name["summary"]["mode"] == "compact"
    assert plan_by_name["summary"]["reason"] == "limited_recap_evidence"
    assert adapted_by_name["summary"]["word_count"] == [0, 180]
    assert plan_by_name["faq"]["mode"] == "compact"
    assert plan_by_name["faq"]["reason"] == "limited_recap_evidence"
    assert adapted_by_name["faq"]["word_count"] == [0, 60]
    assert plan_by_name["body_1"]["mode"] == "full"
    assert adapted_by_name["body_1"]["word_count"] == [360, 600]
    assert plan_by_name["body_2"]["mode"] == "compact"
    assert plan_by_name["body_2"]["reason"] == "insufficient_owned_evidence"
    assert adapted_by_name["body_2"]["word_count"] == [0, 60]
    assert all(
        plan_by_name[name]["mode"] == "compact"
        for name in ("intro", "context", "body_2", "body_3", "summary", "faq", "cta")
    )
    assert [section["name"] for section in adapted["sections"]] == [
        section["name"] for section in TEMPLATES["blog_standard"]["sections"]
    ]
    assert {
        section["name"]: section["keyword_slot"]
        for section in adapted["sections"]
    } == {
        section["name"]: section["keyword_slot"]
        for section in TEMPLATES["blog_standard"]["sections"]
    }


def test_model_depth_policy_is_replaced_by_the_reviewed_server_policy():
    strategy = {
        "section_guidance": [{
            "section": "pain_points",
            "responsibility": "Explain the reader problem.",
            "proof_points": [],
            "depth_policy": "proof_only",
        }]
    }

    attached = attach_depth_policies(
        strategy,
        "service_page",
        ADAPTIVE_POLICY_VERSION,
    )

    assert attached["section_guidance"][0]["depth_policy"] == "explanatory"


def test_v1_depth_classification_is_dispatched_from_its_immutable_registry(
    monkeypatch,
):
    monkeypatch.setitem(
        ADAPTIVE_TEMPLATE_POLICIES,
        "service_page",
        {
            **ADAPTIVE_TEMPLATE_POLICIES["service_page"],
            "claim_sensitive_sections": frozenset(),
        },
    )

    assert depth_policy_for_section(
        "service_page",
        "benefits",
        ADAPTIVE_POLICY_VERSION,
    ) == "claim_sensitive"
