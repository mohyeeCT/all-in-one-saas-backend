import math
import re


# ── Stemming ──────────────────────────────────────────────────────────────────

def _stem(word: str) -> str:
    """Basic suffix stripping for word overlap scoring."""
    word = word.lower()
    for suffix in ("ing", "tion", "tions", "ed", "er", "ers", "ly", "ies", "ness"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z]+", text.lower())
    return {_stem(w) for w in words if len(w) > 2}


# ── Brand filtering ───────────────────────────────────────────────────────────

def is_branded(keyword: str, brand_terms: list) -> bool:
    kw_lower = keyword.lower()
    return any(term.lower() in kw_lower for term in brand_terms if term.strip())


# ── Scoring formula ───────────────────────────────────────────────────────────

def score_keyword(
    keyword: str,
    volume: int,
    difficulty: int,
    impressions: float,
    ctr: float,
    position: float,
    h1: str = "",
) -> float:
    """
    score = (volume / difficulty) x log1p(impressions) x (1 + CTR) x position_score x relevance_score

    position_score: 1.0 for positions 1-20, drops off beyond 20. Position 1.0 exactly is hard-filtered upstream.
    relevance_score: word overlap between keyword and H1, range 0.5 to 1.5.
    """
    difficulty = max(difficulty, 1)
    volume = max(volume, 1)

    if position <= 20:
        position_score = 1.0
    else:
        position_score = max(0.1, 1.0 - (position - 20) / 100)

    h1_tokens = _tokens(h1) if h1 else set()
    kw_tokens = _tokens(keyword)
    if h1_tokens and kw_tokens:
        overlap = len(h1_tokens & kw_tokens) / len(kw_tokens)
        relevance_score = 0.5 + overlap
    else:
        relevance_score = 1.0

    return (volume / difficulty) * math.log1p(impressions) * (1 + ctr) * position_score * relevance_score


def rank_keywords(
    keyword_pool: list,
    brand_terms: list,
    h1: str = "",
    exclude_position_one: bool = True,
) -> list:
    """
    Takes a merged keyword pool (from GSC + DFS + manual).
    Each item must have: keyword, volume, difficulty, impressions, ctr, position.
    Returns sorted list with score added. Branded terms flagged but not removed.
    """
    results = []
    for kw in keyword_pool:
        pos = kw.get("position", 100.0)
        if exclude_position_one and pos == 1.0:
            kw["score"] = 0.0
            kw["branded"] = is_branded(kw["keyword"], brand_terms)
            results.append(kw)
            continue

        score = score_keyword(
            keyword=kw["keyword"],
            volume=kw.get("volume", 1),
            difficulty=kw.get("difficulty", 1),
            impressions=kw.get("impressions", 1),
            ctr=kw.get("ctr", 0.0),
            position=pos,
            h1=h1,
        )
        kw["score"] = score
        kw["branded"] = is_branded(kw["keyword"], brand_terms)
        results.append(kw)

    return sorted(results, key=lambda x: x["score"], reverse=True)


def merge_keyword_pools(
    gsc_rows: list,
    dfs_ranked: list,
    manual_seeds: list,
    dfs_volume_map: dict,
    dfs_difficulty_map: dict,
) -> list:
    """
    Merges GSC queries, DFS ranked keywords, and manual seeds into one pool.
    GSC provides impression/CTR/position signals.
    DFS volume and difficulty are used for keywords not in GSC.
    Deduplicates by keyword (case-insensitive).
    """
    pool = {}

    for row in gsc_rows:
        kw = row["query"].lower().strip()
        pool[kw] = {
            "keyword": kw,
            "volume": dfs_volume_map.get(kw, 0),
            "difficulty": dfs_difficulty_map.get(kw, 1),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0.0),
            "position": row.get("position", 100.0),
            "source": "gsc",
        }

    for item in dfs_ranked:
        kw = item["keyword"].lower().strip()
        if kw not in pool:
            pool[kw] = {
                "keyword": kw,
                "volume": item.get("volume", 0),
                "difficulty": item.get("difficulty", 1),
                "impressions": 1,
                "ctr": 0.0,
                "position": item.get("position", 100.0),
                "source": "dfs_ranked",
            }
        else:
            if pool[kw]["volume"] == 0:
                pool[kw]["volume"] = item.get("volume", 0)

    for kw_str in manual_seeds:
        kw = kw_str.lower().strip()
        if not kw:
            continue
        if kw not in pool:
            vol = dfs_volume_map.get(kw, 0)
            diff = dfs_difficulty_map.get(kw, 1)
            pool[kw] = {
                "keyword": kw,
                "volume": vol,
                "difficulty": diff,
                "impressions": 1,
                "ctr": 0.0,
                "position": 50.0,
                "source": "manual",
            }

    return list(pool.values())


def assign_keywords_to_sections(ranked_keywords: list, section_names: list) -> dict:
    """
    Distributes the ranked keyword cluster across template sections.
    First keyword (highest score) goes to the intro/primary slot.
    Remaining keywords assigned one per section in score order.
    Returns: { section_name: { primary: str, supporting: str } }
    """
    non_branded = [k for k in ranked_keywords if not k.get("branded") and k.get("score", 0) > 0]

    assignment = {}
    kw_queue = [k["keyword"] for k in non_branded]

    primary = kw_queue[0] if kw_queue else ""
    remaining = kw_queue[1:]

    for i, section in enumerate(section_names):
        if isinstance(section, dict):
            section_name = section["name"]
            keyword_slot = section.get("keyword_slot", "supporting")
        else:
            section_name = str(section)
            keyword_slot = "primary" if i == 0 else ("none" if section_name in {"cta", "contact"} else "supporting")

        if keyword_slot == "none":
            supporting = ""
        elif remaining:
            supporting_index = max(i - 1, 0) % len(remaining)
            supporting = remaining[supporting_index]
        else:
            supporting = ""

        assignment[section_name] = {
            "primary": primary if keyword_slot == "primary" else "",
            "supporting": supporting if keyword_slot in {"supporting", "lsi"} else "",
        }

    return assignment
