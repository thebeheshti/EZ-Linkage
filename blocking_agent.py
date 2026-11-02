import json
import random
import re
from typing import Any, Callable

import duckdb

from agent_runtime import LLMSettings, agent_tool, extract_json_object, run_agent
from linkage_inputs import build_splink_inputs, record_columns, records_table
from schema_agent import ensure_api_connection, normalize_api_base_url, profile_fields

BLOCKING_RATIO_MIN = 0.0002  # below this → rules too restrictive
BLOCKING_RATIO_MAX = 0.002   # above this → rules too broad
# Never let one blocking plan hand Splink more candidate pairs than this.
MAX_PREDICTION_PAIRS = 20_000_000
# The ratio window always admits this many candidates per record, so small files
# are never forced to drop true matches just to fit a fixed ratio.
MIN_CANDIDATES_PER_RECORD = 20
# Rules are measured on a sample of each file: 5% of its records, but at least
# 5,000 (so small files still yield enough pairs for a stable estimate) and at
# most 50,000 (so the agent's repeated tests stay quick on very large files).
BLOCKING_SAMPLE_FRACTION = 0.05
BLOCKING_SAMPLE_MIN = 5_000
BLOCKING_SAMPLE_MAX = 50_000
BLOCKING_SAMPLE_SEED = 0
# Once one rule alone keeps this many times the maximum allowed pairs, the plan is
# clearly too broad, so the union is not counted (its lower bound is reported).
UNION_COUNT_LIMIT_FACTOR = 4


def _extract_json_array(text: str) -> list | None:
    """Extract the outermost JSON array from text.
    Tolerates trailing garbage (stray '}') and unclosed arrays (missing ']').
    """
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    last_close = -1
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
        elif ch == "}" and depth == 1:
            last_close = i
    # Array was never closed — try appending ']' after the last complete object
    if last_close != -1:
        try:
            return json.loads(text[start : last_close + 1] + "]")
        except json.JSONDecodeError:
            pass
    return None


SAFE_SQL_FUNCTIONS = {
    "abs",
    "cast",
    "ceil",
    "coalesce",
    "concat",
    "date_diff",
    "date_part",
    "floor",
    "greatest",
    "jaro_similarity",
    "jaro_winkler_similarity",
    "least",
    "left",
    "length",
    "levenshtein",
    "lower",
    "ltrim",
    "nullif",
    "regexp_replace",
    "replace",
    "right",
    "round",
    "rtrim",
    "starts_with",
    "strpos",
    "substr",
    "substring",
    "trim",
    "try_cast",
    "upper",
}


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def normalize_blocking_sql(sql: str) -> str:
    sql = str(sql or "").strip().rstrip(";")
    if not sql:
        raise ValueError("Blocking SQL condition is empty.")
    blocked = (" select ", " from ", " join ", " insert ", " update ", " delete ", " drop ", " alter ", "--", "/*", "*/")
    padded = f" {sql.lower()} "
    if any(token in padded for token in blocked):
        raise ValueError("Blocking SQL must be a boolean condition, not a full SQL statement.")
    return sql


def referenced_columns(sql: str) -> list[str]:
    refs = []
    for quoted, bare in re.findall(r"\b[lr]\.(?:\"((?:[^\"]|\"\")+)\"|([A-Za-z_][A-Za-z0-9_]*))", sql):
        value = quoted or bare
        if value:
            refs.append(value.replace('""', '"'))
    return refs


def canonicalize_blocking_sql_columns(sql: str, available_columns: list[str] | None = None) -> str:
    available = list(available_columns or [])
    by_token: dict[str, str] = {}
    for column in available:
        lower = column.lower()
        by_token[lower] = column
        stripped = lower.replace("_", "").replace(" ", "")
        if stripped not in by_token:
            by_token[stripped] = column

    def replace(match: re.Match[str]) -> str:
        alias = match.group(1)
        quoted = match.group(2)
        bare = match.group(3)
        raw_column = (quoted or bare or "").replace('""', '"')
        key = raw_column.lower()
        canonical = by_token.get(key) or by_token.get(key.replace("_", "").replace(" ", ""), raw_column)
        return f"{alias}.{quote_ident(canonical)}"

    return re.sub(
        r"\b([lr])\.(?:\"((?:[^\"]|\"\")+)\"|([A-Za-z_][A-Za-z0-9_]*))",
        replace,
        sql,
    )


def referenced_functions(sql: str) -> list[str]:
    names = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", sql)
    return sorted({name.lower() for name in names if name.lower() not in {"and", "or", "not", "case", "when", "in"}})



def to_executed_sql(sql: str) -> str:
    # CAST on a malformed date raises in DuckDB; TRY_CAST yields NULL. The stored
    # rule is the executed rule, so the agent's measurements match production.
    return re.sub(r"(?<![A-Za-z_])CAST\s*\(", "TRY_CAST(", sql, flags=re.IGNORECASE)


def validate_blocking_plan(
    plan: dict[str, Any],
    selected_fields: list[str],
    available_columns: list[str] | None = None,
    min_rule_count: int = 1,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Blocking plan must be a JSON object.")
    rules = plan.get("blocking_rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Blocking plan must include at least one blocking rule.")

    available = set(available_columns or selected_fields)
    cleaned_rules = []
    seen = set()
    errors = []
    for index, rule in enumerate(rules, start=1):
        if isinstance(rule, str):
            rule = {"label": f"SQL rule {index}", "sql": rule}
        if not isinstance(rule, dict):
            errors.append(f"Rule {index} must be an object.")
            continue
        sql = rule.get("sql") or rule.get("sql_condition")
        if not sql and isinstance(rule.get("fields"), list) and rule.get("fields"):
            parts = [f"l.{quote_ident(field)} = r.{quote_ident(field)}" for field in rule["fields"]]
            sql = " AND ".join(parts)
        try:
            sql = normalize_blocking_sql(str(sql or ""))
            sql = canonicalize_blocking_sql_columns(sql, list(available))
        except ValueError as exc:
            errors.append(f"Rule {index}: {exc}")
            continue
        missing = [column for column in referenced_columns(sql) if column not in available]
        if missing:
            errors.append(f"Rule {index} references unavailable column(s): {', '.join(sorted(set(missing)))}.")
            continue
        disallowed_functions = [name for name in referenced_functions(sql) if name not in SAFE_SQL_FUNCTIONS]
        if disallowed_functions:
            errors.append(f"Rule {index} uses unsupported SQL function(s): {', '.join(disallowed_functions)}.")
            continue
        sql = to_executed_sql(sql)
        if sql in seen:
            continue
        seen.add(sql)
        cleaned_rules.append({
            "label": str(rule.get("label") or f"SQL rule {index}"),
            "sql": sql,
            "reason": str(rule.get("reason") or ""),
            "estimated_pairs": int(rule.get("estimated_pairs") or 0),
        })

    if errors:
        raise ValueError(" ".join(errors))
    if len(cleaned_rules) < min_rule_count:
        raise ValueError(f"Blocking plan produced {len(cleaned_rules)} valid rule(s), but at least {min_rule_count} are required.")

    return {
        "blocking_rules": cleaned_rules,
        "notes": plan.get("notes", []) if isinstance(plan.get("notes", []), list) else [],
        "available_sql_columns": sorted(available),
        "safe_sql_functions": sorted(SAFE_SQL_FUNCTIONS),
    }


def total_possible_pairs(n1: int, n2: int, mode: str) -> int:
    return n1 * (n1 - 1) // 2 if mode == "dedupe" else n1 * n2


def blocking_ratio_bounds(n1: int, n2: int, mode: str, ratio_min: float, ratio_max: float) -> tuple[float, float]:
    """The pair-ratio window a plan must land in, adjusted so it is always achievable.

    - The ceiling is raised so every record can keep MIN_CANDIDATES_PER_RECORD
      candidates (small files would otherwise be forced to drop true matches).
    - The ceiling is capped so Splink never gets more than MAX_PREDICTION_PAIRS.
    """
    possible = total_possible_pairs(n1, n2, mode)
    if possible <= 0:
        return ratio_min, ratio_max
    records = n1 if mode == "dedupe" else min(n1, n2)
    effective_max = max(ratio_max, min(1.0, MIN_CANDIDATES_PER_RECORD * records / possible))
    effective_max = min(effective_max, MAX_PREDICTION_PAIRS / possible)
    effective_min = min(ratio_min, effective_max / 10)
    return effective_min, effective_max


def _rule_pairs_sql(rule_sql: str, mode: str) -> str:
    if mode == "dedupe":
        return f"select l.record_id as l_id, r.record_id as r_id from records as l join records as r on ({rule_sql}) and l.record_id < r.record_id"
    return f"select l.record_id as l_id, r.record_id as r_id from left_records as l join right_records as r on ({rule_sql})"


def blocking_sample_size(n_records: int) -> int:
    """How many records of a file the blocking rules are measured on."""
    target = round(n_records * BLOCKING_SAMPLE_FRACTION)
    return min(n_records, max(BLOCKING_SAMPLE_MIN, min(BLOCKING_SAMPLE_MAX, target)))


def evaluate_sql_blocking_rules(
    records1: list[dict[str, Any]],
    records2: list[dict[str, Any]] | None,
    mode: str,
    rules: list[dict[str, Any]],
    columns: list[str],
    max_union_pairs: int | None = None,
) -> dict[str, Any]:
    """Count candidate pairs per rule and for the union, on Splink-ready records.

    The union is the sum of each rule's marginal pairs (those no earlier rule
    keeps), which needs only counts, never the pairs themselves. When one rule
    alone keeps more than max_union_pairs, the union is not counted and the
    largest rule count is reported as its lower bound.
    """
    if not records1:
        return {"total_possible_pairs": 0, "rules": [], "union_pairs": 0, "union_ratio_of_possible": 0}
    con = duckdb.connect(database=":memory:")
    try:
        left = records_table(records1, columns)
        if mode == "dedupe":
            con.register("records", left)
            total_possible = total_possible_pairs(len(records1), 0, mode)
        else:
            con.register("left_records", left)
            con.register("right_records", records_table(records2 or [], columns))
            total_possible = total_possible_pairs(len(records1), len(records2 or []), mode)
        rule_metrics = []
        for index, rule in enumerate(rules, start=1):
            sql = rule["sql"]
            label = rule.get("label") or f"Rule {index}"
            try:
                count = int(con.execute(f"select count(*) from ({_rule_pairs_sql(sql, mode)})").fetchone()[0])
            except Exception as exc:
                raise ValueError(f"Rule {index} ({label!r}) failed DuckDB execution: {exc}") from exc
            rule_metrics.append({
                "index": index,
                "label": label,
                "sql": sql,
                "pair_count": count,
                "ratio_of_possible": round(count / total_possible, 6) if total_possible else 0,
            })
        largest = max((metric["pair_count"] for metric in rule_metrics), default=0)
        union_is_lower_bound = max_union_pairs is not None and largest > max_union_pairs
        if union_is_lower_bound:
            union_pairs = largest
        else:
            union_pairs = 0
            for index, rule in enumerate(rules):
                earlier = [f"coalesce(({previous['sql']}), false)" for previous in rules[:index]]
                sql = rule["sql"] if not earlier else f"({rule['sql']}) and not ({' or '.join(earlier)})"
                union_pairs += int(con.execute(f"select count(*) from ({_rule_pairs_sql(sql, mode)})").fetchone()[0])
    finally:
        con.close()
    return {
        "total_possible_pairs": total_possible,
        "union_pairs": union_pairs,
        "union_is_lower_bound": union_is_lower_bound,
        "union_ratio_of_possible": round(union_pairs / total_possible, 8) if total_possible else 0,
        "rules": rule_metrics,
    }


def _parse_rules_argument(text: str) -> list[Any]:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raw = _extract_json_array(str(text)) or extract_json_object(str(text))
    if isinstance(raw, dict):
        raw = raw.get("blocking_rules") or raw.get("rules") or []
    if not isinstance(raw, list):
        raise ValueError("Pass a JSON array of {label, sql} objects.")
    return raw


def build_blocking_plan(
    *,
    selected_fields: list[str],
    available_fields: list[dict[str, Any]] | None = None,
    mode: str,
    rows1: list[dict[str, Any]] | None = None,
    rows2: list[dict[str, Any]] | None = None,
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
    blocking_ratio_min: float = BLOCKING_RATIO_MIN,
    blocking_ratio_max: float = BLOCKING_RATIO_MAX,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    def progress(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)

    rows1 = rows1 or []
    rows2 = (rows2 or []) if mode == "linkage" else []
    if not rows1:
        raise ValueError("Blocking needs the standardized rows from Schema Mapping; run Schema Mapping again.")
    rng = random.Random(BLOCKING_SAMPLE_SEED)
    size1, size2 = blocking_sample_size(len(rows1)), blocking_sample_size(len(rows2))
    eval_rows1 = rng.sample(rows1, size1) if size1 < len(rows1) else rows1
    eval_rows2 = rng.sample(rows2, size2) if size2 < len(rows2) else rows2
    # The agent measures rules on the records Splink will actually compare.
    records, _, _ = build_splink_inputs(eval_rows1, eval_rows2, mode, selected_fields=selected_fields)
    records1 = [record for record in records if record["source_dataset"] == "file1"]
    records2 = [record for record in records if record["source_dataset"] == "file2"]
    available_columns = record_columns(records)

    n1_full, n2_full = len(rows1), len(rows2)
    full_possible = total_possible_pairs(n1_full, n2_full, mode)
    sample_possible = total_possible_pairs(len(records1), len(records2), mode)
    pair_scale = (full_possible / sample_possible) if sample_possible else 1.0
    ratio_min, ratio_max = blocking_ratio_bounds(n1_full, n2_full, mode, blocking_ratio_min, blocking_ratio_max)
    profiles = {
        "file1": profile_fields(records1, available_columns),
        "file2": profile_fields(records2, available_columns) if mode == "linkage" else {},
    }

    settings = LLMSettings(
        api_url=normalize_api_base_url(api_url), model=model, api_key=api_key,
        temperature=temperature, top_p=top_p, extra_body=extra_body,
        reasoning_effort=reasoning_effort,
    )
    progress(5, "Connecting to AI service...")
    ensure_api_connection(settings.api_url, api_key)
    submitted: list[dict[str, Any]] = []

    def measure(rules: list[Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        plan = validate_blocking_plan({"blocking_rules": rules}, selected_fields, available_columns)
        metrics = evaluate_sql_blocking_rules(
            records1, records2, mode, plan["blocking_rules"], available_columns,
            max_union_pairs=int(UNION_COUNT_LIMIT_FACTOR * ratio_max * sample_possible) + 1,
        )
        union_ratio = metrics["union_ratio_of_possible"]
        if metrics["union_is_lower_bound"]:
            verdict = "TOO_MANY_PAIRS: one rule alone keeps far more than the maximum. Tighten or remove the broadest rule."
        elif union_ratio < ratio_min:
            verdict = f"TOO_FEW_PAIRS: union ratio {union_ratio:.2e} is below the minimum {ratio_min:.2e}. Add broader rules or relax conditions."
        elif union_ratio > ratio_max:
            verdict = f"TOO_MANY_PAIRS: union ratio {union_ratio:.2e} is above the maximum {ratio_max:.2e}. Tighten the broadest rule."
        else:
            verdict = "OK"
        metrics["projected_union_pairs"] = int(metrics["union_pairs"] * pair_scale)
        metrics["full_rows"] = {"file1": n1_full, "file2": n2_full}
        metrics["target_ratio_range"] = [ratio_min, ratio_max]
        return plan, metrics, verdict

    def describe_columns() -> str:
        """
        Call this first. Returns every column available for blocking with completeness,
        uniqueness, average length and up to 8 sample values per file, plus the pair counts
        and the target ratio range. Values are shown exactly as the blocking rules will see
        them (lower-cased names, digits-only phones, ISO dates; blanks are NULL).
        """
        progress(15, "Profiling fields and sampling values...")
        columns: dict[str, Any] = {}
        for column in available_columns:
            entry: dict[str, Any] = {}
            for side, side_records in (("file1", records1), ("file2", records2)):
                if side == "file2" and mode != "linkage":
                    continue
                profile = profiles[side].get(column, {})
                samples = sorted({str(r.get(column)) for r in side_records[:200] if r.get(column) is not None})[:8]
                entry[side] = {
                    "completeness": round(float(profile.get("non_missing_rate", 0)), 3),
                    "uniqueness": round(float(profile.get("uniqueness_rate", 0)), 3),
                    "avg_length": round(float(profile.get("average_length", 0)), 1),
                    "samples": samples,
                }
            columns[column] = entry
        return json.dumps({
            "mode": mode,
            "full_dataset_rows": {"file1": n1_full, "file2": n2_full},
            "total_possible_pairs_full": full_possible,
            "eval_sample_rows": {"file1": len(records1), "file2": len(records2)},
            "target_ratio_range": [ratio_min, ratio_max],
            "note": "Ratios are measured on the evaluation sample and apply to the full data.",
            "selected_fields": selected_fields,
            "columns": columns,
            "allowed_sql_functions": sorted(SAFE_SQL_FUNCTIONS),
            "sql_syntax": (
                "Boolean conditions using l.col and r.col aliases. Quote columns with spaces: l.\"Column Name\". "
                "No SELECT / FROM / JOIN / subqueries. Blanks are NULL, and NULL never equals NULL."
            ),
        }, ensure_ascii=False)

    def test_blocking_plan(rules_json: str) -> str:
        """
        Measure a set of blocking rules. Splink takes the UNION of all rules, so the key
        metric is union_ratio_of_possible; per-rule pair_count shows each rule's raw size.

        Argument: JSON array of {label, sql} objects, e.g.
        [{"label": "Exact last name", "sql": "l.last_name = r.last_name"}]

        Returns a verdict (OK / TOO_FEW_PAIRS / TOO_MANY_PAIRS), the union metrics and per-rule counts.
        """
        progress(50, "Testing blocking rule coverage...")
        plan, metrics, verdict = measure(_parse_rules_argument(rules_json))
        return json.dumps({"verdict": verdict, **metrics}, ensure_ascii=False)

    def accept(raw: dict[str, Any]) -> dict[str, Any]:
        progress(80, "Finalizing blocking rules...")
        if not isinstance(raw, dict):
            raw = {"blocking_rules": raw}
        plan, metrics, verdict = measure(raw.get("blocking_rules") or [])
        if verdict != "OK":
            raise ValueError(f"Rejected. {verdict} Call test_blocking_plan before submitting again.")
        by_sql = {item["sql"]: item for item in metrics["rules"]}
        for rule in plan["blocking_rules"]:
            rule["estimated_pairs"] = int(by_sql.get(rule["sql"], {}).get("pair_count", 0) * pair_scale)
        plan["blocking_metrics"] = metrics
        plan["notes"] = raw.get("notes", []) if isinstance(raw.get("notes", []), list) else []
        return plan

    def submit_blocking_plan(plan_json: str) -> str:
        """
        Call this when your blocking plan is final. This is the ONLY valid way to finish.

        Expected JSON:
        {"blocking_rules": [{"label": "...", "sql": "l.col = r.col", "reason": "why this rule adds recall"}],
         "notes": ["optional"]}

        The rules are validated and measured again; a plan outside the target ratio range is rejected.
        """
        raw = _parse_rules_argument(plan_json) if str(plan_json).lstrip().startswith("[") else extract_json_object(str(plan_json))
        plan = accept(raw if isinstance(raw, dict) else {"blocking_rules": raw})
        submitted.append(plan)
        return json.dumps({
            "status": "accepted",
            "rule_count": len(plan["blocking_rules"]),
            "projected_union_pairs": plan["blocking_metrics"]["projected_union_pairs"],
            "rules": [{"label": r["label"], "estimated_pairs": r["estimated_pairs"]} for r in plan["blocking_rules"]],
        }, ensure_ascii=False)

    system_prompt = (
        "You are a record linkage blocking agent. Craft SQL blocking rules that achieve high recall "
        "despite typos, missing fields, corruption and formatting variation.\n\n"
        "Workflow:\n"
        "1. Call describe_columns to see the available fields, sample values and statistics.\n"
        "2. Design rules that together cover the realistic ways two records of the same entity can differ.\n"
        "3. Call test_blocking_plan to measure coverage.\n"
        f"4. union_ratio_of_possible must land in the target range {ratio_min:.2e} to {ratio_max:.2e}. "
        "If it is too low, add or relax rules; if too high, tighten the broadest rule. "
        "submit_blocking_plan enforces this range.\n\n"
        "Rule crafting:\n"
        "  - High-uniqueness fields are strong anchors on their own. Low-uniqueness fields produce too many "
        "pairs alone; pair them with a high-uniqueness field.\n"
        "  - A field with low completeness cannot be a rule's only anchor; make sure another rule covers "
        "pairs where it is missing.\n"
        "  - Aim for each true match to be caught by at least two independent rules. Two rules on the same "
        "field are not independent.\n"
        "  - Use exact match for stable fields and partial matching (e.g. substr) for fields prone to typos.\n"
        "  - If one rule accounts for most of the union, it is probably too loose.\n\n"
        "SQL syntax:\n"
        "  - Boolean condition only, using l.col and r.col. Quote columns with spaces: l.\"Column Name\".\n"
        "  - Blank values are NULL, so l.col = r.col already excludes blanks.\n"
        "  - Partial matching example: substr(l.col, 1, 4) = substr(r.col, 1, 4)."
    )
    prompt: dict[str, Any] = {
        "task": (
            f"Generate SQL blocking rules for Splink {mode} mode. Call describe_columns, draft rules, "
            "measure them with test_blocking_plan, then call submit_blocking_plan."
        ),
        "mode": mode,
        "selected_fields": selected_fields,
    }
    if available_fields:
        prompt["field_hints"] = [
            {"field": f.get("field"), "label": f.get("label"), "role": f.get("role")}
            for f in available_fields
        ]
    plan = run_agent(
        label="blocking_agent",
        settings=settings,
        tools=[
            agent_tool(describe_columns, cancel_callback),
            agent_tool(test_blocking_plan, cancel_callback),
            agent_tool(submit_blocking_plan, cancel_callback),
        ],
        system_prompt=system_prompt,
        prompt=prompt,
        submitted=submitted,
        accept=accept,
        submit_tool_name="submit_blocking_plan",
        max_attempts=max_attempts,
        progress=progress,
        progress_message="Generating blocking rules...",
        cancel_check=cancel_callback,
        usage=usage,
    )
    progress(95, "Blocking rules ready.")
    return plan
