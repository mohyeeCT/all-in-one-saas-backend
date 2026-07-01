from utils.page_types import default_template_key_for_page_type, normalize_page_type


def test_service_aliases_stay_service():
    assert normalize_page_type("service_lp") == "service"
    assert normalize_page_type("Service Landing Page") == "service"
    assert normalize_page_type("service page") == "service"


def test_plain_landing_page_stays_distinct():
    assert normalize_page_type("landing page") == "landing_page"
    assert normalize_page_type("LP") == "landing_page"
    assert default_template_key_for_page_type("landing_page") == "landing_page"


def test_aio_specific_aliases_match_template_page_types():
    assert normalize_page_type("category page") == "collection"
    assert normalize_page_type("home page") == "homepage"
    assert normalize_page_type("about us") == "about"
    assert normalize_page_type("", default="service") == "service"
