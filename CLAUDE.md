# all-in-one-saas-backend — Repo Context

See `../CLAUDE.md` for full platform context, conventions, and working rules.

## What This Repo Is

FastAPI backend for the All in One workflow. Runs Meta + FAQ + full Page Copy
in a single shared pipeline per URL. Most complex backend.
Deployed on Railway EU West. Default branch: `main`. Current HEAD: `8e89b93`.
Runtime: Python 3.12.

Railway URL: `https://all-in-one-saas-backend-production.up.railway.app`

## File Structure

```
main.py           — App, CORS, router mounts, global exception handler
auth.py           — Supabase token validation
models.py         — Pydantic models
routers/
  all_in_one.py   — POST /api/all-in-one/run, GET /templates,
                    _process_single_row, _build_combined_docx
  jobs.py         — Shared job CRUD + POST /{id}/rerun-section (AiO-only)
  settings.py     — Shared settings CRUD
utils/
  copy_gen.py     — All generation functions: meta (structured JSON),
                    generate_faq, generate_faq_batch, generate_page,
                    _build_section_prompt, sanitise, PROVIDER_FN
  dfs.py          — Keyword volume, difficulty, SERP, LSI
  gsc.py          — GSC queries
  keyword.py      — select_keyword, keyword assignment
  scraper.py      — Jina page scraping
  faq_scraper.py  — Competitor FAQ scraping
  niches.py       — get_niche_context (23 niches)
  templates.py    — Page section templates
  docx_export.py  — .docx generation
schema.sql        — Reference schema including brand_profiles
tests/
  test_cors.py
  test_dfs_error_visibility.py
  test_provider_routing.py    — Provider routing regression tests
```

## Endpoints

Same shared job CRUD as other backends, plus:
```
POST /api/all-in-one/run
GET  /api/all-in-one/templates
POST /api/jobs/{id}/rerun-section     — AiO-only
```

## AiO Pipeline (_process_single_row)

Shared pipeline per URL — no redundant API calls:
1. Select primary keyword (GSC → DFS → manual → H1 fallback)
2. Fetch SERP: PAA + AI Overview
3. Scrape competitor pages (if enabled)
4. Build competitor section map
5. Per-row output toggles: gen_meta, gen_faqs, gen_page_copy
6. If gen_meta: generate title, description, H1 (structured JSON)
7. If gen_faqs: generate FAQ items + schema
8. If gen_page_copy: generate all sections via generate_page
9. Build combined .docx: _build_combined_docx
10. Write result to Supabase

## Provider Routing

All three generation steps (meta, FAQ, page copy) use the same provider
from job settings. Provider routing was repaired — do not hardcode a provider
for any individual step. Always use PROVIDER_FN[settings["provider"]].

## Section-Level Rerun (rerun-section)

POST /api/jobs/{id}/rerun-section
Body: { row_index: int, section_name: str }

Background task _rerun_single_section:
1. Fetches credentials from user_settings
2. Merges brand profile into client_brief
3. Loads template from rows[row_index]["template_key"] or settings fallback
4. Re-fetches SERP for fresh PAA + AI Overview (one DFS call)
5. Builds previous_section_text from stored section_results (ordered by template)
6. Calls _build_section_prompt + PROVIDER_FN for that section only
7. Patches section_results, rebuilds full_page + word_count
8. Regenerates docx via _build_combined_docx (keeps old docx on failure)
9. Re-fetches results from Supabase before patching (avoids race conditions)
10. Writes back

Limitations by design:
- competitor_section_map not stored — section reruns use empty competitor excerpts
- keyword_assignment not stored — uses primary keyword for all sections on rerun

## _build_combined_docx

Module-level function in routers/all_in_one.py. Importable from jobs.py for
section rerun. Accepts: url, h1, primary_keyword, page_type, template,
generated_title, generated_description, optimised_h1, faq_items, faq_schema,
section_results, word_count, competitor_urls, gen_meta, gen_faqs, gen_page_copy.

## Key Model Fields

```python
niche: str = ""
business_type: str = "general"
provider: str = "Claude"
brand_name: str
include_brand: bool = True
forbidden_phrases: str = ""
brand_profile_id: str = ""
template_key: str = "service_page"
client_brief: str = ""
gen_meta: bool = True      # per-row toggleable
gen_faqs: bool = True      # per-row toggleable
gen_page_copy: bool = True # per-row toggleable
```

## Known Gotchas

- Section reruns live in jobs.py, not all_in_one.py, because they need to
  import _build_combined_docx from all_in_one. Circular import risk: always
  import _build_combined_docx inside the background function, not at module top.
- All three generation steps (meta, faq, page) must use the same PROVIDER_FN
  routing — no hardcoded providers.
- The stop button works between rows but cannot interrupt a running AI call
  mid-execution. This is a fundamental threading limitation, not a bug.
- signal.SIGALRM must never be used — background thread context.
