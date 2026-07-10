from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from google.auth.exceptions import RefreshError
from datetime import datetime, timedelta, timezone
from auth import get_current_user, get_supabase
from credentials import hydrate_job_settings, mark_gsc_reconnect_required, strip_secret_fields
from abuse_protection import enforce_job_start, enforce_rate_limit, execute_active_job_write

router = APIRouter()

_GSC_RECONNECT_ERROR = "Google Search Console reconnect required."
_GSC_UNAVAILABLE_ERROR = "Selected Google Search Console connection unavailable."
_GSC_CONFIG_ERROR = "Google Search Console OAuth configuration missing."
_CREDENTIALS_UNAVAILABLE_ERROR = "Saved credentials are temporarily unavailable."
_STALE_CANCELLING_AFTER = timedelta(minutes=30)
_STALE_FINISHED_RUNNING_AFTER = timedelta(minutes=2)


from pydantic import BaseModel

class RenameRequest(BaseModel):
    name: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_updated_at(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale(job: dict, threshold: timedelta) -> bool:
    updated_at = _parse_updated_at(job.get("updated_at"))
    return bool(updated_at and _utc_now() - updated_at >= threshold)


def _failed_row_count(results: list | None, fallback: int = 0) -> int:
    if not isinstance(results, list):
        return fallback
    return sum(1 for row in results if isinstance(row, dict) and row.get("status") != "ok")


def _has_finished_rows(job: dict) -> bool:
    total = int(job.get("total_rows") or 0)
    completed = int(job.get("completed_rows") or 0)
    if total <= 0 or completed < total:
        return False
    results = job.get("results")
    return not isinstance(results, list) or len(results) >= total


def _is_missing_internal_link_suggestions_column(exc: Exception) -> bool:
    message = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").upper()
    return "internal_link_suggestions" in message and (
        code in {"42703", "PGRST204", "PGRST205"}
        or "column" in message
        or "schema cache" in message
    )


def _execute_job_update(sb, job: dict, user_id: str, update: dict):
    return (
        sb.table("jobs")
        .update(update)
        .eq("id", job["id"])
        .eq("user_id", user_id)
        .execute()
    )


def _persist_terminal_status(sb, job: dict, user_id: str, payload: dict) -> dict:
    update = {**payload, "updated_at": "now()"}
    try:
        _execute_job_update(sb, job, user_id, update)
    except Exception as exc:
        if (
            "internal_link_suggestions" in update
            and _is_missing_internal_link_suggestions_column(exc)
        ):
            update = {**update}
            update.pop("internal_link_suggestions", None)
            _execute_job_update(sb, job, user_id, update)
        else:
            raise
    job.update(update)
    return job


def _finalize_stale_active_job(sb, job: dict, user_id: str) -> dict:
    status = job.get("status")
    if status == "cancelling" and _is_stale(job, _STALE_CANCELLING_AFTER):
        return _persist_terminal_status(sb, job, user_id, {
            "status": "cancelled",
            "current_step": "Cancelled after worker stopped responding.",
            "failed_rows": _failed_row_count(job.get("results"), int(job.get("failed_rows") or 0)),
        })
    if (
        status == "running"
        and _has_finished_rows(job)
        and _is_stale(job, _STALE_FINISHED_RUNNING_AFTER)
    ):
        payload = {
            "status": "complete",
            "current_step": "Done.",
            "failed_rows": _failed_row_count(job.get("results"), int(job.get("failed_rows") or 0)),
        }
        if "internal_link_suggestions" not in job:
            payload["internal_link_suggestions"] = []
        return _persist_terminal_status(sb, job, user_id, payload)
    return job


@router.patch("/{job_id}/rename")
def rename_job(job_id: str, body: RenameRequest, user=Depends(get_current_user)):
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .update({"name": body.name.strip()})
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"renamed": True}


@router.get("")
def list_jobs(user=Depends(get_current_user)):
    """Return all jobs for the current user, newest first."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .select("id, name, status, total_rows, completed_rows, failed_rows, created_at, updated_at, error")
        .eq("user_id", user.id)
        .eq("tool", "all-in-one")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return [_finalize_stale_active_job(sb, job, user.id) for job in (res.data or [])]


@router.get("/{job_id}")
def get_job(job_id: str, user=Depends(get_current_user)):
    """Return full job including results."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .select("*")
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _finalize_stale_active_job(sb, res.data[0], user.id)
    return {**job, "settings": strip_secret_fields(job.get("settings"))}


@router.delete("/{job_id}")
def delete_job(job_id: str, user=Depends(get_current_user)):
    """Delete a job from history."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .delete()
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"deleted": True}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user=Depends(get_current_user)):
    """Cancel a running job. The processing loop checks this flag and stops gracefully."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .select("id, status, user_id")
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = res.data[0]
    if job["status"] in {"cancelling", "cancelled"}:
        return {"cancelled": True}
    if job["status"] != "running":
        raise HTTPException(status_code=400, detail=f"Job is not running (status: {job['status']})")
    sb.table("jobs").update({
        "status": "cancelling",
        "current_step": "Cancelling - stopping after current row...",
    }).eq("id", job_id).eq("user_id", user.id).execute()
    return {"cancelled": True}


class RerunRequest(BaseModel):
    keyword_override: str = ""


class MultiRerunRequest(BaseModel):
    row_indices: list[int]


def _persist_gsc_error(sb, job_id: str, user_id: str, message: str):
    try:
        sb.table("jobs").update({"error": message}).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        pass


def _clear_runtime_error(sb, job_id: str, user_id: str, messages: list[str]):
    try:
        (
            sb.table("jobs")
            .update({"error": None})
            .eq("id", job_id)
            .eq("user_id", user_id)
            .in_("error", messages)
            .execute()
        )
    except Exception:
        pass


def _clear_gsc_runtime_error(sb, job_id: str, user_id: str):
    _clear_runtime_error(sb, job_id, user_id, [_GSC_UNAVAILABLE_ERROR, _GSC_RECONNECT_ERROR, _GSC_CONFIG_ERROR])


def _clear_credentials_runtime_error(sb, job_id: str, user_id: str):
    _clear_runtime_error(sb, job_id, user_id, [_CREDENTIALS_UNAVAILABLE_ERROR])


def _get_runtime_gsc_client(settings: dict, sb, user_id: str, job_id: str):
    if not settings.get("use_gsc"):
        return None
    credentials = settings.get("_gsc_credentials")
    if not credentials:
        _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
        return None

    from utils.gsc import GscOAuthConfigError, get_gsc_client

    try:
        client = get_gsc_client(credentials)
        _clear_gsc_runtime_error(sb, job_id, user_id)
        return client
    except GscOAuthConfigError:
        _persist_gsc_error(sb, job_id, user_id, _GSC_CONFIG_ERROR)
    except RefreshError:
        if credentials.get("method") == "google_oauth":
            _persist_gsc_error(sb, job_id, user_id, _GSC_RECONNECT_ERROR)
            ciphertext = credentials.get("refresh_token_ciphertext")
            if ciphertext:
                try:
                    mark_gsc_reconnect_required(sb, user_id, ciphertext)
                except Exception:
                    pass
        else:
            _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
    except Exception:
        _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
    return None


@router.post("/{job_id}/rerun-row/{row_index}")
def rerun_row(
    job_id: str,
    row_index: int,
    body: RerunRequest = None,
    background_tasks: BackgroundTasks = None,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Re-run a single row in a completed job, optionally with a keyword override."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res.data[0]
    rows = job.get("rows", [])
    settings = job.get("settings", {})

    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=400, detail="Row index out of range")
    enforce_job_start(sb, user.id, "all-in-one", 1, 50, exclude_job_id=job_id)
    enforce_rate_limit(sb, user.id, "all-in-one", "row-rerun", 30)

    keyword_override = (body.keyword_override or "").strip() if body else ""

    step_msg = f"Re-running row {row_index + 1}"
    if keyword_override:
        step_msg += f' with keyword "{keyword_override}"'
    step_msg += "..."

    sb.table("jobs").update({
        "current_step": step_msg,
        "updated_at": "now()"
    }).eq("id", job_id).eq("user_id", user.id).execute()

    background_tasks.add_task(_rerun_single_row, job_id, row_index, rows, settings, sb, keyword_override, user.id)
    return {"status": "rerunning"}


def _rerun_single_row(job_id: str, row_index: int, rows: list, settings: dict, sb, keyword_override: str = "", user_id: str = ""):
    """Background task to re-run one row and update its result in place."""
    try:
        settings = hydrate_job_settings(sb, user_id, settings)
    except Exception:
        (
            sb.table("jobs")
            .update({
                "error": _CREDENTIALS_UNAVAILABLE_ERROR,
                "current_step": f"Row {row_index + 1} re-run failed: saved credentials are temporarily unavailable.",
                "updated_at": "now()",
            })
            .eq("id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        return
    _clear_credentials_runtime_error(sb, job_id, user_id)
    import traceback, time

    try:
        row = rows[row_index]
        # Apply keyword override if provided - inject as manual keyword
        if keyword_override:
            row = {**row, "keyword": keyword_override}
        api_key = settings.get("api_key", "")
        dfs_password = settings.get("dfs_password", "")

        # Re-init GSC if needed
        gsc_client = _get_runtime_gsc_client(settings, sb, user_id, job_id)

        branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
        full_brand_name = settings.get("full_brand_name", "").strip()
        if full_brand_name:
            import re as _re
            full_name_words = [w.lower() for w in _re.findall(r"[a-zA-Z]+", full_brand_name) if len(w) >= 3]
            branded_terms = list(set(branded_terms + full_name_words))
        branded_terms_input = settings.get("branded_terms_input", "").strip()
        if branded_terms_input:
            manual_terms = [t.strip().lower() for t in branded_terms_input.splitlines() if t.strip()]
            branded_terms = list(set(branded_terms + manual_terms))

        settings_with_key = {**settings, "api_key": api_key, "dfs_password": dfs_password}

        # Run the single row through full pipeline
        from routers.all_in_one import _process_single_row, _safe_gsc_auth_method, _update_job
        gsc_auth_method = _safe_gsc_auth_method(settings, settings.get("_gsc_credentials"), gsc_client)
        # Re-fetch brand profile if one was used on the original job
        brand_profile = {}
        brand_profile_id = settings.get("brand_profile_id")
        if brand_profile_id:
            try:
                bp_res = sb.table("brand_profiles").select("data").eq("id", brand_profile_id).eq("user_id", user_id).execute()
                if bp_res.data:
                    brand_profile = bp_res.data[0].get("data") or {}
            except Exception:
                pass
        result = _process_single_row(
            row=row,
            settings=settings_with_key,
            gsc_client=gsc_client,
            branded_terms=branded_terms,
            used_keywords=set(),
            
            sb=sb,
            job_id=job_id,
            row_num=row_index + 1,
            total_rows=len(rows),
            user_id=user_id,
            brand_profile=brand_profile,
            gsc_auth_method=gsc_auth_method,
        )

        # Update just this row's result in the existing results array
        res = sb.table("jobs").select("results").eq("id", job_id).eq("user_id", user_id).execute()
        current_results = res.data[0].get("results", []) if res.data else []

        # Extend if needed
        while len(current_results) <= row_index:
            current_results.append({})
        current_results[row_index] = result

        sb.table("jobs").update({
            "results": current_results,
            "current_step": f"Row {row_index + 1} complete.",
            "updated_at": "now()"
        }).eq("id", job_id).eq("user_id", user_id).execute()

    except Exception:
        sb.table("jobs").update({
            "current_step": f"Row {row_index + 1} failed: {traceback.format_exc(limit=1)[:120]}",
            "updated_at": "now()"
        }).eq("id", job_id).eq("user_id", user_id).execute()


@router.post("/{job_id}/rerun-rows")
def rerun_rows(
    job_id: str,
    body: MultiRerunRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Re-run multiple rows from a completed job."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res.data[0]
    rows = job.get("rows", [])
    settings = job.get("settings", {})

    valid_indices = [i for i in body.row_indices if 0 <= i < len(rows)]
    if not valid_indices:
        raise HTTPException(status_code=400, detail="No valid row indices provided")
    enforce_job_start(sb, user.id, "all-in-one", len(valid_indices), 50, exclude_job_id=job_id)
    enforce_rate_limit(sb, user.id, "all-in-one", "bulk-rerun", 10)

    execute_active_job_write(lambda: sb.table("jobs").update({
        "status": "running",
        "current_step": f"Re-running {len(valid_indices)} row(s)...",
        "updated_at": "now()",
    }).eq("id", job_id).eq("user_id", user.id).execute(), "all-in-one")

    background_tasks.add_task(_rerun_multiple_rows, job_id, valid_indices, rows, settings, sb, user.id)
    return {"status": "rerunning", "row_count": len(valid_indices)}


def _rerun_multiple_rows(job_id: str, row_indices: list, rows: list, settings: dict, sb, user_id: str = ""):
    """Run multiple rows sequentially, updating results in place."""
    try:
        settings = hydrate_job_settings(sb, user_id, settings)
    except Exception:
        (
            sb.table("jobs")
            .update({
                "status": "failed",
                "error": _CREDENTIALS_UNAVAILABLE_ERROR,
                "current_step": "Re-run failed: saved credentials are temporarily unavailable.",
                "updated_at": "now()",
            })
            .eq("id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        return
    _clear_credentials_runtime_error(sb, job_id, user_id)
    from routers.all_in_one import _process_single_row, _safe_gsc_auth_method, _update_job

    api_key = settings.get("api_key", "")
    dfs_password = settings.get("dfs_password", "")

    gsc_client = _get_runtime_gsc_client(settings, sb, user_id, job_id)
    gsc_auth_method = _safe_gsc_auth_method(settings, settings.get("_gsc_credentials"), gsc_client)

    import re as _re
    branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
    full_brand = settings.get("full_brand_name", "").strip()
    if full_brand:
        branded_terms = list(set(branded_terms + [w.lower() for w in _re.findall(r"[a-zA-Z]+", full_brand) if len(w) >= 3]))
    branded_input = settings.get("branded_terms_input", "").strip()
    if branded_input:
        branded_terms = list(set(branded_terms + [t.strip().lower() for t in branded_input.splitlines() if t.strip()]))

    # Fetch brand profile if original job used one
    brand_profile = {}
    brand_profile_id = settings.get("brand_profile_id")
    if brand_profile_id:
        try:
            bp_res = sb.table("brand_profiles").select("data").eq("id", brand_profile_id).eq("user_id", user_id).execute()
            if bp_res.data:
                brand_profile = bp_res.data[0].get("data") or {}
        except Exception:
            pass

    res = sb.table("jobs").select("results").eq("id", job_id).eq("user_id", user_id).execute()
    results = list(res.data[0].get("results") or []) if res.data else []
    # Pad results if needed
    while len(results) < len(rows):
        results.append({})

    failed = 0
    for n, row_index in enumerate(row_indices):
        _update_job(sb, job_id, user_id, {
            "current_step": f"Re-running row {row_index + 1} ({n + 1}/{len(row_indices)})...",
        })
        try:
            result = _process_single_row(
                row=rows[row_index],
                settings={**settings, "api_key": api_key, "dfs_password": dfs_password},
                gsc_client=gsc_client,
                branded_terms=branded_terms,
                used_keywords=set(),
                
                sb=sb,
                job_id=job_id,
                row_num=row_index + 1,
                total_rows=len(rows),
                user_id=user_id,
                brand_profile=brand_profile,
                gsc_auth_method=gsc_auth_method,
            )
            results[row_index] = result
            if result.get("error") or result.get("status") == "error":
                failed += 1
        except Exception as e:
            results[row_index] = {
                "url": rows[row_index].get("url", ""),
                "error": str(e),
                "status": "error",
                "gsc_auth_method": gsc_auth_method,
            }
            failed += 1

    sb.table("jobs").update({
        "status": "complete",
        "current_step": f"Re-run complete — {len(row_indices)} row(s) updated.",
        "results": results,
        "failed_rows": sum(1 for r in results if r.get("error") or r.get("status") == "error"),
        "updated_at": "now()",
    }).eq("id", job_id).eq("user_id", user_id).execute()


@router.post("/{job_id}/duplicate")
def duplicate_job(
    job_id: str,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Duplicate a job's settings and rows as a new draft job."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    original = res.data[0]
    enforce_rate_limit(sb, user.id, "all-in-one", "job-create", 10)

    new_job = {
        "user_id": user.id,
        "status": "draft",
        "name": f"{original.get('name', 'Job')} (copy)",
        "settings": strip_secret_fields(original.get("settings")),
        "rows": original.get("rows", []),
        "results": [],
        "total_rows": original.get("total_rows", 0),
        "completed_rows": 0,
        "current_step": "",
    }

    new_res = sb.table("jobs").insert(new_job).execute()
    if not new_res.data:
        raise HTTPException(status_code=500, detail="Failed to duplicate job")

    return {"job_id": new_res.data[0]["id"]}


# ── Section-level regeneration ─────────────────────────────────────────────────

class RerunSectionRequest(BaseModel):
    row_index: int
    section_name: str
    reviewer_instruction: str = ""


@router.post("/{job_id}/rerun-section")
def rerun_section(
    job_id: str,
    body: RerunSectionRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Regenerate a single page-copy section for one row without re-running the full pipeline."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res.data[0]
    results = job.get("results") or []
    row_index = body.row_index

    if row_index < 0 or row_index >= len(results):
        raise HTTPException(status_code=400, detail="Row index out of range")

    row_result = results[row_index]
    section_results = row_result.get("section_results") or {}

    if not section_results:
        raise HTTPException(status_code=400, detail="Row has no section_results — page copy was not generated")

    if body.section_name not in section_results:
        raise HTTPException(status_code=400, detail=f"Section '{body.section_name}' not found in row results")
    enforce_job_start(sb, user.id, "all-in-one", 1, 50, exclude_job_id=job_id)
    enforce_rate_limit(sb, user.id, "all-in-one", "section-rerun", 30)

    sb.table("jobs").update({
        "current_step": f"Regenerating section '{body.section_name}' for row {row_index + 1}...",
        "updated_at": "now()",
    }).eq("id", job_id).eq("user_id", user.id).execute()

    background_tasks.add_task(
        _rerun_single_section,
        job_id=job_id,
        row_index=row_index,
        section_name=body.section_name,
        job=job,
        user_id=user.id,
        sb=sb,
        reviewer_instruction=body.reviewer_instruction,
    )
    return {"status": "rerunning"}


def _rerun_single_section(
    job_id: str,
    row_index: int,
    section_name: str,
    job: dict,
    user_id: str,
    sb,
    reviewer_instruction: str = "",
):
    """
    Background task: regenerate one page-copy section for one row.

    Uses stored primary_keyword, h1, and section_results for context.
    Re-fetches SERP (one DFS call) for fresh PAA + AI Overview.
    Skips competitor scraping — too slow for a spot fix.
    Rebuilds _full_page, _word_count, and docx after updating the section.
    """
    import base64
    import traceback
    from utils.copy_gen import _build_section_prompt, DEFAULT_MODELS, PROVIDER_FN, sanitise
    from utils.templates import get_template
    from utils.dfs import get_serp_data
    from routers.all_in_one import _build_combined_docx, _split_forbidden_phrases

    try:
        settings = hydrate_job_settings(sb, user_id, job.get("settings") or {})
    except Exception:
        (
            sb.table("jobs")
            .update({
                "error": _CREDENTIALS_UNAVAILABLE_ERROR,
                "current_step": "Section re-run failed: saved credentials are temporarily unavailable.",
                "updated_at": "now()",
            })
            .eq("id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        return
    _clear_credentials_runtime_error(sb, job_id, user_id)
    rows = job.get("rows") or []
    results = list(job.get("results") or [])

    row_result = results[row_index]
    stored_row = rows[row_index] if row_index < len(rows) else {}

    try:
        # ── 1. Credentials ─────────────────────────────────────────────────────
        api_key = settings.get("api_key", "")
        dfs_password = settings.get("dfs_password", "")

        dfs_login = settings.get("dfs_login", "")
        provider = settings.get("provider", "Claude")
        model = row_result.get("model") or settings.get("model") or DEFAULT_MODELS.get(provider)
        brand_name = settings.get("brand_name", "")
        business_type = settings.get("business_type", "general")
        page_type = stored_row.get("page_type") or settings.get("page_type", "service")
        location_code = int(settings.get("location_code", 2840))

        # ── 2. Brand profile → append to client_brief ──────────────────────────
        client_brief = settings.get("client_brief", "") or ""
        brand_words_to_avoid = ""
        brand_profile_id = settings.get("brand_profile_id", "")
        if brand_profile_id:
            try:
                bp = sb.table("brand_profiles").select("data").eq("id", brand_profile_id).eq("user_id", user_id).execute()
                if bp.data:
                    bp_data = bp.data[0].get("data") or {}
                    parts = []
                    if bp_data.get("tone_of_voice"):
                        parts.append("Tone of voice: " + bp_data["tone_of_voice"])
                    if bp_data.get("key_messages"):
                        parts.append("Key messages: " + bp_data["key_messages"])
                    if bp_data.get("words_to_avoid"):
                        brand_words_to_avoid = bp_data["words_to_avoid"]
                        parts.append("Words to avoid: " + bp_data["words_to_avoid"])
                    if bp_data.get("guidelines"):
                        parts.append(bp_data["guidelines"])
                    if parts:
                        client_brief = (client_brief + "\n\n" + "\n".join(parts)).strip()
            except Exception:
                pass
        forbidden_phrase_text = ", ".join(_split_forbidden_phrases(
            settings.get("forbidden_phrases", ""),
            brand_words_to_avoid,
        ))

        # ── 3. Template and section definition ────────────────────────────────
        template_key = stored_row.get("template_key") or settings.get("template_key", "service_page")
        try:
            template = get_template(template_key)
        except ValueError:
            template = get_template("service_page")

        section_def = next((s for s in template["sections"] if s["name"] == section_name), None)
        if not section_def:
            sb.table("jobs").update({
                "current_step": f"Section '{section_name}' not found in template '{template_key}'.",
                "updated_at": "now()",
            }).eq("id", job_id).eq("user_id", user_id).execute()
            return

        # ── 4. Context from stored result ─────────────────────────────────────
        overall_primary_keyword = row_result.get("primary_keyword") or ""
        h1 = row_result.get("h1") or overall_primary_keyword
        section_results = dict(row_result.get("section_results") or {})
        section_rerun_notes = dict(row_result.get("section_rerun_notes") or {})
        strategy_brief = row_result.get("strategy_brief") or {}
        keyword_assignment = row_result.get("keyword_assignment") or {}
        section_assignment = keyword_assignment.get(section_name) or {}
        section_primary_keyword = section_assignment.get("primary") or overall_primary_keyword
        section_supporting_keyword = section_assignment.get("supporting") or overall_primary_keyword
        lsi_terms = (row_result.get("lsi_keywords") or {}).get(
            section_supporting_keyword or section_primary_keyword,
            [],
        )
        competitor_excerpts = (row_result.get("competitor_section_map") or {}).get(section_name, [])
        existing_notes = [
            str(note).strip()
            for note in section_rerun_notes.get(section_name, [])
            if str(note).strip()
        ]
        new_note = str(reviewer_instruction or "").strip()
        reviewer_corrections = (existing_notes + ([new_note] if new_note else []))[-5:]

        # previous_section_text: all sections before target in template order
        section_order = [s["name"] for s in template["sections"]]
        target_pos = section_order.index(section_name) if section_name in section_order else len(section_order)
        previous_section_text = "\n\n".join(
            section_results.get(s, "") for s in section_order[:target_pos] if section_results.get(s)
        )[-600:]  # cap so prompt stays lean

        # ── 5. Re-fetch SERP for fresh PAA + AI Overview ───────────────────────
        paa_questions = []
        ai_overview = ""
        if overall_primary_keyword and dfs_login and dfs_password:
            try:
                serp = get_serp_data(dfs_login, dfs_password, overall_primary_keyword, location_code)
                if serp.get("error"):
                    _update_job(sb, job_id, user_id, {
                        "current_step": "DataForSEO SERP refresh failed: " + str(serp["error"])[:120]
                    })
                paa_questions = serp.get("paa_items") or serp.get("paa") or []
                ai_overview = serp.get("ai_overview_raw") or serp.get("ai_overview") or ""
            except Exception as e:
                _update_job(sb, job_id, user_id, {
                    "current_step": "DataForSEO SERP refresh failed: " + str(e)[:120]
                })

        # ── 6. Build prompt and call AI ────────────────────────────────────────
        fn = PROVIDER_FN.get(provider)
        if not fn:
            raise ValueError(f"Unknown provider: {provider}")

        prompt = _build_section_prompt(
            section=section_def,
            primary_keyword=section_primary_keyword,
            supporting_keyword=section_supporting_keyword,
            lsi_keywords=lsi_terms,
            business_type=business_type,
            brand_name=brand_name,
            h1=h1,
            page_type=page_type,
            paa_questions=paa_questions if section_name == "faq" else [],
            competitor_excerpts=competitor_excerpts,
            client_brief=client_brief,
            previous_section_text=previous_section_text,
            client_existing_content="",
            ai_overview=ai_overview,
            forbidden_phrases=forbidden_phrase_text,
            reviewer_corrections=reviewer_corrections,
            strategy_brief=strategy_brief,
        )

        raw = fn(api_key, prompt, model=model)
        new_text = sanitise(raw, brand_name)

        # ── 7. Patch section, rebuild full_page + word_count ───────────────────
        section_results[section_name] = new_text
        if new_note:
            section_rerun_notes[section_name] = reviewer_corrections

        full_page = "\n\n".join(
            section_results.get(s, "") for s in section_order if section_results.get(s)
        )
        word_count = len(full_page.split())

        # ── 8. Regenerate docx ─────────────────────────────────────────────────
        docx_b64 = row_result.get("docx_b64")  # keep old if rebuild fails
        try:
            docx_bytes = _build_combined_docx(
                url=row_result.get("url", ""),
                h1=h1,
                primary_keyword=overall_primary_keyword,
                page_type=page_type,
                template=template,
                generated_title=row_result.get("generated_title"),
                generated_description=row_result.get("generated_description"),
                optimised_h1=row_result.get("optimised_h1"),
                faq_items=row_result.get("faq_items") or [],
                faq_schema=row_result.get("faq_schema"),
                section_results=section_results,
                word_count=word_count,
                competitor_urls=row_result.get("competitor_urls") or [],
                gen_meta=bool(row_result.get("generated_title")),
                gen_faqs=bool(row_result.get("faq_items")),
                gen_page_copy=True,
            )
            docx_b64 = base64.b64encode(docx_bytes).decode("utf-8")
        except Exception:
            pass  # keep old docx

        # ── 9. Re-fetch results to avoid race with other reruns, then patch ────
        fresh = sb.table("jobs").select("results").eq("id", job_id).eq("user_id", user_id).execute()
        current_results = list((fresh.data[0].get("results") or []) if fresh.data else results)

        while len(current_results) <= row_index:
            current_results.append({})

        current_results[row_index] = {
            **current_results[row_index],
            "section_results": section_results,
            "section_rerun_notes": section_rerun_notes,
            "word_count": word_count,
            "docx_b64": docx_b64,
        }

        sb.table("jobs").update({
            "results": current_results,
            "current_step": f"Section '{section_name}' regenerated for row {row_index + 1}.",
            "updated_at": "now()",
        }).eq("id", job_id).eq("user_id", user_id).execute()

    except Exception:
        sb.table("jobs").update({
            "current_step": f"Section rerun failed (row {row_index + 1}, '{section_name}'): {traceback.format_exc(limit=1)[:140]}",
            "updated_at": "now()",
        }).eq("id", job_id).eq("user_id", user_id).execute()
