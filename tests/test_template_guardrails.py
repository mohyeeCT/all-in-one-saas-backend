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
        "blog_standard": (1700, 2690),
        "blog_listicle": (1370, 2240),
        "blog_howto": (1630, 2590),
        "blog_comparison": (1690, 2660),
        "case_study_b2b": (1450, 2320),
        "glossary": (1210, 1920),
        "homepage": (610, 1040),
        "landing_page": (800, 1330),
        "service_page": (1350, 2260),
        "local_service_page": (1260, 2110),
        "about_us": (1080, 1780),
        "contact_us": (560, 920),
        "product_page": (850, 1450),
        "collection_page": (400, 700),
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
