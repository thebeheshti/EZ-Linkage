import copy
import ipaddress
import json
import os
import threading
import uuid
import webbrowser
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_from_directory, Response

from api_key_store import (
    get_api_key, get_saved_key, is_valid_variable_name, key_source, load_dotenv,
    missing_variable_message, set_api_key, variable_is_set,
)
from app_paths import APP_DIR, ASSETS_DIR, OUTPUT_DIR, SETTINGS_FILE
from blocking_agent import BLOCKING_RATIO_MAX, BLOCKING_RATIO_MIN
from core import (
    JOBS, JOBS_LOCK, MATCH_THRESHOLD, NON_MATCH_THRESHOLD, SOURCE_ID_FIELD,
    schema_mapping_worker, blocking_worker, comparators_worker, linkage_worker,
    class_counts, clamp_probability_threshold, csv_response,
    export_filename, export_results_csv,
    paged_results, parse_multipart_upload, query_truthy,
    read_csv_upload, reclassify_results, register_job, request_job_cancel,
    validate_columns,
)
from llm_client import (
    get_llm_call_count, get_token_counts, reset_llm_call_count, sampling_params_from_settings,
)
from matching_agent import validate_comparison_plan
from schema_agent import apply_shared_field_mapping, normalize_rows, transform_error_warnings


load_dotenv()

HOST = "127.0.0.1"
PORT = int(os.environ.get("LINKAGE_APP_PORT", "5000"))
ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}
# Sent by the app's own frontend on every state-changing request. A cross-site
# page cannot add a custom header without a CORS preflight, which this server never approves.
CSRF_HEADER = "X-EZ-Linkage"

app = Flask(__name__,
            template_folder=str(APP_DIR / "templates"),
            static_folder=str(APP_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB upload limit


@app.before_request
def _reject_foreign_requests():
    # Host check blocks DNS-rebinding; header and Origin checks block cross-site requests.
    hostname = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if hostname not in ALLOWED_HOSTNAMES:
        return jsonify({"error": "Requests must be addressed to the local app."}), 403
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    origin = request.headers.get("Origin")
    if origin and (urlsplit(origin).hostname or "").lower() not in ALLOWED_HOSTNAMES:
        return jsonify({"error": "Cross-site requests are not allowed."}), 403
    if request.headers.get(CSRF_HEADER) != "1":
        return jsonify({"error": f"Missing {CSRF_HEADER} header."}), 403
    return None


# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/assets/<path:filename>")
def serve_asset(filename):
    safe_root = ASSETS_DIR.resolve()
    requested = (ASSETS_DIR / filename).resolve()
    if not requested.is_file() or (requested.parent != safe_root and safe_root not in requested.parents):
        return jsonify({"error": "Asset not found."}), 404
    return send_from_directory(ASSETS_DIR, filename)


# ── Settings persistence ─────────────────────────────────────────────────────

def _read_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


@app.route("/api/defaults", methods=["GET"])
def api_get_defaults():
    return jsonify({
        "matchThreshold": MATCH_THRESHOLD,
        "nonMatchThreshold": NON_MATCH_THRESHOLD,
        "blockingRatioMin": BLOCKING_RATIO_MIN,
        "blockingRatioMax": BLOCKING_RATIO_MAX,
    })


def _write_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# Kept in settings.json but changed only through their own endpoints.
SERVER_MANAGED_SETTINGS = {"approvedRemoteEndpoints"}


def _migrate_api_key_out_of_settings() -> None:
    """Older versions kept the API key in settings.json; move it to .env."""
    data = _read_settings()
    if "aiApiKey" not in data:
        return
    legacy_key = str(data.pop("aiApiKey") or "")
    if legacy_key and not get_saved_key():
        set_api_key(legacy_key)
    _write_settings(data)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    settings = _read_settings()
    data = {key: value for key, value in settings.items() if key != "aiApiKey"}
    return jsonify({
        **data,
        "apiKeySaved": bool(get_saved_key()),
        "apiKeyVariableFound": variable_is_set(key_source(settings)[1]),
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({"error": "Settings must be a JSON object."}), 400
    try:
        if str(incoming.get("aiApiKey") or ""):
            set_api_key(str(incoming["aiApiKey"]))  # older frontends still send the key here
        updates = {k: v for k, v in incoming.items()
                   if k not in SERVER_MANAGED_SETTINGS | {"aiApiKey", "apiKeySaved", "apiKeyVariableFound"}}
        if "apiKeyVariable" in updates:
            updates["apiKeyVariable"] = str(updates["apiKeyVariable"] or "").strip()
        if updates.get("extraParams") is not None and not isinstance(updates["extraParams"], dict):
            return jsonify({"error": "Extra request parameters must be a JSON object, like {\"name\": value}."}), 400
        source, variable = key_source({**_read_settings(), **updates})
        if source == "variable" and not is_valid_variable_name(variable):
            return jsonify({"error": "Enter the name of the system variable that holds the API key, "
                                     "using letters, digits and underscores, for example MY_API_KEY."}), 400
        _write_settings({**_read_settings(), **updates})
    except (OSError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"saved": True})


@app.route("/api/api_key", methods=["POST"])
def api_save_api_key():
    """Save the AI API key to .env; an empty key removes it. The key is never sent back."""
    payload = request.get_json(silent=True) or {}
    try:
        set_api_key(str(payload.get("key") or ""))
    except (OSError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"apiKeySaved": bool(get_saved_key())})


@app.route("/api/api_key_variable", methods=["GET"])
def api_check_api_key_variable():
    """Whether a system variable is set, so Settings can say so. Its value is never returned."""
    name = (request.args.get("name") or "").strip()
    return jsonify({"valid": is_valid_variable_name(name), "found": variable_is_set(name)})


def _require_ai_settings(api_url: str, api_model: str) -> None:
    """The app has no built-in AI service, so the steps that use one need it set in Settings."""
    if not api_url or not api_model:
        raise ValueError("Enter the AI service's Base URL and model name in Settings first.")
    message = missing_variable_message(_read_settings())
    if message:
        raise ValueError(message)


# ── Remote AI endpoints ──────────────────────────────────────────────────────
# Agents send sample records to the AI endpoint. Before the first request to one
# that is not on this machine or a private network, the user has to approve it.

class EndpointApprovalRequired(Exception):
    def __init__(self, origin: str) -> None:
        super().__init__(f"Sending data to {origin} has not been approved.")
        self.origin = origin


def endpoint_origin(api_url: str) -> str:
    parts = urlsplit(api_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Not a valid http(s) AI endpoint: {api_url!r}")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def is_local_endpoint(api_url: str) -> bool:
    host = (urlsplit(api_url.strip()).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _require_endpoint_approval(api_url: str) -> None:
    if is_local_endpoint(api_url):
        return
    origin = endpoint_origin(api_url)
    if origin not in _read_settings().get("approvedRemoteEndpoints", []):
        raise EndpointApprovalRequired(origin)


def _approval_response(exc: EndpointApprovalRequired):
    return jsonify({"error": str(exc), "needs_endpoint_approval": True, "endpoint": exc.origin}), 409


@app.route("/api/approve_endpoint", methods=["POST"])
def api_approve_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        origin = endpoint_origin(str(payload.get("endpoint") or ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data = _read_settings()
    approved = list(data.get("approvedRemoteEndpoints", []))
    if origin not in approved:
        approved.append(origin)
    _write_settings({**data, "approvedRemoteEndpoints": approved})
    return jsonify({"approved": origin})


def _get_blocking_params() -> tuple[float, float]:
    data = _read_settings()
    try:
        return float(data.get("blockingRatioMin", BLOCKING_RATIO_MIN)), float(data.get("blockingRatioMax", BLOCKING_RATIO_MAX))
    except (TypeError, ValueError):
        return BLOCKING_RATIO_MIN, BLOCKING_RATIO_MAX


def _get_sampling_params() -> tuple[float | None, float | None, dict | None, str | None]:
    return sampling_params_from_settings(_read_settings())


# ── GET API ──────────────────────────────────────────────────────────────────

def _job_summary(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id,
        "file1_rows": job.get("file1_rows", 0),
        "file2_rows": job.get("file2_rows", 0),
        "mode": job.get("mode", "linkage"),
        "selected_fields": job.get("selected_fields", []),
        "blocking_pairs": job.get("blocking_pairs", 0),
        "predicted_matches": job.get("predicted_matches", 0),
        "predicted_uncertain": job.get("predicted_uncertain", 0),
        "predicted_non_matches": job.get("predicted_non_matches", 0),
        "warnings": job.get("warnings", []),
        "llm_usage": job.get("llm_usage", {}),
    }


@app.route("/api/export")
def api_export():
    job_id = request.args.get("job_id", "")
    export_type = request.args.get("type", "linked")
    classes = _selected_classes_from_args()
    include_linkage_details = query_truthy(request.args.get("include_linkage_details", "0"))
    if export_type not in {"file1", "file2", "linked"}:
        return jsonify({"error": "Unknown export type."}), 400
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        results = list(job.get("results", []))
        status = job.get("status")
        file1_rows = list(job.get("file1_source_rows", []))
        file2_rows = list(job.get("file2_source_rows", []))
        mode = job.get("mode", "linkage")
    if status != "done":
        return jsonify({"error": "Results are not ready yet."}), 202
    content = export_results_csv(
        results, export_type, classes, file1_rows, file2_rows,
        mode=mode, include_linkage_details=include_linkage_details,
    ).encode("utf-8-sig")
    return Response(
        content,
        status=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename(export_type, classes)}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        },
    )


@app.route("/api/progress")
def api_progress():
    job_id = request.args.get("job_id", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        snapshot = {
            **_job_summary(job_id, job),
            "status": job.get("status", "unknown"),
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "error": job.get("error", ""),
        }
    return jsonify(snapshot)


def _job_result(job_id: str, key: str, canceled_message: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        status = job.get("status", "running")
        if status in {"canceled", "cancel_requested"}:
            return jsonify({"error": canceled_message}), 410
        if status != "done":
            return jsonify({"status": status, "error": job.get("error", "")}), 202
        payload = job.get(key)
    return jsonify(payload)


@app.route("/api/blocking_result")
def api_blocking_result():
    return _job_result(request.args.get("job_id", ""), "blocking_plan", "Blocking canceled.")


@app.route("/api/comparators_result")
def api_comparators_result():
    return _job_result(request.args.get("job_id", ""), "comparison_plan", "Comparator selection canceled.")


@app.route("/api/schema_mapping_result")
def api_schema_mapping_result():
    return _job_result(request.args.get("job_id", ""), "schema_mapping_result", "Schema mapping canceled.")


@app.route("/api/results_page")
def api_results_page():
    job_id = request.args.get("job_id", "")
    try:
        page_number = int(request.args.get("page", "1") or "1")
        page_size = int(request.args.get("page_size", "100") or "100")
    except ValueError:
        return jsonify({"error": "page and page_size must be integers."}), 400
    search_query = (request.args.get("q", "") or "").strip()
    selected_class = request.args.get("class", "All")
    if selected_class not in {"All", "Match", "Uncertain", "Non-Match"}:
        selected_class = "All"
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        if job.get("status") != "done":
            return jsonify({"status": job.get("status", "running"), "error": job.get("error", "")}), 202
        results = list(job.get("results", []))
        summary = _job_summary(job_id, job)
        display_order = list(job.get("display_order", []))
    # Filter outside the lock so a large result set does not stall progress updates.
    page_payload = paged_results(results, page_number, page_size, search_query, selected_class)
    return jsonify({**summary, "display_order": display_order, **page_payload})


# ── POST API ─────────────────────────────────────────────────────────────────

def _llm_request_settings(uploads: dict[str, bytes]) -> tuple[str, str, str]:
    api_url = _decode(uploads.get("api_url", b"")).strip()
    api_model = _decode(uploads.get("api_model", b"")).strip()
    # The key is held server-side (api_key_store); anything the browser sends is ignored.
    return api_url, api_model, get_api_key(_read_settings())


def _start_job(kind: str, mode: str, target, args: tuple, extra: dict | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    register_job(job_id, {
        "kind": kind,
        "mode": mode,
        "status": "queued",
        "progress": 2,
        "message": f"Queued {kind.replace('_', ' ')} job...",
        "error": "",
        **(extra or {}),
    })
    threading.Thread(target=target, args=(job_id, *args), daemon=True).start()
    return job_id


@app.route("/api/schema_mapping", methods=["POST"])
def api_schema_mapping():
    try:
        _, mode, file1_bytes, file2_bytes, api_url, api_model, api_key, _, _ = _parse_upload_request()
        _require_ai_settings(api_url, api_model)
        _require_endpoint_approval(api_url)
        job_id = _start_job(
            "schema_mapping", mode, schema_mapping_worker,
            (mode, file1_bytes, file2_bytes, api_url, api_model, api_key, *_get_sampling_params()),
            {"schema_mapping_result": None},
        )
        return jsonify({"job_id": job_id, "status": "queued", "mode": mode})
    except EndpointApprovalRequired as exc:
        return _approval_response(exc)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _planning_request() -> tuple[str, list, list, str, tuple[str, str, str]]:
    uploads = _read_multipart()
    mode = _decode(uploads.get("mode", b"linkage"))
    if mode not in {"linkage", "dedupe"}:
        mode = "linkage"
    selected_fields = json.loads(_decode(uploads.get("selected_fields", b"[]")) or "[]")
    available_fields = json.loads(_decode(uploads.get("available_fields", b"[]")) or "[]")
    schema_mapping_job_id = _decode(uploads.get("schema_mapping_job_id", b"")).strip()
    if not selected_fields:
        raise ValueError("Please select at least one linkage variable.")
    if not schema_mapping_job_id:
        raise ValueError("Schema Mapping has not been run for these files yet; run Schema Mapping first.")
    return mode, selected_fields, available_fields, schema_mapping_job_id, _llm_request_settings(uploads)


@app.route("/api/blocking_rules", methods=["POST"])
def api_blocking_rules():
    try:
        mode, selected_fields, available_fields, schema_mapping_job_id, (api_url, api_model, api_key) = _planning_request()
        _require_ai_settings(api_url, api_model)
        _require_endpoint_approval(api_url)
        job_id = _start_job(
            "blocking", mode, blocking_worker,
            (selected_fields, available_fields, mode, schema_mapping_job_id, api_url, api_model, api_key,
             *_get_sampling_params(), *_get_blocking_params()),
            {"blocking_plan": None},
        )
        return jsonify({"job_id": job_id, "status": "queued"})
    except EndpointApprovalRequired as exc:
        return _approval_response(exc)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/comparators", methods=["POST"])
def api_comparators():
    try:
        mode, selected_fields, available_fields, schema_mapping_job_id, (api_url, api_model, api_key) = _planning_request()
        _require_ai_settings(api_url, api_model)
        _require_endpoint_approval(api_url)
        job_id = _start_job(
            "matching", mode, comparators_worker,
            (selected_fields, available_fields, mode, schema_mapping_job_id, api_url, api_model, api_key, *_get_sampling_params()),
            {"comparison_plan": None},
        )
        return jsonify({"job_id": job_id, "status": "queued"})
    except EndpointApprovalRequired as exc:
        return _approval_response(exc)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _validated_shared_plan(shared_field_plan: dict, plan1: dict, plan2: dict) -> dict:
    """The browser may add manual mappings; each must point at a field Schema Mapping produced."""
    shared_fields = shared_field_plan.get("shared_fields") if isinstance(shared_field_plan, dict) else None
    if not isinstance(shared_fields, dict):
        return {}
    fields1 = set((plan1.get("fields") or {}).keys())
    fields2 = set((plan2.get("fields") or {}).keys())
    for name, spec in shared_fields.items():
        if not isinstance(spec, dict) or spec.get("file1_field") not in fields1 or spec.get("file2_field") not in fields2:
            raise ValueError(f"Shared field {name!r} does not map to fields produced by Schema Mapping.")
    return shared_field_plan


@app.route("/api/link", methods=["POST"])
def api_link():
    try:
        uploads, mode, file1_bytes, file2_bytes, _, _, _, match_threshold, non_match_threshold = _parse_upload_request()
        selected_fields = json.loads(_decode(uploads.get("selected_fields", b"[]")) or "[]")
        if not selected_fields:
            raise ValueError("Please select at least one linkage variable.")
        available_fields = json.loads(_decode(uploads.get("available_fields", b"[]")) or "[]")
        comparison_plan = json.loads(_decode(uploads.get("comparison_plan", b"{}")) or "{}")
        comparison_plan = validate_comparison_plan(comparison_plan, selected_fields, available_fields)
        blocking_rules = json.loads(_decode(uploads.get("blocking_rules", b"[]")) or "[]")
        schema_mapping_job_id = _decode(uploads.get("schema_mapping_job_id", b"")).strip()
        with JOBS_LOCK:
            schema_mapping = (JOBS.get(schema_mapping_job_id) or {}).get("schema_mapping_result")
        if not schema_mapping:
            raise ValueError("The Schema Mapping results are no longer available; run Schema Mapping again.")
        # Preprocessing plans (which may contain AI-written code) come from the server, never the browser.
        plan1 = copy.deepcopy(schema_mapping["plan1"])
        plan2 = copy.deepcopy(schema_mapping["plan2"])
        shared_field_plan = json.loads(_decode(uploads.get("shared_field_plan", b"{}")) or "{}")
        shared_field_plan = _validated_shared_plan(shared_field_plan, plan1, plan2) if mode == "linkage" else {}

        original_rows1 = read_csv_upload(file1_bytes)
        original_rows2 = read_csv_upload(file2_bytes) if mode == "linkage" else []
        errors1: dict = {}
        errors2: dict = {}
        rows1, plan1 = normalize_rows(original_rows1, SOURCE_ID_FIELD, plan1, errors=errors1)
        rows2, plan2 = normalize_rows(original_rows2, SOURCE_ID_FIELD, plan2, errors=errors2) if mode == "linkage" else ([], plan2)
        if mode == "linkage" and shared_field_plan:
            rows1 = apply_shared_field_mapping(rows1, shared_field_plan, "file1")
            rows2 = apply_shared_field_mapping(rows2, shared_field_plan, "file2")
        validate_columns(rows1, [SOURCE_ID_FIELD], "File 1 after AI preprocessing")
        if mode == "linkage":
            validate_columns(rows2, [SOURCE_ID_FIELD], "File 2 after AI preprocessing")
        enable_em = uploads.get("enable_em") == b"1"
        warnings = transform_error_warnings(errors1, len(original_rows1), "File 1") + transform_error_warnings(errors2, len(original_rows2), "File 2")

        OUTPUT_DIR.mkdir(exist_ok=True)
        job_id = _start_job(
            "linkage", mode, linkage_worker,
            (rows1, rows2, original_rows1, original_rows2, enable_em, mode, selected_fields,
             blocking_rules, comparison_plan, match_threshold, non_match_threshold),
            {
                "results": [],
                "file1_rows": len(original_rows1),
                "file2_rows": len(original_rows2),
                "shared_field_plan": shared_field_plan,
                "selected_fields": selected_fields,
                "comparison_plan": comparison_plan,
                "blocking_rules": blocking_rules,
                "match_threshold": match_threshold,
                "non_match_threshold": non_match_threshold,
                "display_order": schema_mapping.get("display_order", []),
                "warnings": warnings,
                "blocking_pairs": 0,
                **class_counts([]),
            },
        )
        return jsonify({"job_id": job_id, "file1_rows": len(original_rows1), "file2_rows": len(original_rows2), "status": "queued"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    try:
        payload = request.get_json(silent=True) or {}
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("Job id is required.")
        if not request_job_cancel(job_id):
            raise ValueError("Job not found or cannot be canceled.")
        return jsonify({"ok": True, "job_id": job_id, "status": "cancel_requested"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/labels", methods=["POST"])
def api_labels():
    try:
        payload = request.get_json(silent=True) or {}
        job_id = payload.get("job_id", "")
        labels = payload.get("labels", {})
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                raise ValueError("Job not found.")
            for row in job["results"]:
                label = labels.get(row["pair_id"])
                if label in {"Match", "Uncertain", "Non-Match"}:
                    row["predicted_class"] = label
            counts = class_counts(job["results"])
            job.update(counts)
            results = list(job["results"])
        (OUTPUT_DIR / f"linkage_results_{job_id}.csv").write_text(csv_response(results), encoding="utf-8-sig")
        return jsonify({"saved": True, **counts})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/reclassify", methods=["POST"])
def api_reclassify():
    try:
        payload = request.get_json(silent=True) or {}
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("Job id is required.")
        match_threshold = clamp_probability_threshold(payload.get("match_threshold"), MATCH_THRESHOLD)
        non_match_threshold = clamp_probability_threshold(payload.get("non_match_threshold"), NON_MATCH_THRESHOLD)
        if non_match_threshold >= match_threshold:
            raise ValueError("Non-Match Threshold must be lower than Match Threshold.")
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                raise ValueError("Job not found.")
            reclassify_results(job["results"], match_threshold, non_match_threshold)
            counts = class_counts(job["results"])
            job["match_threshold"] = match_threshold
            job["non_match_threshold"] = non_match_threshold
            job.update(counts)
            results = list(job["results"])
        (OUTPUT_DIR / f"linkage_results_{job_id}.csv").write_text(csv_response(results), encoding="utf-8-sig")
        return jsonify({"reclassified": True, "match_threshold": match_threshold, "non_match_threshold": non_match_threshold, **counts})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/llm_call_count")
def api_llm_call_count():
    """Process-wide LLM totals (per-job totals are in /api/progress as llm_usage)."""
    count = get_llm_call_count()
    input_tokens, output_tokens = get_token_counts()
    if request.args.get("reset", "0") == "1":
        reset_llm_call_count()
    return jsonify({"llm_calls": count, "input_tokens": input_tokens, "output_tokens": output_tokens})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _decode(value: bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="ignore")


def _read_multipart() -> dict[str, bytes]:
    return parse_multipart_upload(request.content_type or "", request.get_data())


def _parse_upload_request():
    uploads = _read_multipart()
    mode = _decode(uploads.get("mode", b"linkage"))
    if mode not in {"linkage", "dedupe"}:
        mode = "linkage"
    file1_bytes = uploads.get("file1")
    file2_bytes = uploads.get("file2")
    if not file1_bytes:
        raise ValueError("Please upload File 1.")
    if mode == "linkage" and not file2_bytes:
        raise ValueError("Please upload File 2.")
    api_url, api_model, api_key = _llm_request_settings(uploads)
    match_threshold = clamp_probability_threshold(uploads.get("match_threshold"), MATCH_THRESHOLD)
    non_match_threshold = clamp_probability_threshold(uploads.get("non_match_threshold"), NON_MATCH_THRESHOLD)
    if non_match_threshold >= match_threshold:
        raise ValueError("Non-Match Threshold must be lower than Match Threshold.")
    return uploads, mode, file1_bytes, file2_bytes, api_url, api_model, api_key, match_threshold, non_match_threshold


def _selected_classes_from_args() -> set[str]:
    raw_values = request.args.getlist("classes")
    if not raw_values:
        return {"Match", "Uncertain", "Non-Match"}
    selected: set[str] = set()
    for raw in raw_values:
        for value in raw.split(","):
            value = value.strip()
            if value in {"Match", "Uncertain", "Non-Match"}:
                selected.add(value)
    return selected or {"Match", "Uncertain", "Non-Match"}


# ── Entry point ──────────────────────────────────────────────────────────────

def _find_free_port() -> int:
    import socket
    for candidate in range(PORT, PORT + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, candidate))
                return candidate
            except OSError:
                continue
    raise OSError(f"Could not bind to any port from {PORT} to {PORT + 19}.")


def _preload_ai_libraries() -> None:
    """Load the AI and linkage libraries in the background, so the window opens first and the
    first step does not wait for them."""
    import langchain.agents  # noqa: F401
    import langchain_openai  # noqa: F401
    import splink  # noqa: F401


REQUIRED_SPLINK = "5"


def _check_splink_version() -> None:
    """Stop at startup with a clear message, rather than at the last step of a run."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("splink")
    except PackageNotFoundError:
        installed = ""
    if installed.split(".")[0] != REQUIRED_SPLINK:
        found = f"Splink {installed} is installed" if installed else "Splink is not installed"
        raise SystemExit(
            f"EZ Linkage needs Splink {REQUIRED_SPLINK}, but {found}.\n"
            f'Install the right version with:  pip install "splink>={REQUIRED_SPLINK}.0,<{int(REQUIRED_SPLINK) + 1}"\n'
            "or install everything the app needs with:  pip install -r requirements.txt"
        )


def main() -> None:
    _check_splink_version()
    _migrate_api_key_out_of_settings()
    OUTPUT_DIR.mkdir(exist_ok=True)

    port = _find_free_port()
    url = f"http://{HOST}:{port}"

    print(f"EZ Linkage running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Thread(target=_preload_ai_libraries, daemon=True).start()
    if os.environ.get("LINKAGE_APP_OPEN_BROWSER") == "1":
        threading.Timer(0.75, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
