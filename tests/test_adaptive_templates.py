from copy import deepcopy

from utils.adaptive_templates import ADAPTIVE_TEMPLATE_POLICIES, adapt_template_for_generation
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


def test_responsive_sections_scale_with_owned_proof():
    template = TEMPLATES["service_page"]
    adapted, plan = adapt_template_for_generation(
        template,
        "service_page",
        _strategy(
            ("benefits", ["Proof one", "Proof two", "Proof three"]),
            ("process", ["One verified process detail"]),
        ),
    )
    adapted_by_name = {section["name"]: section for section in adapted["sections"]}
    plan_by_section = _plan_by_section(plan)

    assert plan_by_section["benefits"]["mode"] == "full"
    assert adapted_by_name["benefits"]["word_count"] == [120, 190]
    assert plan_by_section["process"]["mode"] == "compact"
    assert adapted_by_name["process"]["word_count"] == [60, 108]
    assert "fewest complete paragraphs or blocks" in adapted_by_name["process"]["adaptive_instruction"]


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
    assert product_modes["benefits_features"]["mode"] == "compact"
    assert product_modes["social_proof"]["mode"] == "omit"
    assert about["_adaptive_family"] == "brand"
    assert about_modes["company_story"]["mode"] == "compact"
    assert about_modes["mission_values"]["mode"] == "omit"
    assert about_modes["team"]["mode"] == "omit"
