import json
import random
from typing import Any, Callable

from agent_runtime import LLMSettings, agent_tool, parse_json_argument, run_agent
from comparators import (
    ALL_SUPPORTED_COMPARATORS,
    THRESHOLD_DEFAULTS,
    comparison_level_labels,
    normalize_thresholds,
)
from linkage_inputs import build_splink_inputs
from schema_agent import ensure_api_connection, normalize_api_base_url, profile_fields

PROFILE_SAMPLE_ROWS = 5000


COMPARATOR_DOCS: dict[str, dict[str, Any]] = {
    "ExactMatch": {
        "description": (
            "Binary: exact match or not. "
            "No thresholds. Use when values are clean, standardized, and typos are very unlikely."
        ),
        "best_for": [
            "standardized short codes (sex/gender codes, race codes, state abbreviations)",
            "high-quality structured identifiers (SSN, national ID) when values are clean",
            "low-cardinality categorical fields where every distinct value is meaningful",
        ],
        "avoid_for": ["names", "addresses", "free text", "noisy or un-standardized values", "numeric IDs with digit errors"],
        "thresholds": "None — binary match/no-match.",
        "example": "ExactMatch('sex_code')",
    },
    "JaroWinklerAtThresholds": {
        "description": (
            "Fuzzy character similarity with extra weight on prefix agreement (first 4 chars). "
            "Levels: exact match, then one level per similarity threshold, then everything else. "
            "Float thresholds in (0, 1]. "
            "Good default for most string fields with spelling variation."
        ),
        "best_for": [
            "general fuzzy text where spelling errors occur",
            "fields where the beginning characters are the most reliable part",
            "addresses, place names, short-to-medium strings",
        ],
        "avoid_for": ["dates", "emails", "numeric identifiers", "postcodes", "multi-word fields with variable word order"],
        "thresholds": (
            "List of floats in (0, 1], descending. "
            "Calibrate by avg_length: ≤6→[0.95,0.90], 7-15→[0.92,0.82], 16-40→[0.88,0.75], >40→[0.85,0.70]."
        ),
        "example": "JaroWinklerAtThresholds('last_name', [0.92, 0.82])",
    },
    "JaroAtThresholds": {
        "description": (
            "Like JaroWinkler but without prefix weighting — treats all character positions equally. "
            "Use instead of JaroWinkler when the beginning of the string is NOT more reliable than the rest "
            "(e.g. fields where values often start with variable prefixes)."
        ),
        "best_for": [
            "strings where early characters are not more informative than later ones",
            "fields with variable leading content",
        ],
        "avoid_for": ["dates", "emails", "numeric identifiers", "postcodes"],
        "thresholds": (
            "List of floats in (0, 1], descending. "
            "Same calibration as JaroWinkler by avg_length: ≤6→[0.95,0.90], 7-15→[0.92,0.82], 16-40→[0.88,0.75], >40→[0.85,0.70]."
        ),
        "example": "JaroAtThresholds('middle_name', [0.92, 0.82])",
    },
    "LevenshteinAtThresholds": {
        "description": (
            "Edit distance: counts insertions, deletions, substitutions. "
            "Integer thresholds = maximum allowed edits per level. "
            "Does NOT count transpositions (adjacent swaps) as a single edit — use DamerauLevenshtein for that."
        ),
        "best_for": [
            "numeric strings with digit errors",
            "short codes where a small number of absolute edits is meaningful",
        ],
        "avoid_for": [
            "fields where character transpositions (adjacent swaps) are common — use DamerauLevenshtein instead",
            "very short strings where even 1 edit is a large fraction of the value",
        ],
        "thresholds": (
            "List of non-negative integers, ascending. "
            "Calibrate by avg_length: ≤6→[1], 7-15→[1,2], 16-40→[2,3], >40→[2,4]."
        ),
        "example": "LevenshteinAtThresholds('zip_code', [1, 2])",
    },
    "DamerauLevenshteinAtThresholds": {
        "description": (
            "Like Levenshtein but also counts adjacent character transpositions (swaps) as one edit. "
            "Strictly better than Levenshtein for text where swap errors are common."
        ),
        "best_for": [
            "names and text where transposition errors occur (e.g. 'Jhon' vs 'John', 'teh' vs 'the')",
            "general fuzzy text fields",
        ],
        "avoid_for": ["very short strings (avg_length ≤ 4)"],
        "thresholds": (
            "List of non-negative integers, ascending. "
            "Calibrate by avg_length: ≤6→[1], 7-15→[1,2], 16-40→[2,3], >40→[2,4]."
        ),
        "example": "DamerauLevenshteinAtThresholds('first_name', [1, 2])",
    },
    "JaccardAtThresholds": {
        "description": (
            "Token-set Jaccard similarity. Tokenizes values into words, then computes the Jaccard index "
            "of the two token sets. Robust to word-order variation within a field. "
            "Lower similarity scores are expected even for good matches on diverse multi-word text."
        ),
        "best_for": [
            "multi-word text fields where word order varies",
            "descriptions, titles, addresses with multiple word components",
            "fields with avg_length > 20 that contain multiple distinct words",
        ],
        "avoid_for": [
            "short single-token fields (use JaroWinkler instead)",
            "structured identifiers",
            "fields where character-level comparison is more appropriate",
        ],
        "thresholds": (
            "List of floats in (0, 1], descending. "
            "Token-based so scores are lower than character-based; use lower thresholds: "
            "avg_length ≤ 20→[0.88,0.70], >20→[0.80,0.60]."
        ),
        "example": "JaccardAtThresholds('title', [0.85, 0.70])",
    },
    "AbsoluteDateDifferenceAtThresholds": {
        "description": (
            "Compares two date fields by their absolute difference in days. "
            "Thresholds are integers: the maximum allowed difference in days per level. "
            "Unlike DateOfBirthComparison, this does NOT use year/month partial agreement — "
            "it is purely distance-based, making it appropriate for event or encounter dates "
            "where proximity in time (not partial date structure) is what matters."
        ),
        "best_for": [
            "event dates, encounter dates, transaction dates, service dates",
            "any date field that is NOT a date of birth",
            "date fields where time proximity is the relevant notion of similarity",
        ],
        "avoid_for": [
            "date of birth fields — use DateOfBirthComparison instead (it uses year/month partial levels)",
            "non-date fields",
        ],
        "thresholds": (
            "List of non-negative integers, ascending (days). "
            "Examine sample dates to judge typical recording variation. "
            "Examples: [1, 7] for high-precision dates, [7, 30] for weekly tolerance, "
            "[30, 365] for loose date matching."
        ),
        "example": "AbsoluteDateDifferenceAtThresholds('encounter_date', [7, 30])",
    },
    "NameComparison": {
        "description": (
            "Specialized person-name comparator that combines multiple similarity metrics tuned "
            "specifically for personal names. Handles nicknames and common name variations automatically. "
            "No thresholds needed."
        ),
        "best_for": ["given names (first names)", "family names (last names)", "full person names"],
        "avoid_for": ["non-name text fields", "organizational names", "place names"],
        "thresholds": "None — uses built-in multi-level name comparison.",
        "example": "NameComparison('last_name')",
    },
    "DateOfBirthComparison": {
        "description": (
            "Specialized date-of-birth comparator. Uses partial date agreement levels "
            "(year only, year+month) to capture partial DOB matches. "
            "Levels: exact, one typo, within a month, within a year, other. "
            "Input must be date strings. Do NOT use for non-DOB date fields."
        ),
        "best_for": ["date of birth fields exclusively"],
        "avoid_for": [
            "event/encounter/service dates — use AbsoluteDateDifferenceAtThresholds instead",
            "non-date fields",
        ],
        "thresholds": "None — uses built-in date-level partial agreement.",
        "example": "DateOfBirthComparison('dob')",
    },
    "EmailComparison": {
        "description": (
            "Compares email addresses by splitting into username and domain parts. "
            ""
        ),
        "best_for": ["email address fields"],
        "avoid_for": ["any non-email field"],
        "thresholds": "None — uses built-in username/domain structure.",
        "example": "EmailComparison('email')",
    },
    "PostcodeComparison": {
        "description": (
            "Compares postal/ZIP codes using geographic proximity levels. "
            ""
        ),
        "best_for": ["ZIP codes", "postal codes"],
        "avoid_for": ["non-postcode fields"],
        "thresholds": "None — uses built-in geographic proximity levels.",
        "example": "PostcodeComparison('zip_code')",
    },
}


def allowed_comparators(fields: list[str]) -> dict[str, list[str]]:
    return {field: list(ALL_SUPPORTED_COMPARATORS) for field in fields}


def validate_comparison_plan(
    plan: dict[str, Any],
    selected_fields: list[str],
    available_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Comparison plan must be a JSON object.")
    selected_set = set(selected_fields)
    excluded_raw = plan.get("excluded_fields", [])
    if excluded_raw in (None, ""):
        excluded_raw = []
    if not isinstance(excluded_raw, list):
        raise ValueError("Comparison plan excluded_fields must be a list when provided.")
    excluded_fields = [str(field) for field in excluded_raw if str(field) in selected_set]
    active_selected_fields = [field for field in selected_fields if field not in set(excluded_fields)]
    field_pool = [option.get("field") for option in (available_fields or []) if option.get("field")] or list(selected_fields)
    all_allowed = allowed_comparators(field_pool)
    comparisons = plan.get("comparisons")
    if not isinstance(comparisons, dict):
        raise ValueError("Comparison plan must include a comparisons object.")
    cleaned: dict[str, Any] = {}
    errors: list[str] = []
    for field, spec in comparisons.items():
        if field not in selected_set and field not in all_allowed:
            continue
        if not isinstance(spec, dict):
            errors.append(f"{field}: spec must be an object.")
            continue
        comparator = str(spec.get("comparator") or "").strip()
        if comparator not in ALL_SUPPORTED_COMPARATORS:
            errors.append(f"{field}: comparator {comparator!r} is not one of {ALL_SUPPORTED_COMPARATORS}.")
            continue
        try:
            thresholds = normalize_thresholds(comparator, spec.get("thresholds"))
        except ValueError as exc:
            errors.append(f"{field}: {exc}")
            continue
        cleaned[field] = {"comparator": comparator, "reason": str(spec.get("reason") or ""), "thresholds": thresholds}
    missing = [field for field in active_selected_fields if field not in cleaned]
    if missing:
        errors.append(f"Missing a comparator for active field(s) {missing}; add them or list them in excluded_fields.")
    if errors:
        raise ValueError("Plan rejected: " + " | ".join(errors))
    plan["comparisons"] = cleaned
    plan["selected_fields"] = active_selected_fields
    plan["excluded_fields"] = excluded_fields
    plan["allowed_comparators"] = all_allowed
    plan["threshold_defaults"] = THRESHOLD_DEFAULTS
    return plan


def field_profiles_for_matching(
    selected_fields: list[str],
    mode: str,
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Completeness, uniqueness, length and samples per field, on the values Splink will compare."""
    rng = random.Random(0)
    sample1 = rng.sample(rows1, PROFILE_SAMPLE_ROWS) if len(rows1) > PROFILE_SAMPLE_ROWS else rows1
    sample2 = rng.sample(rows2, PROFILE_SAMPLE_ROWS) if len(rows2) > PROFILE_SAMPLE_ROWS else rows2
    records, _, _ = build_splink_inputs(sample1, sample2, mode, selected_fields=selected_fields)
    profiles: dict[str, dict[str, Any]] = {}
    sides = [("file1", [r for r in records if r["source_dataset"] == "file1"])]
    if mode == "linkage":
        sides.append(("file2", [r for r in records if r["source_dataset"] == "file2"]))
    for side, side_records in sides:
        side_profiles = profile_fields(side_records, selected_fields)
        for field in selected_fields:
            profile = side_profiles.get(field, {})
            seen: list[str] = []
            for record in side_records:
                value = record.get(field)
                if value is not None and str(value) not in seen:
                    seen.append(str(value))
                    if len(seen) >= 10:
                        break
            profiles.setdefault(field, {})[side] = {
                "completeness": profile.get("non_missing_rate", 0),
                "uniqueness": profile.get("uniqueness_rate", 0),
                "avg_length": profile.get("average_length", 0),
                "samples": seen,
            }
    return profiles


def build_comparison_plan(
    *,
    selected_fields: list[str],
    available_fields: list[dict[str, Any]],
    mode: str,
    rows1: list[dict[str, str]] | None = None,
    rows2: list[dict[str, str]] | None = None,
    api_url: str = "",
    model: str = "",
    api_key: str = "",
    max_attempts: int = 3,
    progress_callback: Any = None,
    cancel_callback: Callable[[], bool] | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    def progress(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)

    if not rows1:
        raise ValueError("Comparator selection needs the standardized rows from Schema Mapping; run Schema Mapping again.")
    settings = LLMSettings(
        api_url=normalize_api_base_url(api_url), model=model, api_key=api_key,
        temperature=temperature, top_p=top_p, extra_body=extra_body,
        reasoning_effort=reasoning_effort,
    )
    progress(5, "Checking AI connection...")
    ensure_api_connection(settings.api_url, api_key)
    progress(12, "Profiling selected fields...")
    profiles = field_profiles_for_matching(selected_fields, mode, rows1, rows2 or [])
    field_meta_by_name = {opt.get("field"): opt for opt in available_fields if opt.get("field")}
    submitted: list[dict[str, Any]] = []

    def describe_fields() -> str:
        """
        Call this first. For every selected field returns its role and, per file, completeness
        (fraction non-empty), uniqueness (fraction distinct among non-empty), average length and
        up to 10 sample values, all measured on the values Splink will compare. Also returns a
        one-line summary of every supported comparator.
        """
        progress(20, "Reviewing field profiles...")
        fields_info = []
        for field in selected_fields:
            meta = field_meta_by_name.get(field, {})
            fields_info.append({
                "field": field,
                "label": meta.get("label", field),
                "role": meta.get("role") or "supporting",
                **profiles.get(field, {}),
            })
        return json.dumps({
            "mode": mode,
            "selected_fields": selected_fields,
            "fields": fields_info,
            "supported_comparators": {name: doc["description"] for name, doc in COMPARATOR_DOCS.items()},
        }, ensure_ascii=False)

    def lookup_comparator(comparator_name: str, thresholds_json: str = "") -> str:
        """
        Full details for one comparator: what it suits, what to avoid, threshold guidance, and
        the exact comparison levels Splink builds (strongest first) for the given thresholds.

        Arguments:
        - comparator_name: one of ExactMatch, JaroWinklerAtThresholds, JaroAtThresholds,
          LevenshteinAtThresholds, DamerauLevenshteinAtThresholds, JaccardAtThresholds,
          AbsoluteDateDifferenceAtThresholds, NameComparison, DateOfBirthComparison,
          EmailComparison, PostcodeComparison.
        - thresholds_json: optional JSON list of thresholds to preview, e.g. "[0.9, 0.8]".
        """
        doc = COMPARATOR_DOCS.get(comparator_name)
        if doc is None:
            raise ValueError(f"Unknown comparator: '{comparator_name}'. Supported: {', '.join(COMPARATOR_DOCS)}.")
        thresholds = parse_json_argument(thresholds_json) if str(thresholds_json or "").strip() else None
        return json.dumps({
            "comparator": comparator_name,
            **doc,
            "thresholds_used": normalize_thresholds(comparator_name, thresholds),
            "splink_levels_strongest_first": comparison_level_labels(comparator_name, thresholds),
        }, ensure_ascii=False)

    def accept(raw: dict[str, Any]) -> dict[str, Any]:
        progress(80, "Validating comparison plan...")
        return validate_comparison_plan(raw, selected_fields, available_fields)

    def submit_comparison_plan(plan_json: str) -> str:
        """
        Call this when your comparison plan is complete. This is the ONLY valid way to finish.

        Expected JSON:
        {
          "comparisons": {"FieldName": {"comparator": "ExactMatch", "thresholds": [], "reason": "why"}},
          "excluded_fields": ["fields to leave out of matching"],
          "notes": ["optional"]
        }

        Every active selected field needs a supported comparator; threshold types are checked.
        On failure you get every problem at once; fix them and call again.
        """
        validated = accept(parse_json_argument(plan_json))
        submitted.append(validated)
        return json.dumps({
            "status": "accepted",
            "active_fields": validated["selected_fields"],
            "excluded_fields": validated["excluded_fields"],
            "assignments": {field: spec["comparator"] for field, spec in validated["comparisons"].items()},
        }, ensure_ascii=False)

    system_prompt = (
        "You are a Splink comparators agent. Assign the best comparison function to each selected field "
        "for probabilistic record linkage.\n\n"
        "How Splink comparisons work: each comparator sorts a record pair into one of several levels "
        "(exact, close, loose, other). Splink learns how much each level counts as match evidence.\n\n"
        "Workflow:\n"
        "1. Call describe_fields to see completeness, uniqueness, average length, role and samples per field.\n"
        "2. Call lookup_comparator for any comparator you are unsure about; it shows the exact levels Splink builds.\n"
        "3. Assign exactly one comparator per active field.\n"
        "4. Call submit_comparison_plan. This is the ONLY valid way to finish.\n\n"
        "Comparator selection, using sample values and statistics as evidence:\n"
        "  - Email addresses: EmailComparison\n"
        "  - Postal/ZIP codes: PostcodeComparison\n"
        "  - Date of birth: DateOfBirthComparison\n"
        "  - Other dates (events, encounters, transactions): AbsoluteDateDifferenceAtThresholds\n"
        "  - Few distinct values, or short standardized codes with no spelling variation: ExactMatch\n"
        "  - Personal names: NameComparison\n"
        "  - avg_length > 25 with multiple words in varying order: JaccardAtThresholds\n"
        "  - Other fuzzy strings: JaroWinklerAtThresholds (JaroAtThresholds when the prefix is not more reliable)\n"
        "  - Numeric identifiers prone to digit errors or swaps: DamerauLevenshteinAtThresholds\n\n"
        "Threshold calibration, using avg_length from describe_fields:\n"
        "  JaroWinkler / Jaro (floats in (0, 1]): <=6: [0.95, 0.90]; 7-15: [0.92, 0.82]; 16-40: [0.88, 0.75]; >40: [0.85, 0.70]\n"
        "  Jaccard: <=25: [0.88, 0.70]; >25: [0.80, 0.60]\n"
        "  Levenshtein / DamerauLevenshtein (integers): <=6: [1]; 7-15: [1, 2]; 16-40: [2, 3]; >40: [2, 4]\n"
        "  AbsoluteDateDifference (days): [1, 7] precise, [7, 30] weekly tolerance, [30, 365] loose.\n"
        "  Two thresholds is standard; add a third only for highly informative fields in large datasets.\n\n"
        "Exclusion rule: exclude file-local identifiers (row numbers, internal keys, system IDs that exist in "
        "only one file). Do NOT exclude cross-file identifiers such as national IDs or SSNs."
    )
    plan = run_agent(
        label="comparators",
        settings=settings,
        tools=[
            agent_tool(describe_fields, cancel_callback),
            agent_tool(lookup_comparator, cancel_callback),
            agent_tool(submit_comparison_plan, cancel_callback),
        ],
        system_prompt=system_prompt,
        prompt={
            "task": (
                f"Assign a Splink comparator to each selected field for {mode} mode. Start with describe_fields, "
                "use lookup_comparator when unsure, then call submit_comparison_plan."
            ),
            "mode": mode,
            "selected_fields": selected_fields,
        },
        submitted=submitted,
        accept=accept,
        submit_tool_name="submit_comparison_plan",
        max_attempts=max_attempts,
        progress=progress,
        progress_message="Selecting comparison methods...",
        progress_start=30,
        cancel_check=cancel_callback,
        usage=usage,
    )
    plan["field_profiles"] = profiles
    progress(95, "Comparison methods selected.")
    return plan
