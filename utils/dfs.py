import requests
from urllib.parse import urlparse


BASE = "https://api.dataforseo.com/v3"


def _post(endpoint: str, payload: list, login: str, password: str) -> dict:
    resp = requests.post(
        f"{BASE}/{endpoint}",
        json=payload,
        auth=(login, password),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status_code") != 20000:
        raise RuntimeError(f"DFS error {data.get('status_code')}: {data.get('status_message')}")
    for task in data.get("tasks") or []:
        task_status = task.get("status_code")
        if task_status is not None and task_status != 20000:
            raise RuntimeError(f"DFS error {task_status}: {task.get('status_message')}")
    return data


def get_search_volume(keywords: list, login: str, password: str, location_code: int = 2840) -> dict:
    """Returns {keyword: volume} for a list of keywords."""
    payload = [{"keywords": keywords, "location_code": location_code, "language_code": "en"}]
    data = _post("keywords_data/google_ads/search_volume/live", payload, login, password)
    result = {}
    for task in data.get("tasks", []):
        for item in (task.get("result") or []):
            kw = item.get("keyword", "")
            vol = item.get("search_volume") or 0
            result[kw] = vol
    return result


def get_keyword_difficulty(keywords: list, login: str, password: str, location_code: int = 2840) -> dict:
    """Returns {keyword: difficulty} for a list of keywords."""
    payload = [{"keywords": keywords, "location_code": location_code, "language_code": "en"}]
    data = _post("dataforseo_labs/google/bulk_keyword_difficulty/live", payload, login, password)
    result = {}
    for task in data.get("tasks", []):
        for item in (task.get("result") or []):
            kw = item.get("keyword", "")
            diff = item.get("keyword_difficulty")
            result[kw] = max(diff if diff is not None else 1, 1)
    return result


def get_ranked_keywords_for_url(url: str, login: str, password: str, location_code: int = 2840, limit: int = 100) -> list:
    """
    Returns ranked keywords for a specific URL using DFS ranked_keywords endpoint.
    Filters to the exact URL path via relative_url.
    Each item: { keyword, volume, difficulty, position }
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    relative_url = parsed.path

    payload = [{
        "target": domain,
        "location_code": location_code,
        "language_code": "en",
        "limit": limit,
        "filters": [
            "ranked_serp_element.serp_item.relative_url",
            "=",
            relative_url
        ]
    }]
    data = _post("dataforseo_labs/google/ranked_keywords/live", payload, login, password)

    results = []
    for task in data.get("tasks", []):
        for item in (task.get("result") or []):
            for ranked in (item.get("items") or []):
                kw_data = ranked.get("keyword_data", {})
                kw_info = kw_data.get("keyword_info", {})
                kw = kw_data.get("keyword", "")
                vol = kw_info.get("search_volume") or 0
                diff = ranked.get("keyword_difficulty")
                diff = diff if diff is not None else 1
                pos_data = ranked.get("ranked_serp_element", {}).get("serp_item", {})
                pos = pos_data.get("rank_absolute") or 100
                if kw:
                    results.append({
                        "keyword": kw,
                        "volume": vol,
                        "difficulty": max(diff, 1),
                        "position": pos,
                    })
    return results


def get_keyword_ideas(seed_keyword: str, login: str, password: str, location_code: int = 2840, limit: int = 20) -> list:
    """
    Returns related/LSI keywords for a seed keyword.
    Each item: { keyword, volume, difficulty }
    Used to enrich section prompts with semantic coverage signals.
    """
    payload = [{
        "keyword": seed_keyword,
        "location_code": location_code,
        "language_code": "en",
        "limit": limit,
    }]
    data = _post("dataforseo_labs/google/keyword_ideas/live", payload, login, password)

    results = []
    for task in data.get("tasks", []):
        for item in (task.get("result") or []):
            for kw_item in (item.get("items") or []):
                kw = kw_item.get("keyword", "")
                vol = (kw_item.get("keyword_info") or {}).get("search_volume") or 0
                diff = kw_item.get("keyword_difficulty")
                diff = diff if diff is not None else 1
                if kw and kw != seed_keyword:
                    results.append({
                        "keyword": kw,
                        "volume": vol,
                        "difficulty": max(diff, 1),
                    })
    return results


def get_serp_content(keyword: str, login: str, password: str, location_code: int = 2840) -> dict:
    """
    Single SERP call that returns:
    - organic results (for competitor identification)
    - PAA questions
    - AI Overview content (when available)

    Uses asynchronous_ai_overview: true to request AI Overview in the same call.
    Returns: { organic: [...], paa: [...], ai_overview: str }
    """
    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": "en",
        "device": "desktop",
        "os": "windows",
        "depth": 10,
        "people_also_ask_click_depth": 2,
        "asynchronous_ai_overview": True,
    }]

    data = _post("serp/google/organic/live/advanced", payload, login, password)

    organic = []
    paa = []
    ai_overview_text = ""

    for task in data.get("tasks", []):
        for result_item in (task.get("result") or []):
            for item in (result_item.get("items") or []):
                item_type = item.get("type", "")

                if item_type == "organic":
                    organic.append({
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "description": item.get("description", ""),
                        "rank": item.get("rank_absolute", 99),
                    })

                elif item_type == "people_also_ask":
                    for paa_item in (item.get("items") or []):
                        q = paa_item.get("title") or paa_item.get("question", "")
                        expanded = (paa_item.get("expanded_element") or [{}])
                        a = expanded[0].get("description", "") if expanded else ""
                        if q:
                            paa.append({"question": q, "answer_excerpt": a})

                elif item_type in ("ai_overview", "ai_answer"):
                    # Extract text from AI Overview blocks
                    for block in (item.get("items") or []):
                        if block.get("type") == "paragraph":
                            ai_overview_text += block.get("text", "") + "\n"
                        elif block.get("type") == "list":
                            for li in (block.get("items") or []):
                                ai_overview_text += "- " + li.get("description", li.get("title", "")) + "\n"

    return {
        "organic": organic,
        "paa": paa,
        "ai_overview": ai_overview_text.strip(),
    }



def _extract_ai_overview_text(item: dict) -> str:
    """Extract AI Overview text from a DFS SERP item.

    Mirrors the SF script approach: no assumptions about block.type,
    just pull text from wherever it exists in the response.

    Priority order:
    1. item.items[].text  (structured blocks)
    2. item.text          (flat text field)
    3. item.markdown      (markdown fallback for async overviews)
    """
    if not item:
        return ""

    # 1. Try structured items array — map each block's text field
    blocks = item.get("items") or []
    if blocks:
        parts = []
        for block in blocks:
            txt = ""
            if isinstance(block, dict):
                txt = (
                    block.get("text", "")
                    or block.get("content", "")
                    or ""
                ).strip()
            if txt:
                parts.append(txt)
        combined = "\n\n".join(parts)
        if combined:
            return combined

    # 2. Try flat text field on the item itself
    flat = (item.get("text") or "").strip()
    if flat:
        return flat

    # 3. Try markdown field — present on some async AI Overview responses
    markdown = (item.get("markdown") or "").strip()
    if markdown:
        # Strip markdown syntax to plain text
        import re
        markdown = re.sub(r"!\[([^\]]*)\]\((https?://[^\)]+)\)", r"\1", markdown)
        markdown = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\1", markdown)
        markdown = re.sub(r"https?://\S+", "", markdown)
        markdown = re.sub(r"\*+", "", markdown)
        markdown = re.sub(r"#+\s*", "", markdown)
        markdown = re.sub(r"\s+", " ", markdown).strip()
        if markdown:
            return markdown

    return ""


def _extract_paa_answer(paa_el: dict) -> str:
    """Extract answer text from a PAA element, handling all known expanded_element types.

    Covers:
    - people_also_ask_expanded_element  (standard text answer)
    - people_also_ask_ai_overview_expanded_element  (AIO-style answer)
    - video elements  (use title as fallback)
    - table/list elements  (flatten to text)
    - any unknown type  (try all known text fields)
    """
    answer_source = (
        paa_el.get("expanded_element") or
        paa_el.get("items") or
        []
    )

    for el in answer_source:
        if not isinstance(el, dict):
            continue

        el_type = el.get("type", "")

        # Standard text answer
        if el_type == "people_also_ask_expanded_element":
            answer = (
                el.get("description", "")
                or el.get("text", "")
                or el.get("snippet", "")
                or el.get("featured_title", "")
                or ""
            ).strip()
            if answer:
                return answer

        # AI Overview style answer — flatten items[].text
        elif el_type == "people_also_ask_ai_overview_expanded_element":
            parts = []
            for sub in (el.get("items") or []):
                txt = (sub.get("text", "") or sub.get("content", "") or "").strip()
                if txt:
                    parts.append(txt)
            answer = " ".join(parts).strip()
            if answer:
                return answer

        # Video answer — use description or title as fallback text
        elif el_type in ("video", "youtube_video"):
            answer = (
                el.get("description", "")
                or el.get("title", "")
                or ""
            ).strip()
            if answer:
                return answer

        # Table answer — flatten rows to readable text
        elif el_type == "table":
            rows = el.get("table_element", {}).get("rows", []) if isinstance(el.get("table_element"), dict) else []
            cells = []
            for row in rows:
                for cell in (row.get("cells") or []):
                    txt = (cell.get("text", "") or "").strip()
                    if txt:
                        cells.append(txt)
            answer = ", ".join(cells[:8])
            if answer:
                return answer

        # List answer — join items
        elif el_type in ("list", "ordered_list", "unordered_list"):
            items_list = el.get("items") or []
            parts = []
            for li in items_list:
                txt = (li.get("text", "") or li.get("title", "") or "").strip()
                if txt:
                    parts.append(txt)
            answer = "; ".join(parts[:6])
            if answer:
                return answer

        # Unknown type — try every known text field
        else:
            answer = (
                el.get("description", "")
                or el.get("text", "")
                or el.get("snippet", "")
                or el.get("featured_title", "")
                or el.get("title", "")
                or ""
            ).strip()
            if answer:
                return answer

    # Nothing found in expanded_element — try top-level fields on paa_el itself
    return (
        paa_el.get("description", "")
        or paa_el.get("snippet", "")
        or paa_el.get("answer", "")
        or ""
    ).strip()



def get_serp_data(login: str, password: str, keyword: str, location_code: int = 2840, load_async_ai_overview: bool = True) -> dict:
    """Single SERP call that returns both AI Overview and PAA data.

    Returns:
    {
        "ai_overview_present": bool,
        "ai_overview_sections": [{"title": str, "content": str}, ...],
        "ai_overview_raw": str,          # full concatenated AI overview text
        "paa_questions": [str, ...],     # PAA question strings
        "paa_items": [{"question": str, "answer": str, "url": str}, ...]
    }
    """
    empty = {
        "ai_overview_present": False,
        "ai_overview_async_only": False,
        "ai_overview_sections": [],
        "ai_overview_raw": "",
        "paa_questions": [],
        "paa_items": [],
        "serp_item_types": [],
        "paa_raw_debug": "",
        "ao_raw_debug": "",
        "ao_raw_found": False,
        "ao_attempts": 0,
    }

    if not keyword:
        return empty

    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": "en",
        "depth": 10,
        "people_also_ask_click_depth": 4,
        "device": "desktop",
        "os": "macos",
        "load_async_ai_overview": load_async_ai_overview,
    }]

    # 3 attempts max — gives the async AI Overview a fair chance to load
    # without the original 5-attempt × 3s = 15s wasted sleep per URL
    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
      try:
        r = requests.post(
            f"{DFS_BASE}/serp/google/organic/live/advanced",
            headers=_auth_header(login, password),
            json=payload,
            timeout=45
        )
        r.raise_for_status()
        data = r.json()

        ai_sections = []
        ai_raw_parts = []
        paa_questions = []
        paa_items = []
        paa_raw_items = []
        ao_raw_items = []

        for task in data.get("tasks", []):
            for result_block in (task.get("result") or []):
                for item in (result_block.get("items") or []):
                    item_type = item.get("type", "")

                    # ── AI Overview ──────────────────────────────────────────
                    if item_type in ("ai_overview", "asynchronous_ai_overview"):
                        ao_raw_items.append(item)
                        ao_text = _extract_ai_overview_text(item)
                        if ao_text:
                            ai_sections.append({"title": "", "content": ao_text})
                            ai_raw_parts.append(ao_text)

                    # ── PAA ──────────────────────────────────────────────────
                    if item_type == "people_also_ask":
                        paa_raw_items.append(item)
                        for paa_el in (item.get("items") or []):
                            # DFS uses "title" for the question text,
                            # but fall back to other fields defensively
                            q = (
                                paa_el.get("title", "")
                                or paa_el.get("question", "")
                                or paa_el.get("name", "")
                                or paa_el.get("text", "")
                            ).strip()
                            if not q or q in paa_questions:
                                continue
                            paa_questions.append(q)
                            answer = _extract_paa_answer(paa_el)
                            paa_items.append({
                                "question": q,
                                "answer": answer,
                                "url": paa_el.get("url", "")
                            })

        # Collect all item types for debugging
        all_item_types = []
        for task in data.get("tasks", []):
            for result_block in (task.get("result") or []):
                for item in (result_block.get("items") or []):
                    t = item.get("type", "unknown")
                    if t not in all_item_types:
                        all_item_types.append(t)

        async_ao_detected = "asynchronous_ai_overview" in all_item_types
        ao_found = len(ai_sections) > 0

        result = {
            "ai_overview_present": ao_found,
            "ai_overview_async_only": async_ao_detected and not ao_found,
            "ai_overview_sections": ai_sections,
            "ai_overview_raw": "\n".join(ai_raw_parts),
            "paa_questions": paa_questions,
            "paa_items": paa_items,
            "serp_item_types": all_item_types,
            "paa_raw_debug": str(paa_raw_items[:1])[:500] if paa_raw_items else "",
            "ao_raw_debug": str(ao_raw_items[:1])[:800] if ao_raw_items else "",
            "ao_raw_found": len(ao_raw_items) > 0,
            "ao_attempts": attempt,
        }

        # AI Overview found — no need to retry
        if ao_found:
            return result

        # No AI Overview content yet — retry if attempts remain
        if attempt < max_attempts:
            import time as _time
            _time.sleep(1.5)
            continue

        # Exhausted all attempts — return best result so far
        return result

      except Exception as e:
        last_error = str(e)
        if attempt < max_attempts:
            import time as _time
            _time.sleep(1.5)
            continue
        result = empty.copy()
        result["error"] = last_error
        result["ao_attempts"] = attempt
        return result

