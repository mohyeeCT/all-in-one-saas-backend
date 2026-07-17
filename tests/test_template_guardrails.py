from utils.templates import TEMPLATES


def _all_template_text() -> str:
    parts: list[str] = []
    for template in TEMPLATES.values():
        parts.append(template.get("description", ""))
        for section in template.get("sections", []):
            parts.extend(
                [
                    section.get("label", ""),
                    section.get("purpose", ""),
                    section.get("prompt_rules", ""),
                ]
            )
    return " ".join(parts).lower()


def _total_word_max(template_key: str) -> int:
    return sum(section["word_count"][1] for section in TEMPLATES[template_key]["sections"])


def _total_word_min(template_key: str) -> int:
    return sum(section["word_count"][0] for section in TEMPLATES[template_key]["sections"])


def test_templates_do_not_ask_for_invented_placeholders_or_social_proof():
    text = _all_template_text()

    banned_phrases = [
        "realistic placeholder",
        "write placeholders",
        "use placeholder",
        "placeholder-format",
        "rated 4.",
        "[x% improvement",
    ]

    for phrase in banned_phrases:
        assert phrase not in text


def test_predefined_templates_use_expanded_intent_appropriate_word_ranges():
    expected_totals = {
        "blog_standard": (2160, 3540),
        "blog_listicle": (1740, 2870),
        "blog_howto": (2090, 3450),
        "blog_comparison": (2140, 3500),
        "case_study_b2b": (1840, 3110),
        "glossary": (1520, 2540),
        "homepage": (890, 1600),
        "landing_page": (1130, 1950),
        "service_page": (1780, 3060),
        "local_service_page": (1660, 2890),
        "about_us": (1370, 2350),
        "contact_us": (720, 1220),
        "product_page": (1120, 1930),
        "collection_page": (650, 1110),
    }

    assert sum(len(template["sections"]) for template in TEMPLATES.values()) == 86
    for template_key, expected in expected_totals.items():
        assert (_total_word_min(template_key), _total_word_max(template_key)) == expected

    for template in TEMPLATES.values():
        for section in template["sections"]:
            minimum, maximum = section["word_count"]
            assert minimum >= 40
            assert maximum > minimum
            assert maximum - minimum >= 30


def test_blog_template_labels_are_reader_intent_specific():
    labels = {
        section["label"]
        for key in ("blog_standard", "blog_comparison")
        for section in TEMPLATES[key]["sections"]
    }

    assert "Core Section 1" not in labels
    assert "Core Section 2" not in labels
    assert "Core Section 3" not in labels
    assert "Comparison Criteria 1" not in labels
    assert "Comparison Criteria 2" not in labels
    assert "Comparison Criteria 3" not in labels
