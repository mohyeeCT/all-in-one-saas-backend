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


def test_commercial_templates_use_tighter_default_word_ranges():
    assert _total_word_max("homepage") <= 760
    assert _total_word_max("service_page") <= 1350
    assert _total_word_max("local_service_page") <= 1350
    assert _total_word_max("product_page") <= 900


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
