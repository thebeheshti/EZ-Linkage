import csv
import contextlib
import io
import logging
import threading
import time
import traceback
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Callable

from agent_runtime import AgentCanceled
from app_paths import LOGS_DIR, OUTPUT_DIR
from blocking_agent import build_blocking_plan, validate_blocking_plan
from comparators import build_splink_comparison, comparator_gamma_max
from linkage_inputs import (
    SOURCE_ID_FIELD,
    build_splink_inputs,
    comparison_column_for_field,
    record_columns,
    records_table,
)
from llm_client import new_usage
from matching_agent import build_comparison_plan, validate_comparison_plan
from schema_agent import (
    aligned_schema_pairs, apply_preprocessing_plan, apply_shared_field_mapping,
    available_field_options, build_aligned_schema_cleaning_plan, build_joint_preprocessing_plan,
    build_preprocessing_plan, profile_fields, shared_mapping_from_joint_plan,
    split_joint_preprocessing_plan, transform_error_warnings, validate_plan,
)

LOGS_DIR.mkdir(exist_ok=True)
_log_handler = TimedRotatingFileHandler(
    filename=str(LOGS_DIR / "ezlinkage.log"),
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_log = logging.getLogger("ezlinkage")
_log.setLevel(logging.WARNING)
_log.addHandler(_log_handler)

MATCH_THRESHOLD = 0.95
NON_MATCH_THRESHOLD = 0.65

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
# Finished jobs hold whole datasets and every scored pair, so they are not kept forever.
JOB_TTL_SECONDS = 6 * 60 * 60
MAX_FINISHED_JOBS = 20
FINISHED_STATUSES = {"done", "error", "canceled"}

def register_job(job_id: str, job: dict[str, Any]) -> None:
    """Add a job and drop expired or surplus finished ones."""
    now = time.time()
    job.setdefault("created_at", now)
    job.setdefault("llm_usage", new_usage())
    with JOBS_LOCK:
        finished = sorted(
            ((jid, j) for jid, j in JOBS.items() if j.get("status") in FINISHED_STATUSES),
            key=lambda item: item[1].get("created_at", 0),
        )
        for jid, j in finished:
            if now - j.get("created_at", now) > JOB_TTL_SECONDS:
                del JOBS[jid]
        finished = [(jid, j) for jid, j in finished if jid in JOBS]
        for jid, _ in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            del JOBS[jid]
        JOBS[job_id] = job

def job_usage(job_id: str) -> dict[str, int] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return job.get("llm_usage") if job else None

def parse_multipart_upload(content_type: str, body: bytes) -> dict[str, bytes]:
    import re
    boundary_match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not boundary_match:
        raise ValueError("The upload request is missing a multipart boundary.")
    boundary = ("--" + boundary_match.group(1)).encode("utf-8")
    uploads: dict[str, bytes] = {}
    for part in body.split(boundary):
        part = part.strip()
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip()
        header_blob, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        uploads[name_match.group(1)] = payload
    return uploads

def read_csv_upload(raw: bytes) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [{(key or "").strip(): (value or "").strip() for key, value in row.items()} for row in reader]

def validate_columns(rows: list[dict[str, str]], expected: list[str], label: str) -> None:
    if not rows:
        raise ValueError(f"{label} is empty or missing a header row.")
    missing = [column for column in expected if column not in rows[0]]
    if missing:
        raise ValueError(f"{label} is missing required column(s): {', '.join(missing)}")

def assign_class(probability: float, match_threshold: float = MATCH_THRESHOLD, non_match_threshold: float = NON_MATCH_THRESHOLD) -> str:
    if probability >= match_threshold:
        return "Match"
    if probability < non_match_threshold:
        return "Non-Match"
    return "Uncertain"

def splink_settings(
    link_type: str,
    selected_fields: list[str],
    available_columns: list[str],
    blocking_rules: list[dict[str, Any]],
    comparison_plan: dict[str, Any],
) -> Any:
    from splink import SettingsCreator
    from splink.blocking_rule_library import CustomRule

    blocking_plan = validate_blocking_plan({"blocking_rules": blocking_rules}, selected_fields, available_columns=available_columns)
    comparisons = [
        build_splink_comparison(field, spec["comparator"], spec.get("thresholds"))
        for field in comparison_plan.get("selected_fields", selected_fields)
        if (spec := comparison_plan["comparisons"].get(field))
    ]
    if not comparisons:
        raise ValueError("The matching plan has no active comparisons.")
    return SettingsCreator(
        unique_id_column_name="record_id",
        source_dataset_column_name="source_dataset",
        link_type=link_type,
        retain_matching_columns=True,
        retain_intermediate_calculation_columns=True,
        blocking_rules_to_generate_predictions=[CustomRule(rule["sql"], sql_dialect="duckdb") for rule in blocking_plan["blocking_rules"]],
        comparisons=comparisons,
    ), blocking_plan["blocking_rules"]

def resolve_gamma_col_for_field(field: str, gamma_cols_lower: dict[str, str]) -> str | None:
    """Find the gamma column for a field (case-insensitive; Splink may turn spaces into underscores)."""
    import re
    space_normalized = re.sub(r"\s+", "_", field)
    candidates = [f"gamma_{comparison_column_for_field(field)}", f"gamma_{field}", f"gamma_{space_normalized}"]
    for candidate in dict.fromkeys(candidates):
        actual = gamma_cols_lower.get(candidate.lower())
        if actual is not None:
            return actual
    return None

def dynamic_field_scores(
    row: dict[str, Any],
    selected_fields: list[str],
    gamma_max_by_field: dict[str, int],
    gamma_col_for_field: dict[str, str | None],
) -> dict[str, float]:
    """Per-field agreement in [0, 1]: the pair's comparison level over the field's top level. NULLs score 0."""
    scores: dict[str, float] = {}
    for field in selected_fields:
        gamma_key = gamma_col_for_field.get(field)
        try:
            gamma = float(row.get(gamma_key)) if gamma_key else 0.0
        except (TypeError, ValueError):
            gamma = 0.0
        scores[field] = round(max(0.0, min(1.0, gamma / gamma_max_by_field.get(field, 1))), 3)
    return scores

def report_progress(progress_callback: Any, percent: int, message: str) -> None:
    if progress_callback:
        progress_callback(percent, message)

def _check_canceled(cancel_callback: Callable[[], bool] | None) -> None:
    if cancel_callback and cancel_callback():
        raise AgentCanceled("Linkage canceled.")

def run_linkage(
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]] | None,
    enable_em: bool = False,
    progress_callback: Any = None,
    cancel_callback: Any = None,
    mode: str = "linkage",
    selected_fields: list[str] | None = None,
    blocking_rules: list[dict[str, Any]] | None = None,
    comparison_plan: dict[str, Any] | None = None,
    match_threshold: float = MATCH_THRESHOLD,
    non_match_threshold: float = NON_MATCH_THRESHOLD,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Score candidate pairs with Splink. Returns (results, candidate pair count, training warnings)."""
    from splink import DuckDBAPI, Linker
    from splink.blocking_rule_library import CustomRule

    _check_canceled(cancel_callback)
    report_progress(progress_callback, 12, "Preparing fields for record matching...")
    link_type = "dedupe_only" if mode == "dedupe" else "link_only"
    comparison_plan = validate_comparison_plan(comparison_plan or {"comparisons": {}}, selected_fields or [])
    active_fields = comparison_plan["selected_fields"]
    records, prepared_by_id, raw_by_id = build_splink_inputs(rows1, rows2, mode, selected_fields=active_fields)
    report_progress(progress_callback, 20, "Initializing matching engine...")
    settings, validated_rules = splink_settings(link_type, active_fields, record_columns(records), blocking_rules or [], comparison_plan)
    # Splink 5 takes each file as a registered table; its name fills the source_dataset column.
    db_api = DuckDBAPI()
    columns = record_columns(records)
    sources = list(dict.fromkeys(record["source_dataset"] for record in records))
    inputs = [
        db_api.register(
            records_table([record for record in records if record["source_dataset"] == source], columns),
            dataset_display_name=source,
        )
        for source in sources
    ]
    # log_level=None: Splink must not attach its own log handler inside the app.
    linker = Linker(inputs if len(inputs) > 1 else inputs[0], settings, log_level=None)
    training_rules = [CustomRule(rule["sql"], sql_dialect="duckdb") for rule in validated_rules[:3]]
    warnings: list[str] = []

    def training_step(description: str, step: Callable[[], Any], required: bool) -> None:
        try:
            step()
        except Exception as exc:
            _log.warning("Splink %s failed:\n%s", description, traceback.format_exc())
            if required:
                raise RuntimeError(f"Splink could not {description}: {exc}") from exc
            warnings.append(f"Splink could not {description} ({exc}); defaults were used for this step.")

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _check_canceled(cancel_callback)
        report_progress(progress_callback, 30, "Calibrating baseline match probability...")
        training_step(
            "estimate the baseline match probability",
            lambda: linker.training.estimate_probability_two_random_records_match(training_rules, recall=0.80),
            required=False,
        )
        _check_canceled(cancel_callback)
        report_progress(progress_callback, 42, "Calibrating field comparison weights...")
        # Without u-values every comparison weight is a placeholder, so this step must succeed.
        training_step(
            "estimate u-values by random sampling",
            lambda: linker.training.estimate_u_using_random_sampling(max_pairs=250000),
            required=True,
        )
        if enable_em:
            for index, (rule, training_rule) in enumerate(zip(validated_rules[:3], training_rules)):
                _check_canceled(cancel_callback)
                report_progress(progress_callback, 52 + index * 10, f"Training matching model on {rule['label']}...")
                training_step(
                    f"train m-values with EM on rule '{rule['label']}'",
                    lambda training_rule=training_rule: linker.training.estimate_parameters_using_expectation_maximisation(training_rule),
                    required=False,
                )
        else:
            warnings.append('Match weights are general defaults because "Expectation Maximization" was off; turn it on for probabilities fitted to these files.')
        _check_canceled(cancel_callback)
        report_progress(progress_callback, 80, "Scoring all candidate pairs...")
        predictions = linker.inference.predict(threshold_match_weight=None).as_record_list()

    n_blocking_pairs = len(predictions)
    if not predictions:
        return [], n_blocking_pairs, warnings

    report_progress(progress_callback, 93, "Preparing results for review...")
    gamma_cols_lower = {col.lower(): col for col in predictions[0] if col.lower().startswith("gamma_")}
    gamma_col_for_field = {field: resolve_gamma_col_for_field(field, gamma_cols_lower) for field in active_fields}
    gamma_max_by_field = {
        field: comparator_gamma_max(spec["comparator"], spec.get("thresholds"))
        for field, spec in comparison_plan["comparisons"].items()
    }
    results = []
    for result_index, row in enumerate(predictions, start=1):
        if result_index % 1000 == 1:
            _check_canceled(cancel_callback)
        left_id = str(row.get("record_id_l", ""))
        right_id = str(row.get("record_id_r", ""))
        if mode == "linkage" and left_id.startswith("file2::") and right_id.startswith("file1::"):
            left_id, right_id = right_id, left_id
        expected_right = "file2::" if mode == "linkage" else "file1::"
        if not left_id.startswith("file1::") or not right_id.startswith(expected_right):
            continue
        left = prepared_by_id[left_id]
        right = prepared_by_id[right_id]
        field_scores = dynamic_field_scores(row, active_fields, gamma_max_by_field, gamma_col_for_field)
        probability = max(0.0, min(1.0, float(row.get("match_probability", 0) or 0)))
        results.append({
            "pair_id": f"PAIR{result_index:06d}",
            "file1_row_index": left.row_index,
            "file2_row_index": right.row_index,
            "mode": mode,
            "posterior_probability": round(probability, 4),
            "predicted_class": assign_class(probability, match_threshold=match_threshold, non_match_threshold=non_match_threshold),
            "blocking_rules": f"Splink match key {row.get('match_key', '')}",
            "file1": {**raw_by_id[left_id], **left.extras},
            "file2": {**raw_by_id[right_id], **right.extras},
            "linkage_backend": "Splink with EM" if enable_em else "Splink",
            "field_scores": field_scores,
            **field_scores,
        })
    results.sort(key=lambda item: item["posterior_probability"], reverse=True)
    return results, n_blocking_pairs, warnings

def csv_response(results: list[dict[str, Any]], include_linkage_details: bool = True) -> str:
    file1_fields = sorted({
        field
        for row in results
        for field in (row.get("file1") or {}).keys()
        if field and not str(field).startswith("__")
    }, key=str.lower)
    file2_fields = sorted({
        field
        for row in results
        for field in (row.get("file2") or {}).keys()
        if field and not str(field).startswith("__")
    }, key=str.lower)
    score_fields = sorted({
        field
        for row in results
        for field in (row.get("field_scores") or {}).keys()
        if field and not str(field).startswith("__")
    }, key=str.lower)
    headers = [
        "file1_record_number", "file2_record_number", "linkage_status",
    ]
    headers.extend(f"file1_{field}" for field in file1_fields)
    headers.extend(f"file2_{field}" for field in file2_fields)
    if include_linkage_details:
        headers.append("posterior_probability")
        headers.extend(f"score_{field}" for field in score_fields)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for row in results:
        output = {
            "file1_record_number": row.get("file1_row_index", ""),
            "file2_record_number": row.get("file2_row_index", ""),
            "linkage_status": row["predicted_class"],
        }
        output.update({f"file1_{field}": (row.get("file1") or {}).get(field, "") for field in file1_fields})
        output.update({f"file2_{field}": (row.get("file2") or {}).get(field, "") for field in file2_fields})
        if include_linkage_details:
            output["posterior_probability"] = row["posterior_probability"]
            output.update({f"score_{field}": (row.get("field_scores") or {}).get(field, "") for field in score_fields})
        writer.writerow(output)
    return buffer.getvalue()

def export_filename(export_type: str, classes: set[str]) -> str:
    class_part = "_".join(sorted(label.lower().replace("-", "_") for label in classes))
    return f"{export_type}_{class_part}.csv"

def export_file_records_csv(
    original_rows: list[dict[str, str]],
    results: list[dict[str, Any]],
    source: str,
    classes: set[str],
    mode: str = "linkage",
) -> str:
    original_headers = list(original_rows[0].keys()) if original_rows else []
    # Record number in the source file, counting from 1 after the header row.
    row_id_header = f"{source}_record_number"
    headers = [row_id_header] + original_headers + ["linkage_status"]
    statuses = record_level_status(results, source)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for row_index, original_row in enumerate(original_rows, start=1):
        record_id = row_index
        status = statuses.get(record_id, {
            "predicted_class": "Non-Match",
            "linked_id": "",
            "pair_id": "",
            "posterior_probability": "",
        })
        if status["predicted_class"] not in classes:
            continue
        output = {column: original_row.get(column, "") for column in original_headers}
        output[row_id_header] = row_index
        output["linkage_status"] = status["predicted_class"]
        writer.writerow(output)
    return buffer.getvalue()

def export_results_csv(
    results: list[dict[str, Any]],
    export_type: str,
    classes: set[str],
    file1_rows: list[dict[str, str]] | None = None,
    file2_rows: list[dict[str, str]] | None = None,
    mode: str = "linkage",
    include_linkage_details: bool = True,
) -> str:
    filtered = [row for row in results if row.get("predicted_class") in classes]

    if export_type == "file1":
        return export_file_records_csv(file1_rows or [], results, "file1", classes, mode=mode)

    if export_type == "file2":
        return export_file_records_csv(file2_rows or [], results, "file2", classes)

    if export_type == "linked":
        return csv_response(filtered, include_linkage_details=include_linkage_details)

    raise ValueError("Unknown export type.")

def query_truthy(value: str) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}

def clamp_probability_threshold(value: str | bytes | None, default: float) -> float:
    raw = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value or "").strip()
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))

def class_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "predicted_matches": sum(1 for row in results if row["predicted_class"] == "Match"),
        "predicted_uncertain": sum(1 for row in results if row["predicted_class"] == "Uncertain"),
        "predicted_non_matches": sum(1 for row in results if row["predicted_class"] == "Non-Match"),
    }

def reclassify_results(
    results: list[dict[str, Any]],
    match_threshold: float,
    non_match_threshold: float,
) -> None:
    for row in results:
        probability = float(row.get("posterior_probability", 0) or 0)
        row["predicted_class"] = assign_class(
            probability,
            match_threshold=match_threshold,
            non_match_threshold=non_match_threshold,
        )

def result_matches_query(row: dict[str, Any], query: str, selected_class: str) -> bool:
    if selected_class != "All" and row.get("predicted_class") != selected_class:
        return False
    if not query:
        return True
    query = query.lower()
    searchable_parts = [
        row.get("pair_id", ""),
        row.get("file1_row_index", ""),
        row.get("file2_row_index", ""),
        row.get("predicted_class", ""),
    ]
    searchable_parts.extend(str(value) for value in row.get("file1", {}).values())
    searchable_parts.extend(str(value) for value in row.get("file2", {}).values())
    return query in " ".join(str(part).lower() for part in searchable_parts)

def paged_results(results: list[dict[str, Any]], page: int, page_size: int, query: str, selected_class: str) -> dict[str, Any]:
    page = max(1, page)
    page_size = max(10, min(5000, page_size))
    filtered = [row for row in results if result_matches_query(row, query, selected_class)]
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "results": filtered[start:end],
        "total_results": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }

def project_preview_rows(rows: list[dict[str, str]], fields: list[str], limit: int = 5) -> list[dict[str, str]]:
    if not fields:
        return []
    return [{field: row.get(field, "") for field in fields} for row in rows[:limit]]

def preview_sections(
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]],
    fields1: list[str],
    fields2: list[str],
    mode: str,
    limit: int = 5,
) -> dict[str, Any]:
    return {
        "primary": {
            "title": "First 5 standardized records",
            "rows1": project_preview_rows(rows1, fields1, limit),
            "rows2": project_preview_rows(rows2, fields2, limit) if mode == "linkage" else [],
        }
    }

def field_selection_warnings(
    available_fields: list[dict[str, Any]],
    field_profiles: dict[str, dict[str, dict[str, Any]]],
    mode: str,
) -> list[str]:
    warnings: list[str] = []
    profiles1 = field_profiles.get("file1", {})
    profiles2 = field_profiles.get("file2", {})
    for option in available_fields:
        field = option["field"]
        label = option.get("label") or field
        profile1 = profiles1.get(field, {})
        profile2 = profiles2.get(field, {})
        if profile1.get("non_missing_rate", 1) < 0.25:
            warnings.append(f"{label} has low File 1 coverage ({int(profile1['non_missing_rate'] * 100)}%).")
        if mode == "linkage" and profile2.get("non_missing_rate", 1) < 0.25:
            warnings.append(f"{label} has low File 2 coverage ({int(profile2['non_missing_rate'] * 100)}%).")
    return warnings[:8]

def record_level_status(results: list[dict[str, Any]], source: str) -> dict[int, dict[str, Any]]:
    """Best classification per source row, with the row it was linked to."""
    id_key = "file1_row_index" if source == "file1" else "file2_row_index"
    other_key = "file2_row_index" if source == "file1" else "file1_row_index"
    status_by_id: dict[int, dict[str, Any]] = {}
    priority = {"Non-Match": 0, "Uncertain": 1, "Match": 2}
    for row in results:
        record_refs = [(row.get(id_key), row.get(other_key, ""))]
        if source == "file1" and row.get("mode") == "dedupe":
            record_refs = [
                (row.get("file1_row_index"), row.get("file2_row_index", "")),
                (row.get("file2_row_index"), row.get("file1_row_index", "")),
            ]
        row_class = row.get("predicted_class", "Non-Match")
        for record_id, linked_id in record_refs:
            if record_id is None:
                continue
            current = status_by_id.get(record_id)
            if current is None or priority.get(row_class, 0) > priority.get(current["predicted_class"], 0):
                status_by_id[record_id] = {
                    "predicted_class": row_class,
                    "linked_id": linked_id,
                    "pair_id": row.get("pair_id", ""),
                    "posterior_probability": row.get("posterior_probability", ""),
                }
    return status_by_id

# ── Job state ────────────────────────────────────────────────────────────────

def set_job_progress(job_id: str, percent: int, message: str, status: str = "running") -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if job.get("status") in {"canceled", "cancel_requested"} and status != "canceled":
            return
        job["status"] = status
        job["progress"] = max(0, min(100, percent))
        job["message"] = message

def request_job_cancel(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") in FINISHED_STATUSES:
            return False
        job["status"] = "cancel_requested"
        job["message"] = "Stopping..."
        return True

def is_job_cancel_requested(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("status") == "cancel_requested")

def cancel_job(job_id: str, message: str = "Canceled.") -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update({"status": "canceled", "progress": 100, "message": message})

def finish_job(job_id: str, payload: dict[str, Any], message: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") in {"canceled", "cancel_requested"}:
            return
        job.update({"status": "done", "progress": 100, "message": message, **payload})

def fail_job(job_id: str, error: Exception, failure_message: str = "The linkage run did not complete.") -> None:
    if isinstance(error, AgentCanceled):
        cancel_job(job_id)
        return
    _log.error("Job %s failed (%s):\n%s", job_id, failure_message, traceback.format_exc())
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update({"status": "error", "progress": 100, "message": str(error), "error": str(error)})

def _cancel_check(job_id: str) -> Callable[[], bool]:
    return lambda: is_job_cancel_requested(job_id)

def _progress(job_id: str, start: int = 0, span: int = 100) -> Callable[[int, str], None]:
    return lambda percent, message: set_job_progress(job_id, start + int(max(0, min(100, percent)) / 100 * span), message)

# ── Workers ──────────────────────────────────────────────────────────────────

def linkage_worker(
    job_id: str,
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]],
    original_rows1: list[dict[str, str]],
    original_rows2: list[dict[str, str]],
    enable_em: bool,
    mode: str,
    selected_fields: list[str] | None = None,
    blocking_rules: list[dict[str, Any]] | None = None,
    comparison_plan: dict[str, Any] | None = None,
    match_threshold: float = MATCH_THRESHOLD,
    non_match_threshold: float = NON_MATCH_THRESHOLD,
) -> None:
    try:
        set_job_progress(job_id, 8, "Validating inputs...")
        results, n_blocking_pairs, warnings = run_linkage(
            rows1, rows2,
            enable_em=enable_em,
            mode=mode,
            selected_fields=selected_fields,
            blocking_rules=blocking_rules,
            comparison_plan=comparison_plan,
            match_threshold=match_threshold,
            non_match_threshold=non_match_threshold,
            progress_callback=_progress(job_id),
            cancel_callback=_cancel_check(job_id),
        )
        _check_canceled(_cancel_check(job_id))
        set_job_progress(job_id, 97, "Generating downloadable results file...")
        OUTPUT_DIR.mkdir(exist_ok=True)
        (OUTPUT_DIR / f"linkage_results_{job_id}.csv").write_text(csv_response(results), encoding="utf-8-sig")
        with JOBS_LOCK:
            job = JOBS.get(job_id) or {}
            warnings = [*job.get("warnings", []), *warnings]
        finish_job(job_id, {
            "results": results,
            "file1_source_rows": original_rows1,
            "file2_source_rows": original_rows2,
            "file1_rows": len(original_rows1),
            "file2_rows": len(original_rows2),
            "blocking_pairs": n_blocking_pairs,
            "warnings": warnings,
            **class_counts(results),
        }, "Linkage complete.")
    except Exception as exc:
        fail_job(job_id, exc)

def schema_mapping_worker(
    job_id: str,
    mode: str,
    file1_item: bytes,
    file2_item: bytes | None,
    api_url: str,
    api_model: str,
    api_key: str,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> None:
    cancel_check = _cancel_check(job_id)
    llm_kwargs = dict(
        api_url=api_url, model=api_model, api_key=api_key,
        temperature=temperature, top_p=top_p, extra_body=extra_body,
        reasoning_effort=reasoning_effort,
        cancel_callback=cancel_check, usage=job_usage(job_id),
    )
    try:
        set_job_progress(job_id, 6, "Reading File 1...")
        original_rows1 = read_csv_upload(file1_item)
        validate_columns(original_rows1, [], "File 1")
        set_job_progress(job_id, 12, "Reading File 2...")
        original_rows2 = read_csv_upload(file2_item) if mode == "linkage" and file2_item else []
        if mode == "linkage":
            validate_columns(original_rows2, [], "File 2")
        _check_canceled(cancel_check)
        headers1 = list(original_rows1[0].keys())
        headers2 = list(original_rows2[0].keys()) if original_rows2 else []
        transform_errors1: dict[str, dict[str, Any]] = {}
        transform_errors2: dict[str, dict[str, Any]] = {}
        shared_field_plan = None
        standardized_rows2: list[dict[str, str]] = []
        mapped_rows2: list[dict[str, str]] = []
        if mode == "linkage":
            header_pairs = aligned_schema_pairs(headers1, headers2)
            if header_pairs:
                set_job_progress(job_id, 20, "Files share the same column structure; planning field cleaning...")
                joint_plan = build_aligned_schema_cleaning_plan(
                    original_rows1, original_rows2, header_pairs,
                    input_label="aligned_schema_cleaning", progress_callback=_progress(job_id, 20, 42), **llm_kwargs,
                )
            else:
                set_job_progress(job_id, 20, "Planning how to standardize both files...")
                joint_plan = build_joint_preprocessing_plan(
                    original_rows1, original_rows2,
                    input_label="joint_preprocessing", progress_callback=_progress(job_id, 20, 42), **llm_kwargs,
                )
            plan1, plan2 = split_joint_preprocessing_plan(joint_plan)
            plan1 = validate_plan(plan1, headers1, rows=original_rows1, label="File 1")
            plan2 = validate_plan(plan2, headers2, rows=original_rows2, label="File 2")
            date_warnings = [*joint_plan.get("date_warnings", []), *plan1["date_warnings"], *plan2["date_warnings"]]
            _check_canceled(cancel_check)
            set_job_progress(job_id, 64, "Standardizing File 1...")
            standardized_rows1 = apply_preprocessing_plan(original_rows1, SOURCE_ID_FIELD, plan1, errors=transform_errors1)
            _check_canceled(cancel_check)
            set_job_progress(job_id, 70, "Standardizing File 2...")
            standardized_rows2 = apply_preprocessing_plan(original_rows2, SOURCE_ID_FIELD, plan2, errors=transform_errors2)
            _check_canceled(cancel_check)
            set_job_progress(job_id, 78, "Identifying shared linkage variables...")
            shared_field_plan = shared_mapping_from_joint_plan(joint_plan)
            available = available_field_options(shared_field_plan)
            if not available:
                raise ValueError("The preprocessing step did not find any mutual standardized fields between File 1 and File 2.")
            ai_logs = [path for path in [joint_plan.get("ai_log_path")] if path]
            mapped_rows1 = apply_shared_field_mapping(standardized_rows1, shared_field_plan, "file1")
            mapped_rows2 = apply_shared_field_mapping(standardized_rows2, shared_field_plan, "file2")
            plan_for_order = shared_field_plan
        else:
            set_job_progress(job_id, 20, "Standardizing File 1 fields...")
            plan1 = build_preprocessing_plan(
                original_rows1, input_label="file1", progress_callback=_progress(job_id, 20, 55), **llm_kwargs,
            )
            plan2 = {"fields": {}, "standardized_fields": [], "notes": ["dedupe mode: no File 2"]}
            date_warnings = list(plan1.get("date_warnings", []))
            available = available_field_options(plan1)
            ai_logs = [path for path in [plan1.get("ai_log_path")] if path]
            standardized_rows1 = apply_preprocessing_plan(original_rows1, SOURCE_ID_FIELD, plan1, errors=transform_errors1)
            mapped_rows1 = standardized_rows1
            plan_for_order = plan1
        _check_canceled(cancel_check)
        set_job_progress(job_id, 88, "Building field preview...")
        preview_fields = [option["field"] for option in available]
        # Display order follows the AI's standardized_fields (semantic order), then the rest alphabetically.
        available_set = set(preview_fields)
        suggested_order = [f for f in (plan_for_order.get("standardized_fields") or []) if f in available_set]
        display_order = suggested_order + sorted(available_set - set(suggested_order))
        display_pos = {f: i for i, f in enumerate(display_order)}

        def ordered_fields(rows: list[dict[str, str]]) -> list[str]:
            keys = {key for row in rows for key in row if key and not key.startswith("__")}
            return sorted(keys, key=lambda key: (display_pos.get(key, len(display_order)), key.lower()))

        standardized_fields1 = ordered_fields(standardized_rows1)
        standardized_fields2 = ordered_fields(standardized_rows2) if mode == "linkage" else []
        field_profiles = {
            "file1": profile_fields(mapped_rows1, preview_fields),
            "file2": profile_fields(mapped_rows2, preview_fields) if mode == "linkage" else {},
        }
        warnings = field_selection_warnings(available, field_profiles, mode)
        warnings += list(dict.fromkeys(date_warnings))
        warnings += transform_error_warnings(transform_errors1, len(original_rows1), "File 1")
        warnings += transform_error_warnings(transform_errors2, len(original_rows2), "File 2")
        _check_canceled(cancel_check)
        set_job_progress(job_id, 96, "Finalizing available variables...")
        finish_job(job_id, {"schema_mapping_result": {
            "mode": mode,
            "file1_rows": len(original_rows1),
            "file2_rows": len(original_rows2),
            "plan1": plan1,
            "plan2": plan2,
            "shared_field_plan": shared_field_plan,
            "ai_logs": ai_logs,
            "available_fields": available,
            "suggested_fields": sorted(preview_fields, key=lambda f: (display_pos.get(f, len(display_order)), f.lower())),
            "preview_fields": preview_fields,
            "preview_rows1": project_preview_rows(standardized_rows1, standardized_fields1),
            "preview_rows2": project_preview_rows(standardized_rows2, standardized_fields2) if mode == "linkage" else [],
            "preview_sections": preview_sections(standardized_rows1, standardized_rows2, standardized_fields1, standardized_fields2, mode),
            "field_profiles": field_profiles,
            "agent_rows1": mapped_rows1,
            "agent_rows2": mapped_rows2,
            "standardized_fields1": standardized_fields1,
            "standardized_fields2": standardized_fields2,
            "standardized_profiles": {
                "file1": profile_fields(standardized_rows1, standardized_fields1) if standardized_rows1 else {},
                "file2": profile_fields(standardized_rows2, standardized_fields2) if standardized_rows2 else {},
            },
            "selection_warnings": warnings,
            "display_order": display_order,
        }}, "Schema mapping complete.")
    except Exception as exc:
        fail_job(job_id, exc, "Schema mapping did not complete.")

def schema_mapping_rows(schema_mapping_job_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Standardized rows the planning agents work on, kept server-side by the schema mapping job."""
    with JOBS_LOCK:
        result = (JOBS.get(schema_mapping_job_id) or {}).get("schema_mapping_result") or {}
    rows1 = list(result.get("agent_rows1") or [])
    if not rows1:
        raise ValueError("The Schema Mapping results are no longer available; run Schema Mapping again.")
    return rows1, list(result.get("agent_rows2") or [])

def blocking_worker(
    job_id: str,
    selected_fields: list[str],
    available_fields: list[dict[str, Any]],
    mode: str,
    schema_mapping_job_id: str,
    api_url: str,
    api_model: str,
    api_key: str,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    blocking_ratio_min: float = 0.0002,
    blocking_ratio_max: float = 0.002,
) -> None:
    try:
        rows1, rows2 = schema_mapping_rows(schema_mapping_job_id)
        plan = build_blocking_plan(
            selected_fields=selected_fields,
            available_fields=available_fields,
            mode=mode,
            rows1=rows1,
            rows2=rows2,
            api_url=api_url,
            model=api_model,
            api_key=api_key,
            progress_callback=_progress(job_id),
            cancel_callback=_cancel_check(job_id),
            temperature=temperature,
            top_p=top_p,
            extra_body=extra_body,
            reasoning_effort=reasoning_effort,
            blocking_ratio_min=blocking_ratio_min,
            blocking_ratio_max=blocking_ratio_max,
            usage=job_usage(job_id),
        )
        finish_job(job_id, {"blocking_plan": plan}, "Blocking plan ready.")
    except Exception as exc:
        fail_job(job_id, exc, "The blocking agent did not complete.")

def comparators_worker(
    job_id: str,
    selected_fields: list[str],
    available_fields: list[dict[str, Any]],
    mode: str,
    schema_mapping_job_id: str,
    api_url: str,
    api_model: str,
    api_key: str,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> None:
    try:
        rows1, rows2 = schema_mapping_rows(schema_mapping_job_id)
        plan = build_comparison_plan(
            selected_fields=selected_fields,
            available_fields=available_fields,
            mode=mode,
            rows1=rows1,
            rows2=rows2,
            api_url=api_url,
            model=api_model,
            api_key=api_key,
            progress_callback=_progress(job_id),
            cancel_callback=_cancel_check(job_id),
            temperature=temperature,
            top_p=top_p,
            extra_body=extra_body,
            reasoning_effort=reasoning_effort,
            usage=job_usage(job_id),
        )
        finish_job(job_id, {"comparison_plan": plan}, "Comparison methods selected.")
    except Exception as exc:
        fail_job(job_id, exc, "The matching agent did not complete.")
