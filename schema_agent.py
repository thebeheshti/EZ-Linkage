import functools
import inspect
import json
import math
import re
import sys
from datetime import datetime
from typing import Any, Callable

import httpx

from agent_runtime import LLMSettings, agent_tool, run_agent
from llm_client import tls_verify

# Token hints used to detect name-component column headers (for header inference only)
NAME_COMPONENT_HINTS: dict[str, tuple[str, ...]] = {
    "FirstName": ("first", "fname", "firstname", "givenname", "given", "forename"),
    "MiddleName": ("middle", "mname", "middlename", "mi", "middleinitial"),
    "LastName": ("last", "lname", "lastname", "surname", "familyname", "family", "sn"),
}

class _HelperProxy:
    """Lets LLM-generated custom transforms use helpers.fn() or helpers['fn']() interchangeably."""

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]) -> None:
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name: str) -> Any:
        d = object.__getattribute__(self, "_d")
        if name in d:
            return d[name]
        raise AttributeError(f"helpers has no function '{name}'. Available: {sorted(d)}")

    def __getitem__(self, key: str) -> Any:
        return object.__getattribute__(self, "_d")[key]

    def __contains__(self, key: str) -> bool:
        return key in object.__getattribute__(self, "_d")

ALLOWED_OPERATIONS = {
    "copy",
    "join",
    "normalize_name",
    "split_full_name_first",
    "split_full_name_middle",
    "split_full_name_last",
    "normalize_date",
    "digits_only",
    "normalize_sex",
    "normalize_icd_code",
    "custom_python",
}

SAFE_CUSTOM_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "AttributeError": AttributeError,
    "BaseException": BaseException,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "float": float,
    "IndexError": IndexError,
    "int": int,
    "isinstance": isinstance,
    "KeyError": KeyError,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "pow": pow,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError,
    "zip": zip,
}

def normalize_api_base_url(api_url: str | None) -> str:
    """Strip trailing slashes and /chat/completions suffix if accidentally included."""
    base = (api_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base

def resolve_models_url(api_url: str | None) -> str:
    base = normalize_api_base_url(api_url)
    return f"{base}/models"

def _is_certificate_error(exc: Exception) -> bool:
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower()

def ensure_api_connection(api_url: str | None, api_key: str | None = None) -> None:
    models_url = resolve_models_url(api_url)
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = httpx.get(models_url, timeout=8, headers=headers, verify=tls_verify(), follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = ""
        try:
            body = exc.response.json()
            detail = ((body.get("error") or {}).get("message")
                      or body.get("message")
                      or body.get("error")
                      or "")
        except Exception:
            detail = (exc.response.text or "")[:400]
        msg = f"AI API returned HTTP {status} for {models_url}"
        if detail:
            msg += f": {detail}"
        raise ValueError(msg) from exc
    except httpx.TimeoutException:
        raise ValueError(
            f"AI API at {models_url} did not respond within 8 seconds. "
            "Check the Base URL or try again."
        )
    except httpx.ConnectError as exc:
        if _is_certificate_error(exc):
            raise ValueError(
                f"The AI service at {normalize_api_base_url(api_url)} uses a certificate this computer does not trust. "
                "If your organization issued it, install it in this computer's trusted certificate authorities, "
                "or ask whoever runs the service for it."
            ) from exc
        raise ValueError(
            f"Could not connect to the AI service at {normalize_api_base_url(api_url)}. "
            "Check that the Base URL in Settings is correct and that the service is running."
        ) from exc  # the low-level connection error is kept in the app log via the exception chain
    except httpx.HTTPError as exc:
        raise ValueError(f"AI API request failed for {models_url}: {exc}") from exc

def _normalized_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())

def _looks_like_full_name_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    return "fullname" in token or ("name" in token and not any(
        hint in token for hints in NAME_COMPONENT_HINTS.values() for hint in hints
    ))

def _looks_like_sex_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    sex_hints = ("sex", "gender", "gendercd", "gendercode", "sexcd", "sexcode")
    return any(hint in token for hint in sex_hints)

def _looks_like_date_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    date_hints = (
        "date", "dob", "birthdt", "birthdate", "admitdate", "admitdt",
        "dischargedate", "dischargedt", "dxdate", "servicedate", "encounterdate",
    )
    return any(hint in token for hint in date_hints)

def _looks_like_ssn_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    ssn_hints = ("ssn", "socsec", "socialsecurity", "socialsecuritynumber")
    return any(hint in token for hint in ssn_hints)

def _looks_like_icd_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    icd_hints = ("icd", "dxcode", "diagcode", "diagnosiscode", "dx", "diagnosis")
    return any(hint in token for hint in icd_hints)

def _looks_like_name_header(source_column: str) -> bool:
    token = _normalized_token(source_column)
    return _looks_like_full_name_header(source_column) or any(
        hint in token
        for hints in NAME_COMPONENT_HINTS.values()
        for hint in hints
    )

def _strip_wrapping_quotes(value: str) -> str:
    text = (value or "").strip()
    pairs = {'"': '"', "'": "'", "`": "`", "\u201c": "\u201d", "\u2018": "\u2019"}
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in pairs.items():
            if text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
                changed = True
    return text

def _join_last_name_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    surname_particles = {
        "al", "ap", "ben", "bin", "da", "dal", "de", "del", "dela", "della",
        "der", "di", "dos", "du", "el", "la", "le", "lo", "mac", "mc", "san",
        "st", "st.", "van", "von",
    }
    if len(parts) <= 1:
        return " ".join(parts)
    last_parts = [parts[-1]]
    index = len(parts) - 2
    while index >= 0 and re.sub(r"[^a-z]", "", parts[index].lower()) in surname_particles:
        last_parts.insert(0, parts[index])
        index -= 1
    return " ".join(last_parts)

def _split_name_tokens_standard(parts: list[str]) -> tuple[str, str, str]:
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    last = _join_last_name_parts(parts)
    last_tokens = last.split()
    first = parts[0]
    middle_tokens = parts[1: len(parts) - len(last_tokens)]
    return first, " ".join(middle_tokens), last

def _split_name_tokens_reversed(parts: list[str]) -> tuple[str, str, str]:
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[1], "", parts[0]
    last = " ".join(parts[:-2]) if len(parts) > 2 else parts[0]
    first = parts[-2]
    middle = " ".join(parts[-1:]) if len(parts) > 2 else ""
    return first, middle, last

def _looks_like_multi_token_surname(text: str) -> bool:
    parts = [part for part in clean_name_tokens(text) if part]
    if len(parts) <= 1:
        return False
    normalized_parts = [re.sub(r"[^a-z]", "", part.lower()) for part in parts]
    surname_particles = {
        "al", "ap", "ben", "bin", "da", "dal", "de", "del", "dela", "della",
        "der", "di", "dos", "du", "el", "la", "le", "lo", "mac", "mc", "san",
        "st", "van", "von",
    }
    return normalized_parts[0] in surname_particles or any(part in surname_particles for part in normalized_parts[:-1])

def clean_name_tokens(text: str) -> list[str]:
    suffixes = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}
    tokens = [part.strip(" '\"") for part in text.split() if part.strip(" '\"")]
    return [part for part in tokens if re.sub(r"[^a-z0-9]", "", part.lower()) not in suffixes]

def split_full_name(value: str) -> tuple[str, str, str]:
    cleaned = _strip_wrapping_quotes(value)
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "", "", ""

    delimited_parts = [part.strip() for part in re.split(r"[|;]+", cleaned) if part.strip()]
    if len(delimited_parts) >= 2:
        pieces = [" ".join(clean_name_tokens(part)) for part in delimited_parts]
        pieces = [part for part in pieces if part]
        if len(pieces) == 2:
            left_tokens = clean_name_tokens(pieces[0])
            right_tokens = clean_name_tokens(pieces[1])
            if len(left_tokens) > 1 and len(right_tokens) == 1:
                return right_tokens[0], "", " ".join(left_tokens)
            if _looks_like_multi_token_surname(pieces[0]) and right_tokens:
                return right_tokens[0], " ".join(right_tokens[1:]), pieces[0]
            if len(left_tokens) == 1 and len(right_tokens) > 1:
                return left_tokens[0], " ".join(right_tokens[:-1]), right_tokens[-1]
            return pieces[0], "", pieces[1]
        if len(pieces) >= 3:
            middle_token = _normalized_token(pieces[1]) if len(pieces) > 1 else ""
            last_token = _normalized_token(pieces[2]) if len(pieces) > 2 else ""
            if len(middle_token) == 1 and len(last_token) > 1:
                return pieces[0], pieces[1], pieces[2]
            if len(last_token) == 1 and len(middle_token) > 1:
                return pieces[-2], pieces[-1], " ".join(pieces[:-2])
            return pieces[0], " ".join(pieces[1:-1]), pieces[-1]

    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    comma_parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(comma_parts) >= 2:
        normalized_parts = [_normalized_token(part) for part in comma_parts]
        if normalized_parts and len(set(normalized_parts)) == 1:
            cleaned = comma_parts[0]
            comma_parts = [comma_parts[0]]
    if len(comma_parts) >= 2:
        left_tokens = clean_name_tokens(comma_parts[0])
        right_tokens = clean_name_tokens(" ".join(comma_parts[1:]))
        if left_tokens and right_tokens:
            return right_tokens[0], " ".join(right_tokens[1:]), " ".join(left_tokens)

    parts = clean_name_tokens(cleaned.replace(",", " "))
    if not parts:
        return "", "", ""
    if len(parts) >= 3 and len(parts[-1]) == 1 and len(parts[0]) > 1 and len(parts[1]) > 1:
        return _split_name_tokens_reversed(parts)
    return _split_name_tokens_standard(parts)

DATE_ORDERS = ("mdy", "dmy")
_UNAMBIGUOUS_DATE_FORMATS = ["%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"]
_ORDERED_DATE_FORMATS = {
    "mdy": ["%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y"],
    "dmy": ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y"],
}
_DAY_MONTH_PATTERN = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\s*$")
# Share of contradicting values tolerated as data-entry errors before a column counts as mixed.
DATE_ORDER_CONFLICT_TOLERANCE = 0.02


def normalize_date(value: str, order: str = "mdy") -> str:
    """Standardize to YYYY-MM-DD, reading day/month-first values in the given order.

    A value that does not parse in that order (including a bare year) is kept
    as-is rather than guessed; downstream it counts as an invalid date, not a match.
    """
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in _UNAMBIGUOUS_DATE_FORMATS + _ORDERED_DATE_FORMATS.get(order, _ORDERED_DATE_FORMATS["mdy"]):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def date_order_evidence(values: list[str]) -> dict[str, Any]:
    """Work out a column's day/month order from all of its values.

    25/04/2001 proves day-first and 04/25/2001 proves month-first; 05/04/2001
    proves nothing. The verdict is "mdy" or "dmy" when the evidence agrees (a few
    contradicting values are tolerated as entry errors), "mixed" when it conflicts,
    "ambiguous" when no value decides it, and "none" when the column has no
    day/month-first values at all (for example ISO dates).
    """
    month_first = day_first = ambiguous = 0
    for value in values:
        match = _DAY_MONTH_PATTERN.match(str(value or ""))
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 and 1 <= second <= 12:
            day_first += 1
        elif second > 12 and 1 <= first <= 12:
            month_first += 1
        elif 1 <= first <= 12 and 1 <= second <= 12:
            ambiguous += 1
    proofs = month_first + day_first
    if proofs:
        majority = "mdy" if month_first >= day_first else "dmy"
        minority = min(month_first, day_first)
        verdict = majority if minority / proofs <= DATE_ORDER_CONFLICT_TOLERANCE else "mixed"
    else:
        verdict = "ambiguous" if ambiguous else "none"
    return {
        "verdict": verdict,
        "values_proving_month_first": month_first,
        "values_proving_day_first": day_first,
        "ambiguous_values": ambiguous,
    }


def resolve_date_orders(
    specs: dict[str, dict[str, Any]],
    rows: list[dict[str, str]],
    label: str,
    strict: bool,
) -> list[str]:
    """Fix the day/month order of every normalize_date spec from the column's data.

    strict (an agent's own plan): a stated order that the data contradicts, or a
    column the data cannot decide without a stated order, is an error the agent
    must fix. Not strict (columns the agent never mapped): undecidable columns
    fall back to month-first. Returns warnings for the user.
    """
    warnings: list[str] = []
    errors: list[str] = []
    for field, spec in specs.items():
        if spec.get("operation") != "normalize_date":
            continue
        column = spec["source_columns"][0]
        evidence = date_order_evidence([row.get(column, "") for row in rows])
        spec["date_order_evidence"] = evidence
        stated = str(spec.get("date_order") or "").strip().lower() or None
        if stated == "auto":
            stated = None
        if stated is not None and stated not in DATE_ORDERS:
            errors.append(f"{field}: date_order must be 'mdy' or 'dmy', not {spec.get('date_order')!r}.")
            continue
        verdict = evidence["verdict"]
        contradicting = min(evidence["values_proving_month_first"], evidence["values_proving_day_first"])
        if verdict in DATE_ORDERS:
            if stated and stated != verdict and strict:
                errors.append(
                    f"{field}: the data in {column!r} proves {verdict} ({evidence}), but the plan says {stated}. "
                    "Remove date_order or correct it."
                )
                continue
            spec["date_order"] = verdict
            if contradicting:
                warnings.append(
                    f"{label} {column}: read as {verdict}; {contradicting} value(s) contradict that order and are treated as invalid dates."
                )
        elif verdict == "none":
            spec["date_order"] = stated or "mdy"
        elif stated:
            spec["date_order"] = stated
            reason = "every value is ambiguous (like 05/04/2001)" if verdict == "ambiguous" else "the values disagree"
            warnings.append(f"{label} {column}: the day/month order cannot be proven because {reason}; using {stated} as chosen by the agent.")
        elif strict:
            errors.append(
                f"{field}: the day/month order of {column!r} cannot be determined from the data ({evidence}). "
                "Set \"date_order\": \"mdy\" or \"dmy\" in this field's spec, based on context such as the other file's format."
            )
        else:
            spec["date_order"] = "mdy"
            warnings.append(f"{label} {column}: the day/month order cannot be determined from the data; assumed month-first (mdy).")
    if errors:
        raise ValueError("Plan rejected: " + " | ".join(errors))
    return warnings


def normalize_sex(value: str) -> str:
    stripped = re.sub(r"\s+", "", (value or "").strip())
    if stripped in {"1", "01"}:
        return "M"
    if stripped in {"2", "02"}:
        return "F"
    alpha = re.sub(r"[^a-z]", "", stripped.lower())
    if alpha in {"m", "male", "man"}:
        return "M"
    if alpha in {"f", "female", "woman"}:
        return "F"
    return alpha.upper()

def normalize_name(value: str) -> str:
    text = _strip_wrapping_quotes(value)
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    parts = clean_name_tokens(re.sub(r"[|;,]+", " ", text))
    def _cap(token: str) -> str:
        pieces = re.split(r"([\-'])", token)
        return "".join(part.capitalize() if part not in {"-", "'"} else part for part in pieces)
    return " ".join(_cap(part) for part in parts)

def digits_only(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits == "999999999":
        return ""
    return digits

def _infer_header_operation(header: str) -> str:
    if _looks_like_date_header(header):
        return "normalize_date"
    if _looks_like_ssn_header(header):
        return "digits_only"
    if _looks_like_sex_header(header):
        return "normalize_sex"
    if _looks_like_icd_header(header):
        return "normalize_icd_code"
    if _looks_like_name_header(header):
        return "normalize_name"
    return "copy"

def normalize_icd_code(value: str) -> str:
    """Upper-case and strip punctuation. The full code is kept so E11.65 and E11.9 stay distinct."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").strip().upper())

OPERATION_GUIDE = {
    "copy": "Use for any column that needs no transformation; copies the value as-is.",
    "join": "Builds one output field from several source columns (e.g. street + city + state + zip into Address). Joins non-empty values with a space. Use this, not custom_python, to combine columns.",
    "normalize_name": "Use for any name column (first, last, middle, full). Removes noise, standardizes casing, strips suffixes.",
    "split_full_name_first": "Use when one column holds a combined full name (e.g. 'John A. Smith'). Extracts the first name. Always pair with split_full_name_middle and split_full_name_last on the same source column.",
    "split_full_name_middle": "Same source column as split_full_name_first; extracts the middle name.",
    "split_full_name_last": "Same source column as split_full_name_first; extracts the last name. Do NOT use on an already-separate last-name column; use copy instead.",
    "normalize_date": "Use for any date column. Standardizes to YYYY-MM-DD. The day/month order is detected from all of the column's values. Only when every value is ambiguous (like 05/04/2001), or the values disagree, add \"date_order\": \"mdy\" or \"dmy\" to the spec, chosen from context. Values that are not full dates are left unchanged.",
    "digits_only": "Use for SSN and other numeric identifiers. Strips every non-digit character.",
    "normalize_sex": "Use for sex/gender columns. Normalizes to 'M' or 'F' (ISO 5218 codes 1 and 2 are read as M and F).",
    "normalize_icd_code": "Use for ICD / diagnosis code columns. Upper-cases and strips punctuation; the full code is kept.",
    "custom_python": "Last resort for logic no built-in operation can handle. Must be tested with test_field_transform first; the exact tested code is the only code submit will accept.",
}

# ── Custom (LLM-written) transforms ──────────────────────────────────────────
# The regex blocklist and restricted builtins below are a guard rail against
# accidental damage, not a security boundary. The step budget stops runaway
# loops written in Python; it cannot interrupt a single long C-level call.

CUSTOM_TRANSFORM_FILENAME = "<custom_preprocessing_transform>"
CUSTOM_TRANSFORM_STEP_LIMIT = 20_000  # traced Python lines per transformed value
CUSTOM_HELPERS: dict[str, Any] = {}  # filled in below once the helper functions exist

class TransformStepLimitExceeded(Exception):
    pass

def _clean_custom_transform_code(code: str) -> str:
    code = str(code or "").strip()
    if "\\n" in code and "\n" not in code:
        code = code.replace("\\n", "\n")
    fenced = re.match(r"^```(?:python|py)?\s*(.*?)\s*```$", code, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        code = fenced.group(1).strip()
    return code

def _check_custom_code_text(code: str) -> None:
    if not code:
        raise ValueError("Custom transform code is empty.")
    code_no_comments = re.sub(r'#[^\n]*', '', code)
    banned_patterns = [
        r"^\s*import\b",
        r"^\s*from\s+\S+\s+import\b",
        r"\bopen\s*\(",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"__",
        r"\bglobals\s*\(",
        r"\blocals\s*\(",
        r"\bcompile\s*\(",
        r"\binput\s*\(",
    ]
    for pattern in banned_patterns:
        if re.search(pattern, code_no_comments, flags=re.MULTILINE):
            raise ValueError(f"Custom transform code contains a blocked construct: {pattern}")

def _resolve_custom_transform(namespace: dict[str, Any]) -> Any:
    transform = namespace.get("transform")
    if callable(transform):
        return transform
    user_callables = [
        value for name, value in namespace.items()
        if callable(value) and not str(name).startswith("_")
    ]
    if len(user_callables) == 1:
        return user_callables[0]
    raise ValueError(
        "Custom transform code must define transform(value, row, helpers), "
        "or exactly one callable helper that can be used as the transform."
    )

def _custom_call_style(transform: Any) -> str:
    try:
        parameters = list(inspect.signature(transform).parameters.values())
    except (TypeError, ValueError):
        parameters = []
    if len(parameters) >= 3:
        return "value_row_helpers"
    if len(parameters) == 2:
        names = [parameter.name.lower() for parameter in parameters]
        if "helpers" in names[1]:
            return "row_helpers" if ("row" in names[0] or "record" in names[0]) else "value_helpers"
        if "row" in names[0] or "record" in names[0]:
            return "row_helpers"
        return "value_row"
    if len(parameters) == 1:
        name = parameters[0].name.lower()
        return "row" if ("row" in name or "record" in name) else "value"
    return "none"

@functools.lru_cache(maxsize=256)
def _compiled_custom_transform(code: str) -> tuple[Any, str]:
    """Compile once per distinct code string, not once per row."""
    _check_custom_code_text(code)
    namespace: dict[str, Any] = {}
    exec(
        compile(code, CUSTOM_TRANSFORM_FILENAME, "exec"),
        {"__builtins__": SAFE_CUSTOM_BUILTINS, "re": re, "datetime": datetime, "math": math},
        namespace,
    )
    transform = _resolve_custom_transform(namespace)
    return transform, _custom_call_style(transform)

def _call_with_step_budget(fn: Callable[..., Any], *args: Any) -> Any:
    steps = 0

    def local_trace(frame, event, arg):
        nonlocal steps
        if event == "line":
            steps += 1
            if steps > CUSTOM_TRANSFORM_STEP_LIMIT:
                raise TransformStepLimitExceeded(
                    f"Custom transform exceeded {CUSTOM_TRANSFORM_STEP_LIMIT} steps on one value (likely an endless loop)."
                )
        return local_trace

    def global_trace(frame, event, arg):
        return local_trace if frame.f_code.co_filename == CUSTOM_TRANSFORM_FILENAME else None

    previous = sys.gettrace()
    sys.settrace(global_trace)
    try:
        return fn(*args)
    finally:
        sys.settrace(previous)

def validate_custom_transform_code(code: str) -> str:
    """Return the cleaned code, or raise if it cannot be compiled into a transform."""
    cleaned = _clean_custom_transform_code(code)
    _compiled_custom_transform(cleaned)
    return cleaned

def run_custom_transform(code: str, value: Any, row: dict[str, str], helpers: dict[str, Any] | None = None) -> str:
    transform, style = _compiled_custom_transform(_clean_custom_transform_code(code))
    proxy = _HelperProxy(helpers if helpers is not None else CUSTOM_HELPERS)
    args = {
        "value_row_helpers": (value, row, proxy),
        "row_helpers": (row, proxy),
        "value_helpers": (value, proxy),
        "value_row": (value, row),
        "row": (row,),
        "value": (value,),
        "none": (),
    }[style]
    result = _call_with_step_budget(transform, *args)
    return "" if result is None else str(result)

# ── Plan validation ──────────────────────────────────────────────────────────

SINGLE_SOURCE_OPERATIONS = ALLOWED_OPERATIONS - {"join", "custom_python"}
PLACEHOLDER_SOURCE_VALUES = {"n/a", "na", "none", "null", "missing", "unknown", ""}
ROLES = {"primary", "supporting"}

def _clean_role(value: Any) -> str:
    role = str(value or "supporting").strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be 'primary' or 'supporting', not {value!r}.")
    return role

def _validate_field_spec(field: str, spec: Any, headers: list[str]) -> dict[str, Any]:
    """Return a cleaned, executable spec or raise ValueError saying exactly why not."""
    if not isinstance(spec, dict):
        raise ValueError(f"{field}: spec must be an object.")
    operation = spec.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(f"{field}: operation {operation!r} is not one of {sorted(ALLOWED_OPERATIONS)}.")
    source_columns = spec.get("source_columns", [])
    if not isinstance(source_columns, list) or not source_columns:
        raise ValueError(f"{field}: source_columns must be a non-empty list of exact header names.")
    if any(str(column).strip().lower() in PLACEHOLDER_SOURCE_VALUES for column in source_columns):
        raise ValueError(f"{field}: source_columns contains a placeholder instead of a real header.")
    if operation in SINGLE_SOURCE_OPERATIONS and len(source_columns) != 1:
        raise ValueError(f"{field}: {operation} requires exactly one source column.")
    missing = [str(column) for column in source_columns if column not in headers]
    if missing:
        raise ValueError(f"{field}: source column(s) {missing} do not exist. Available headers: {headers}")
    cleaned = dict(spec)
    cleaned["source_columns"] = [str(column) for column in source_columns]
    if operation == "custom_python":
        custom_code = str(spec.get("custom_code") or "").strip()
        if not custom_code:
            raise ValueError(f"{field}: custom_python requires custom_code.")
        cleaned["custom_code"] = validate_custom_transform_code(custom_code)
    return cleaned

def validate_plan(plan: dict[str, Any], headers: list[str], rows: list[dict[str, str]] | None = None, label: str = "File") -> dict[str, Any]:
    """Validate a single-file plan. With `rows`, also fixes each date column's day/month order from the data."""
    if not isinstance(plan, dict):
        raise ValueError("AI preprocessing plan must be a JSON object.")
    fields = plan.get("fields")
    additional_fields = plan.get("additional_fields")
    if not isinstance(fields, dict):
        fields = {}
    merged_fields = {**fields, **additional_fields} if isinstance(additional_fields, dict) else dict(fields)
    cleaned_fields: dict[str, Any] = {}
    errors: list[str] = []
    for field, spec in merged_fields.items():
        try:
            cleaned_spec = _validate_field_spec(str(field), spec, headers)
            cleaned_spec["role"] = _clean_role(spec.get("role"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        cleaned_spec["ambiguity_reason"] = str(spec.get("ambiguity_reason") or "").strip()
        alternatives = spec.get("alternative_interpretations", [])
        cleaned_spec["alternative_interpretations"] = [str(item).strip() for item in alternatives] if isinstance(alternatives, list) else []
        cleaned_fields[str(field)] = cleaned_spec
    if errors:
        raise ValueError("Plan rejected: " + " | ".join(errors))
    planned_fields = set(cleaned_fields)
    used_headers = {source for spec in cleaned_fields.values() for source in spec.get("source_columns", [])}
    if not plan.get("suppress_passthrough"):
        for header in headers:
            if not header or header in used_headers or header in cleaned_fields:
                continue
            inferred_operation = _infer_header_operation(header)
            output_field = header
            counter = 2
            while output_field in cleaned_fields:
                output_field = f"{header}_{counter}"
                counter += 1
            cleaned_fields[output_field] = {
                "operation": inferred_operation,
                "source_columns": [header],
                "role": "supporting",
                "reason": f"Preserved as an available source variable for preview and manual mapping using {inferred_operation}.",
                "ambiguity_reason": "",
                "alternative_interpretations": [],
            }
    if not cleaned_fields:
        raise ValueError("AI preprocessing plan did not produce any usable fields.")
    if rows is not None:
        plan["date_warnings"] = resolve_date_orders(
            {f: spec for f, spec in cleaned_fields.items() if f in planned_fields}, rows, label, strict=True,
        ) + resolve_date_orders(
            {f: spec for f, spec in cleaned_fields.items() if f not in planned_fields}, rows, label, strict=False,
        )
    original_field_keys = set(fields.keys())
    plan["fields"] = cleaned_fields
    plan["additional_fields"] = {field: cleaned_fields[field] for field in cleaned_fields if field not in original_field_keys}
    selected = plan.get("standardized_fields", [])
    if not isinstance(selected, list):
        raise ValueError("AI preprocessing plan standardized_fields must be a list.")
    plan["standardized_fields"] = [str(field) for field in selected if str(field) in cleaned_fields]
    plan.setdefault("notes", [])
    return plan

def validate_joint_plan(plan: dict[str, Any], headers1: list[str], headers2: list[str]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("AI joint preprocessing plan must be a JSON object.")
    shared_fields = plan.get("shared_fields")
    if not isinstance(shared_fields, dict) or not shared_fields:
        raise ValueError("AI joint preprocessing plan is missing a shared_fields object.")
    cleaned_shared: dict[str, Any] = {}
    errors: list[str] = []
    for field, spec in shared_fields.items():
        field = str(field)
        if field in RESERVED_FIELD_NAMES:
            errors.append(f"{field}: name is reserved ({sorted(RESERVED_FIELD_NAMES)}); rename it.")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{field}: shared field spec must be an object.")
            continue
        try:
            cleaned_file1 = _validate_field_spec(f"{field} (file1)", spec.get("file1"), headers1)
            cleaned_file2 = _validate_field_spec(f"{field} (file2)", spec.get("file2"), headers2)
            role = _clean_role(spec.get("role"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        cleaned_shared[field] = {
            **spec,
            "label": str(spec.get("label") or field),
            "suggested": bool(spec.get("suggested", False)),
            "reason": str(spec.get("reason") or ""),
            "role": role,
            "file1": cleaned_file1,
            "file2": cleaned_file2,
        }
    if errors:
        raise ValueError("Plan rejected: " + " | ".join(errors))
    plan["shared_fields"] = cleaned_shared
    selected = plan.get("standardized_fields", [])
    if not isinstance(selected, list):
        raise ValueError("AI joint preprocessing plan standardized_fields must be a list.")
    plan["standardized_fields"] = [str(field) for field in selected if str(field) in cleaned_shared]
    plan.setdefault("notes", [])
    return plan

def _resolve_joint_date_orders(plan: dict[str, Any], rows1: list[dict[str, str]], rows2: list[dict[str, str]]) -> None:
    plan["date_warnings"] = [
        *resolve_date_orders({f: spec["file1"] for f, spec in plan["shared_fields"].items()}, rows1, "File 1", strict=True),
        *resolve_date_orders({f: spec["file2"] for f, spec in plan["shared_fields"].items()}, rows2, "File 2", strict=True),
    ]


RESERVED_FIELD_NAMES = {"record_id", "source_dataset", "source_row_index", "source_id"}

def apply_shared_field_mapping(rows: list[dict[str, str]], shared_plan: dict[str, Any], side: str) -> list[dict[str, str]]:
    if not rows or not shared_plan:
        return rows
    shared_fields = shared_plan.get("shared_fields", {})
    if not isinstance(shared_fields, dict):
        return rows
    mapped_rows: list[dict[str, str]] = []
    for row in rows:
        mapped = dict(row)
        for shared_name, spec in shared_fields.items():
            if not isinstance(spec, dict):
                continue
            source_field = spec.get("file1_field") if side == "file1" else spec.get("file2_field")
            if source_field and source_field in row:
                mapped[shared_name] = row.get(source_field, "")
            elif shared_name in row:
                mapped[shared_name] = row.get(shared_name, "")
        mapped_rows.append(mapped)
    return mapped_rows

def aligned_schema_pairs(headers1: list[str], headers2: list[str]) -> list[tuple[str, str]]:
    if len(headers1) != len(headers2):
        return []
    pairs = list(zip(headers1, headers2))
    if not pairs:
        return []
    if all(_normalized_token(left) == _normalized_token(right) for left, right in pairs):
        return pairs
    return []

CUSTOM_HELPERS.update({
    "normalize_name": normalize_name,
    "normalize_date": normalize_date,
    "digits_only": digits_only,
    "normalize_sex": normalize_sex,
    "normalize_icd_code": normalize_icd_code,
    "split_full_name": split_full_name,
    "clean_name_tokens": clean_name_tokens,
})

def apply_operation(row: dict[str, str], operation: str, source_columns: list[str]) -> str:
    values = [row.get(column, "") for column in source_columns]
    first = values[0] if values else ""
    if operation == "copy":
        return first
    if operation == "join":
        return " ".join(value for value in values if value).strip()
    if operation == "normalize_name":
        return normalize_name(first)
    if operation.startswith("split_full_name_"):
        first_name, middle, last = split_full_name(first)
        normalized_raw = re.sub(r"\s+", " ", _strip_wrapping_quotes(first)).strip()
        raw_tokens = [part for part in re.split(r"[\s,|;]+", normalized_raw) if part]
        if operation == "split_full_name_first":
            return first_name or first.strip()
        if operation == "split_full_name_middle":
            return middle or (first.strip() if len(raw_tokens) <= 1 else "")
        return last or (first.strip() if len(raw_tokens) <= 1 else "")
    if operation == "normalize_date":
        return normalize_date(first)
    if operation == "digits_only":
        return digits_only(first)
    if operation == "normalize_sex":
        return normalize_sex(first)
    if operation == "normalize_icd_code":
        return normalize_icd_code(first)
    raise ValueError(f"Unsupported preprocessing operation: {operation}")

def apply_field_spec(row: dict[str, str], spec: dict[str, Any]) -> str:
    operation = spec["operation"]
    source_columns = spec["source_columns"]
    if operation == "custom_python":
        values = [row.get(column, "") for column in source_columns]
        custom_value: Any = values if len(values) > 1 else (values[0] if values else "")
        return run_custom_transform(spec.get("custom_code", ""), custom_value, row)
    if operation == "normalize_date":
        return normalize_date(row.get(source_columns[0], ""), spec.get("date_order") or "mdy")
    return apply_operation(row, operation, source_columns)

def apply_preprocessing_plan(
    rows: list[dict[str, str]],
    id_output_column: str,
    plan: dict[str, Any],
    errors: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Apply a validated plan to every row.

    A custom transform that fails on a row yields "" for that value. Failures are
    counted into `errors` ({field: {"count", "examples"}}) when the caller passes
    it; without it the first failure is raised, so nothing is lost silently.
    """
    fields = plan.get("fields", {})
    normalized_rows: list[dict[str, str]] = []
    for row_index, row in enumerate(rows, start=1):
        normalized: dict[str, str] = {id_output_column: str(row_index)}
        for field, spec in fields.items():
            try:
                normalized[field] = apply_field_spec(row, spec)
            except Exception as exc:
                if errors is None:
                    raise ValueError(f"Field {field} failed on row {row_index}: {exc}") from exc
                entry = errors.setdefault(field, {"count": 0, "examples": []})
                entry["count"] += 1
                if len(entry["examples"]) < 3:
                    source_values = {column: row.get(column, "") for column in spec.get("source_columns", [])}
                    entry["examples"].append({"row": row_index, "input": source_values, "error": str(exc)})
                normalized[field] = ""
        normalized_rows.append(normalized)
    return normalized_rows

def transform_error_warnings(errors: dict[str, dict[str, Any]], total_rows: int, label: str) -> list[str]:
    return [
        f"{label}: custom transform for {field} failed on {entry['count']} of {total_rows} rows; those values were left blank."
        for field, entry in sorted(errors.items())
    ]

# Agents may accept a plan whose custom transforms fail on at most this share of rows.
MAX_TRANSFORM_ERROR_RATE = 0.01

def _execution_report(rows: list[dict[str, str]], plan: dict[str, Any], label: str) -> list[dict[str, str]]:
    """Run the plan over every row before accepting it; reject when custom code fails too often."""
    errors: dict[str, dict[str, Any]] = {}
    output = apply_preprocessing_plan(rows, "source_id", plan, errors=errors)
    too_many = {
        field: entry for field, entry in errors.items()
        if rows and entry["count"] / len(rows) > MAX_TRANSFORM_ERROR_RATE
    }
    if too_many:
        details = "; ".join(
            f"{field} failed on {entry['count']}/{len(rows)} rows, e.g. {entry['examples'][0]}"
            for field, entry in too_many.items()
        )
        raise ValueError(f"{label}: custom transform errors on real data. Fix and retest: {details}")
    return output

def normalize_rows(
    rows: list[dict[str, str]],
    id_output_column: str,
    plan: dict[str, Any],
    errors: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    plan = validate_plan(plan, list(rows[0].keys()) if rows else [])
    return apply_preprocessing_plan(rows, id_output_column, plan, errors=errors), plan

def summarize_field_missingness(rows: list[dict[str, str]], fields: list[str]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    total_rows = len(rows)
    if total_rows <= 0:
        return summary
    for field in fields:
        missing_count = 0
        for row in rows:
            value = row.get(field, "")
            if not str(value or "").strip():
                missing_count += 1
        non_missing = total_rows - missing_count
        summary[field] = {
            "total_rows": total_rows,
            "non_missing_count": non_missing,
            "missing_count": missing_count,
            "non_missing_rate": round(non_missing / total_rows, 3),
            "missing_rate": round(missing_count / total_rows, 3),
        }
    return summary

def profile_fields(rows: list[dict[str, str]], fields: list[str]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    total_rows = len(rows)
    if total_rows <= 0:
        return profiles
    missingness = summarize_field_missingness(rows, fields)
    for field in fields:
        values = [str(row.get(field, "") or "").strip() for row in rows]
        non_missing_values = [value for value in values if value]
        distinct_values = {value.lower() for value in non_missing_values}
        avg_length = round(sum(len(value) for value in non_missing_values) / len(non_missing_values), 2) if non_missing_values else 0.0
        uniqueness_rate = round(len(distinct_values) / len(non_missing_values), 3) if non_missing_values else 0.0
        profiles[field] = {
            **missingness.get(field, {}),
            "distinct_count": len(distinct_values),
            "uniqueness_rate": uniqueness_rate,
            "average_length": avg_length,
        }
    return profiles

def split_joint_preprocessing_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_fields = plan.get("shared_fields", {})
    file1_fields: dict[str, Any] = {}
    file2_fields: dict[str, Any] = {}
    for field, spec in shared_fields.items():
        file1_fields[field] = dict(spec.get("file1", {}))
        file2_fields[field] = dict(spec.get("file2", {}))
    shared_suggested = plan.get("standardized_fields", [])
    file1_plan = {
        "fields": file1_fields,
        "standardized_fields": [field for field in shared_suggested if field in file1_fields],
        "notes": list(plan.get("notes", [])) if isinstance(plan.get("notes", []), list) else [],
        "suppress_passthrough": False,
    }
    file2_plan = {
        "fields": file2_fields,
        "standardized_fields": [field for field in shared_suggested if field in file2_fields],
        "notes": list(plan.get("notes", [])) if isinstance(plan.get("notes", []), list) else [],
        "suppress_passthrough": False,
    }
    return file1_plan, file2_plan

def shared_mapping_from_joint_plan(plan: dict[str, Any]) -> dict[str, Any]:
    shared_fields = plan.get("shared_fields", {}) if isinstance(plan, dict) else {}
    mapped: dict[str, Any] = {}
    for field, spec in shared_fields.items():
        if not isinstance(spec, dict):
            continue
        mapped[field] = {
            "label": str(spec.get("label") or field),
            "suggested": bool(spec.get("suggested", False)) or field in plan.get("standardized_fields", []),
            "reason": str(spec.get("reason") or ""),
            "role": str(spec.get("role", "")),
            "file1_field": field,
            "file2_field": field,
            "file1_alternates": [],
            "file2_alternates": [],
        }
    return {
        "shared_fields": mapped,
        "standardized_fields": [field for field in plan.get("standardized_fields", []) if field in mapped],
        "notes": list(plan.get("notes", [])) if isinstance(plan.get("notes", []), list) else [],
        "ai_log_path": plan.get("ai_log_path", ""),
    }

def available_field_options(plan: dict[str, Any]) -> list[dict[str, Any]]:
    fields = plan.get("fields") or plan.get("shared_fields", {})
    options = []
    for field in sorted(fields):
        spec = fields[field]
        if ("file1" in spec and "file2" in spec) or ("file1_field" in spec and "file2_field" in spec):
            reason = spec.get("reason", "")
            suggested = bool(spec.get("suggested", False)) or field in plan.get("standardized_fields", [])
        else:
            reason = spec.get("reason", "")
            suggested = field in plan.get("standardized_fields", [])
        options.append({
            "field": field,
            "label": field,
            "suggested": suggested,
            "reason": reason,
            "role": str(spec.get("role", "")),
            "file1_field": spec.get("file1_field", ""),
            "file2_field": spec.get("file2_field", ""),
            "file1_alternates": spec.get("file1_alternates", []),
            "file2_alternates": spec.get("file2_alternates", []),
        })
    return options

# ── Shared agent tools ───────────────────────────────────────────────────────

TEST_TRANSFORM_FULL_RUN_ROWS = 5000

ROLE_GUIDANCE = (
    "Role assignment for each field's 'role' key (must be exactly 'primary' or 'supporting'):\n"
    "  - 'primary': strongly and uniquely identifies an entity (high uniqueness, an identity attribute).\n"
    "  - 'supporting': context that does not identify on its own (low cardinality or categorical).\n"
    "Use uniqueness_rate from the profiles to decide."
)

CUSTOM_CODE_RULES = (
    "  - custom_python: define transform(value, row, helpers). No imports, no eval/exec, no double-underscore access. "
    "Test it with test_field_transform first; submit only accepts custom_code that was tested verbatim, "
    "and rejects code that fails on more than 1% of rows.\n"
)

def spread_sample(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    """Rows spread across the file, not just the first few."""
    if len(rows) <= count:
        return list(rows)
    step = len(rows) / count
    return [rows[int(index * step)] for index in range(count)]

def _distinct_samples(rows: list[dict[str, str]], column: str, limit: int = 15) -> list[str]:
    seen: set[str] = set()
    samples: list[str] = []
    for row in spread_sample(rows, 2000):
        value = str(row.get(column, "")).strip()
        if value and value not in seen:
            seen.add(value)
            samples.append(value)
            if len(samples) >= limit:
                break
    return samples

FileTable = dict[int, tuple[list[dict[str, str]], list[str]]]

def _file_entry(files: FileTable, file_number: Any) -> tuple[list[dict[str, str]], list[str]]:
    try:
        number = int(file_number)
    except (TypeError, ValueError):
        number = -1
    if number not in files:
        raise ValueError(f"file_number must be one of {sorted(files)}.")
    return files[number]

def _make_sample_values_tool(files: FileTable, cancel_check: Callable[[], bool] | None):
    def sample_values(file_number: int, columns_json: str) -> str:
        """
        Get more example values for specific columns before deciding how to transform them.
        Pass file_number (1, or 2 when there are two files) and a JSON array of column names,
        e.g. file_number=1, columns_json='["PatientName", "DOB"]'.
        Returns up to 15 distinct non-empty values per column, drawn from across the file.
        """
        rows, headers = _file_entry(files, file_number)
        try:
            columns = json.loads(columns_json)
        except (json.JSONDecodeError, ValueError, TypeError):
            columns = [columns_json]
        if not isinstance(columns, list):
            columns = [columns]
        result: dict[str, Any] = {}
        for column in map(str, columns):
            if column not in headers:
                result[column] = {"error": f"Column '{column}' not in file {file_number}. Available: {headers}"}
            else:
                result[column] = _distinct_samples(rows, column)
        return json.dumps({"file": file_number, "samples": result}, ensure_ascii=False)

    return agent_tool(sample_values, cancel_check)

def _make_test_transform_tool(
    files: FileTable,
    tested_code: set[str],
    submit_name: str,
    progress: Callable[[int, str], None],
    cancel_check: Callable[[], bool] | None,
):
    def test_field_transform(file_number: int, source_column: str, operation: str, custom_code: str = "", date_order: str = "") -> str:
        """
        Run a transformation on real data and see the result before putting it in your plan.
        Required before submitting any custom_python transform.

        Arguments:
        - file_number: 1, or 2 when there are two files
        - source_column: exact column name in that file
        - operation: one of the allowed operations
        - custom_code: required when operation is custom_python. Define transform(value, row, helpers).
          helpers: normalize_name, normalize_date, digits_only, normalize_sex, normalize_icd_code,
          split_full_name, clean_name_tokens. No imports, no eval/exec, no double-underscore access.
        - date_order: only for normalize_date, and only when the column's order cannot be proven
          from its values: "mdy" or "dmy". The result reports the evidence found in the column.

        Returns input/output pairs for 8 rows spread across the file, plus how many rows
        (of up to 5000) raised an error. Check that the outputs are semantically right,
        not only that the code ran.
        """
        rows, headers = _file_entry(files, file_number)
        progress(50, "Testing field transformation...")
        spec: dict[str, Any] = {"operation": operation, "source_columns": [source_column]}
        if operation == "custom_python":
            spec["custom_code"] = custom_code
        validated_spec = _validate_field_spec(source_column, spec, headers)
        if operation == "custom_python":
            tested_code.add(validated_spec["custom_code"])
        date_warnings: list[str] = []
        if operation == "normalize_date":
            validated_spec["date_order"] = date_order
            date_warnings = resolve_date_orders({source_column: validated_spec}, rows, f"File {file_number}", strict=False)
        examples = []
        for row in spread_sample(rows, 8):
            try:
                output = apply_field_spec(row, validated_spec)
            except Exception as exc:
                output = f"ERROR: {exc}"
            examples.append({"input": row.get(source_column, ""), "output": output})
        errors: dict[str, dict[str, Any]] = {}
        sample = spread_sample(rows, TEST_TRANSFORM_FULL_RUN_ROWS)
        apply_preprocessing_plan(sample, "source_id", {"fields": {source_column: validated_spec}}, errors=errors)
        error_entry = errors.get(source_column, {"count": 0, "examples": []})
        result: dict[str, Any] = {
            "file": file_number,
            "source_column": source_column,
            "operation": operation,
            "examples": examples,
            "rows_checked": len(sample),
            "rows_with_errors": error_entry["count"],
            "error_examples": error_entry["examples"],
        }
        if operation == "normalize_date":
            result["date_order_used"] = validated_spec["date_order"]
            result["date_order_evidence"] = validated_spec["date_order_evidence"]
            result["date_warnings"] = date_warnings
        if operation == "custom_python":
            result["custom_code"] = validated_spec["custom_code"]
            result["note"] = f"Copy this custom_code exactly into your {submit_name} call for this field."
        return json.dumps(result, ensure_ascii=False)

    return agent_tool(test_field_transform, cancel_check)

def _require_tested_custom_code(specs: list[tuple[str, dict[str, Any]]], tested_code: set[str]) -> None:
    untested = [
        label for label, spec in specs
        if spec.get("operation") == "custom_python" and spec.get("custom_code") not in tested_code
    ]
    if untested:
        raise ValueError(
            f"custom_python code for {untested} was not tested. Call test_field_transform with that exact code first."
        )

def _settings(api_url, model, api_key, temperature, top_p, extra_body, reasoning_effort) -> LLMSettings:
    return LLMSettings(
        api_url=normalize_api_base_url(api_url), model=model, api_key=api_key,
        temperature=temperature, top_p=top_p, extra_body=extra_body,
        reasoning_effort=reasoning_effort,
    )

def _progress_fn(progress_callback: Any) -> Callable[[int, str], None]:
    def progress(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)
    return progress

# ── Single-file standardization (dedupe mode) ────────────────────────────────

def build_preprocessing_plan(
    rows: list[dict[str, str]],
    api_url: str = "",
    model: str = "",
    api_key: str = "",
    input_label: str = "input",
    max_attempts: int = 3,
    progress_callback: Any = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    progress = _progress_fn(progress_callback)
    if not rows:
        raise ValueError("Cannot build an AI preprocessing plan for an empty file.")
    headers = list(rows[0].keys())
    settings = _settings(api_url, model, api_key, temperature, top_p, extra_body, reasoning_effort)
    progress(0, "Connecting to AI service...")
    ensure_api_connection(settings.api_url, api_key)

    files: FileTable = {1: (rows, headers)}
    submitted: list[dict[str, Any]] = []
    tested_code: set[str] = set()

    def inspect_schema() -> str:
        """
        Call this first. Returns headers, per-column profiles (completeness, uniqueness,
        average length), 8 sample rows spread across the file, the allowed operations
        and a guide to when each operation applies.
        """
        progress(15, "Profiling fields and inspecting sample values...")
        return json.dumps({
            "input_label": input_label,
            "headers": headers,
            "sample_rows": spread_sample(rows, 8),
            "field_profiles": profile_fields(rows, headers),
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
            "operation_guide": OPERATION_GUIDE,
        }, ensure_ascii=False)

    def accept(raw: dict[str, Any]) -> dict[str, Any]:
        progress(80, "Validating field standardization plan...")
        validated = validate_plan(raw, headers, rows=rows, label="File 1")
        _require_tested_custom_code(list(validated["fields"].items()), tested_code)
        validated["preview_rows"] = _execution_report(rows, validated, "File 1")[:3]
        return validated

    def submit_plan(fields: dict, standardized_fields: list, additional_fields: dict | None = None, notes: list | None = None) -> str:
        """
        Call this when your preprocessing plan is complete. This is the ONLY valid way to finish.

        Arguments:
        - fields: dict mapping OutputFieldName -> spec with
            "operation": one of the allowed operations,
            "source_columns": [exact source header(s)],
            "reason": why this field is useful,
            "role": "primary" or "supporting",
            "custom_code": only for custom_python; the exact code you tested.
            "date_order": only for normalize_date when the data cannot prove it ("mdy" or "dmy").
        - standardized_fields: ALL field names from fields, in the source schema's natural order.
          Do not filter; the blocking and matching agents decide which fields to use.
        - additional_fields: same spec format, for columns to keep but not link on.
        - notes: optional assumptions or caveats.

        The plan is validated and run over every row. On failure you get the exact reasons; fix and call again.
        """
        validated = accept({
            "fields": fields or {},
            "additional_fields": additional_fields or {},
            "standardized_fields": standardized_fields or [],
            "notes": notes or [],
        })
        submitted.append(validated)
        return json.dumps({
            "status": "accepted",
            "output_fields": list(validated["fields"].keys()),
            "standardized_fields": validated["standardized_fields"],
            "preview_rows": validated["preview_rows"],
        }, ensure_ascii=False)

    tools = [
        agent_tool(inspect_schema, cancel_callback),
        _make_sample_values_tool(files, cancel_callback),
        _make_test_transform_tool(files, tested_code, "submit_plan", progress, cancel_callback),
        agent_tool(submit_plan, cancel_callback),
    ]
    system_prompt = (
        "You are a data preprocessing agent for record linkage. Map every source column to a "
        "standardized output field using the right transformation operation.\n\n"
        "Workflow:\n"
        "1. Call inspect_schema to see the columns, statistics and operation guide.\n"
        "2. Call sample_values (file_number=1) for any column where you need more examples.\n"
        "3. Call test_field_transform for every custom_python transform, and for any built-in "
        "operation you are unsure about. Check that the outputs are correct.\n"
        "4. Call submit_plan. This is the ONLY valid way to finish.\n\n"
        "Key rules:\n"
        "  - Every source column must appear in fields or additional_fields.\n"
        "  - Use exact source column names; never invent columns.\n"
        "  - Do not add person-style fields (FirstName, DOB, ...) unless the source data contains them.\n"
        "  - Output names as separate FirstName, MiddleName, LastName fields, never one combined field. "
        "Split a combined full-name column with split_full_name_first/middle/last; copy separate name columns.\n"
        "  - Do not extract city/state/zip from a combined address string; only output those when separate columns exist.\n"
        + CUSTOM_CODE_RULES + "\n" + ROLE_GUIDANCE
    )
    plan = run_agent(
        label=input_label,
        settings=settings,
        tools=tools,
        system_prompt=system_prompt,
        prompt={
            "task": f"Inspect this file's columns, decide how each should be transformed, test any custom transforms, then call submit_plan. File: {input_label}.",
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
        },
        submitted=submitted,
        accept=accept,
        submit_tool_name="submit_plan",
        max_attempts=max_attempts,
        progress=progress,
        progress_message="Planning field standardization...",
        cancel_check=cancel_callback,
        usage=usage,
    )
    progress(95, "Field standardization plan ready.")
    return plan

# ── Two files with different schemas ─────────────────────────────────────────

def build_joint_preprocessing_plan(
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]],
    api_url: str = "",
    model: str = "",
    api_key: str = "",
    input_label: str = "joint",
    max_attempts: int = 3,
    progress_callback: Any = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    progress = _progress_fn(progress_callback)
    if not rows1:
        raise ValueError("Cannot build a joint AI preprocessing plan for an empty File 1.")
    if not rows2:
        raise ValueError("Cannot build a joint AI preprocessing plan for an empty File 2.")
    headers1 = list(rows1[0].keys())
    headers2 = list(rows2[0].keys())
    settings = _settings(api_url, model, api_key, temperature, top_p, extra_body, reasoning_effort)
    progress(0, "Connecting to AI service...")
    ensure_api_connection(settings.api_url, api_key)

    files: FileTable = {1: (rows1, headers1), 2: (rows2, headers2)}
    submitted: list[dict[str, Any]] = []
    tested_code: set[str] = set()

    def compare_schemas() -> str:
        """
        Call this first. Returns both files side by side: headers, 6 sample rows spread across
        each file, per-column profiles (completeness, uniqueness, average length), the allowed
        operations and a guide to each. A shared concept must be derivable from BOTH files
        using their real source columns.
        """
        progress(15, "Comparing field structures across both files...")
        return json.dumps({
            "file1": {"headers": headers1, "sample_rows": spread_sample(rows1, 6), "field_profiles": profile_fields(rows1, headers1)},
            "file2": {"headers": headers2, "sample_rows": spread_sample(rows2, 6), "field_profiles": profile_fields(rows2, headers2)},
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
            "operation_guide": OPERATION_GUIDE,
            "address_harmonization_note": (
                "If one file has split address columns and the other one combined address column: "
                "use join on the split side (source_columns = [street, city, state, zip]) and copy on the combined side."
            ),
        }, ensure_ascii=False)

    def accept(raw: dict[str, Any]) -> dict[str, Any]:
        progress(80, "Validating shared field schema...")
        validated = validate_joint_plan(raw, headers1, headers2)
        _resolve_joint_date_orders(validated, rows1, rows2)
        _require_tested_custom_code(
            [(f"{field} (file{side})", spec[f"file{side}"]) for field, spec in validated["shared_fields"].items() for side in (1, 2)],
            tested_code,
        )
        file1_plan, file2_plan = split_joint_preprocessing_plan(validated)
        validated["preview"] = {
            "file1": _execution_report(rows1, file1_plan, "File 1")[:3],
            "file2": _execution_report(rows2, file2_plan, "File 2")[:3],
        }
        return validated

    def submit_joint_plan(shared_fields: dict, standardized_fields: list, notes: list | None = None) -> str:
        """
        Call this when your joint preprocessing plan is complete. This is the ONLY valid way to finish.

        Arguments:
        - shared_fields: dict mapping SharedFieldName -> spec with
            "label": human-readable name,
            "suggested": true/false,
            "reason": why this field is shared and useful,
            "role": "primary" or "supporting",
            "file1": {"operation": "...", "source_columns": ["exact file1 header"], "custom_code": "only for custom_python"},
            "file2": {"operation": "...", "source_columns": ["exact file2 header"], "custom_code": "only for custom_python"}
          Add "date_order": "mdy" or "dmy" to a normalize_date file spec only when its data cannot prove the order.
        - standardized_fields: ALL SharedFieldName values, in the source schema's natural order. Do not filter.
        - notes: optional notes or caveats.

        Field names record_id, source_dataset, source_row_index and source_id are reserved.
        The plan is validated and run over every row of both files. On failure you get every
        reason at once; fix them all and call again.
        """
        validated = accept({
            "shared_fields": shared_fields or {},
            "standardized_fields": standardized_fields or [],
            "notes": notes or [],
        })
        submitted.append(validated)
        return json.dumps({
            "status": "accepted",
            "shared_fields": list(validated["shared_fields"].keys()),
            "standardized_fields": validated["standardized_fields"],
            "preview": validated["preview"],
        }, ensure_ascii=False)

    tools = [
        agent_tool(compare_schemas, cancel_callback),
        _make_sample_values_tool(files, cancel_callback),
        _make_test_transform_tool(files, tested_code, "submit_joint_plan", progress, cancel_callback),
        agent_tool(submit_joint_plan, cancel_callback),
    ]
    system_prompt = (
        "You are a joint preprocessing agent for record linkage. Design one shared output schema "
        "both files can conform to, then give per-file transformation specs.\n\n"
        "Workflow:\n"
        "1. Call compare_schemas to see both files side by side.\n"
        "2. Identify shared concepts. A shared concept must be derivable from BOTH files' real columns.\n"
        "3. Call sample_values for any column where you need more examples.\n"
        "4. Call test_field_transform for every custom_python transform, and for any built-in "
        "operation you are unsure about.\n"
        "5. Call submit_joint_plan. An 'accepted' result is the ONLY valid way to finish.\n\n"
        "Key rules:\n"
        "  - Use one shared output field name for the same concept in both files.\n"
        "  - Use exact source column names on each side; never invent columns.\n"
        "  - Output names as separate First, Middle and Last fields, never one combined name field. "
        "Split a combined full-name column with split_full_name_first/middle/last; copy separate name columns.\n"
        "  - Address harmonization: join on the split side, copy on the combined side.\n"
        + CUSTOM_CODE_RULES + "\n" + ROLE_GUIDANCE
    )
    plan = run_agent(
        label=input_label,
        settings=settings,
        tools=tools,
        system_prompt=system_prompt,
        prompt={
            "task": "Inspect both files' schemas, design a shared output schema, test any custom transforms, then call submit_joint_plan.",
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
        },
        submitted=submitted,
        accept=accept,
        submit_tool_name="submit_joint_plan",
        max_attempts=max_attempts,
        progress=progress,
        progress_message="Planning shared field schema...",
        cancel_check=cancel_callback,
        usage=usage,
    )
    progress(95, "Shared field schema ready.")
    return plan

# ── Two files with the same schema ───────────────────────────────────────────

def build_aligned_schema_cleaning_plan(
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]],
    header_pairs: list[tuple[str, str]],
    api_url: str = "",
    model: str = "",
    api_key: str = "",
    input_label: str = "aligned_schema_cleaning",
    max_attempts: int = 3,
    progress_callback: Any = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    progress = _progress_fn(progress_callback)
    headers1 = [left for left, _ in header_pairs]
    headers2 = [right for _, right in header_pairs]
    settings = _settings(api_url, model, api_key, temperature, top_p, extra_body, reasoning_effort)
    progress(0, "Connecting to AI service...")
    ensure_api_connection(settings.api_url, api_key)

    files: FileTable = {1: (rows1, headers1), 2: (rows2, headers2)}
    submitted: list[dict[str, Any]] = []
    tested_code: set[str] = set()

    def to_joint_plan(candidate: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            raise ValueError("Aligned-schema cleaning plan must be a JSON object.")
        fields = candidate.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("Aligned-schema cleaning plan must include a fields object.")
        selected = candidate.get("standardized_fields", [])
        selected = [str(field) for field in selected] if isinstance(selected, list) else []
        errors: list[str] = []
        shared_fields: dict[str, Any] = {}
        for header1, header2 in header_pairs:
            spec = fields.get(header1)
            if spec is None and header2 != header1:
                spec = fields.get(header2)
            if not isinstance(spec, dict):
                errors.append(f"{header1}: missing cleaning decision.")
                continue
            source_columns = spec.get("source_columns") or [header1]
            if source_columns not in ([header1], [header2]):
                errors.append(f"{header1}: must use its own column as the only source.")
                continue
            shared_fields[header1] = {
                "label": str(spec.get("label") or header1),
                "suggested": bool(spec.get("suggested", header1 in selected)),
                "reason": str(spec.get("reason") or "Aligned source field; cleaning-only preprocessing."),
                "role": spec.get("role"),
                "file1": {**spec, "source_columns": [header1]},
                "file2": {**spec, "source_columns": [header2]},
            }
        extra = sorted(set(map(str, fields)) - set(headers1) - set(headers2))
        if extra:
            errors.append(f"Unknown fields {extra}: do not add or rename fields.")
        if errors:
            raise ValueError("Plan rejected: " + " | ".join(errors))
        notes = candidate.get("notes", [])
        return validate_joint_plan({
            "shared_fields": shared_fields,
            "standardized_fields": [field for field in selected if field in shared_fields],
            "notes": ["Schemas already aligned; agent chose field-level cleaning operations only.", *(notes if isinstance(notes, list) else [])],
            "schema_already_aligned": True,
        }, headers1, headers2)

    def inspect_aligned_schema() -> str:
        """
        Call this first. Returns the aligned field pairs, 6 sample rows spread across each file,
        per-column profiles for each file, and the allowed operations. The schemas are already
        aligned: only decide how to clean each existing field. Use copy when no cleaning is needed.
        """
        progress(18, "Inspecting shared fields...")
        return json.dumps({
            "aligned_fields": [{"file1": h1, "file2": h2} for h1, h2 in header_pairs],
            "file1": {"sample_rows": spread_sample(rows1, 6), "field_profiles": profile_fields(rows1, headers1)},
            "file2": {"sample_rows": spread_sample(rows2, 6), "field_profiles": profile_fields(rows2, headers2)},
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
            "operation_guide": OPERATION_GUIDE,
        }, ensure_ascii=False)

    def accept(raw: dict[str, Any]) -> dict[str, Any]:
        progress(80, "Validating field cleaning plan...")
        validated = to_joint_plan(raw)
        _resolve_joint_date_orders(validated, rows1, rows2)
        _require_tested_custom_code(
            [(f"{field} (file{side})", spec[f"file{side}"]) for field, spec in validated["shared_fields"].items() for side in (1, 2)],
            tested_code,
        )
        file1_plan, file2_plan = split_joint_preprocessing_plan(validated)
        validated["preview"] = {
            "file1": _execution_report(rows1, file1_plan, "File 1")[:3],
            "file2": _execution_report(rows2, file2_plan, "File 2")[:3],
        }
        return validated

    def submit_cleaning_plan(fields: dict, standardized_fields: list, notes: list | None = None) -> str:
        """
        Call this when your cleaning plan is complete. This is the ONLY valid way to finish.

        Arguments:
        - fields: dict mapping EVERY aligned field name -> spec with
            "operation": one of the allowed operations,
            "source_columns": [that same field name],
            "suggested": true/false,
            "reason": why this operation fits,
            "role": "primary" or "supporting",
            "custom_code": only for custom_python; the exact code you tested.
            "date_order": only for normalize_date when the data cannot prove it ("mdy" or "dmy").
        - standardized_fields: ALL field names, in the source schema's natural order. Do not filter.
        - notes: optional notes.

        Do not add, rename, split or merge fields. The plan is validated and run over every row
        of both files. On failure you get every reason at once; fix them all and call again.
        """
        validated = accept({
            "fields": fields or {},
            "standardized_fields": standardized_fields or [],
            "notes": notes or [],
        })
        submitted.append(validated)
        return json.dumps({
            "status": "accepted",
            "cleaned_fields": list(validated["shared_fields"].keys()),
            "standardized_fields": validated["standardized_fields"],
            "preview": validated["preview"],
        }, ensure_ascii=False)

    tools = [
        agent_tool(inspect_aligned_schema, cancel_callback),
        _make_sample_values_tool(files, cancel_callback),
        _make_test_transform_tool(files, tested_code, "submit_cleaning_plan", progress, cancel_callback),
        agent_tool(submit_cleaning_plan, cancel_callback),
    ]
    system_prompt = (
        "You are a cleaning-only preprocessing agent. The two files already share an aligned schema. "
        "Your ONLY job is to decide how to clean each existing field; do not create, rename, split or merge fields.\n\n"
        "Workflow:\n"
        "1. Call inspect_aligned_schema to see the aligned fields, statistics and samples.\n"
        "2. Call sample_values for any field where you need more examples.\n"
        "3. Call test_field_transform for every custom_python transform, and for any built-in "
        "operation you are unsure about.\n"
        "4. Call submit_cleaning_plan. This is the ONLY valid way to finish.\n\n"
        "Key rules:\n"
        "  - Every aligned field gets exactly one cleaning operation; use copy when none is needed.\n"
        "  - Each field's source_columns is that same field's name.\n"
        + CUSTOM_CODE_RULES + "\n" + ROLE_GUIDANCE
    )
    plan = run_agent(
        label=input_label,
        settings=settings,
        tools=tools,
        system_prompt=system_prompt,
        prompt={
            "task": "Inspect the aligned schema, choose the right cleaning operation for each field, test any custom transforms, then call submit_cleaning_plan.",
            "aligned_field_count": len(header_pairs),
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
        },
        submitted=submitted,
        accept=accept,
        submit_tool_name="submit_cleaning_plan",
        max_attempts=max_attempts,
        progress=progress,
        progress_message="Planning field cleaning...",
        cancel_check=cancel_callback,
        usage=usage,
    )
    progress(95, "Field cleaning plan ready.")
    return plan
