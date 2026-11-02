"""Builds the exact records Splink compares.

Both the blocking agent (when it measures candidate rules) and the linkage run
(when it scores pairs) go through build_splink_inputs, so a rule is evaluated
on the same values and the same NULL handling it will meet in production.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SOURCE_ID_FIELD = "__source_id"
# Columns build_splink_inputs adds itself; never usable as linkage variables.
RESERVED_RECORD_COLUMNS = {"record_id", "source_dataset", "source_row_index", "source_id"}

PHONE_TOKENS = {
    "tel", "telno", "telephone", "telephonenumber",
    "mobile", "mobileno", "mobilenumber",
    "cell", "cellno", "cellnumber", "cellphone",
}


@dataclass
class PreparedRecord:
    source_id: str
    row_index: int
    raw: dict[str, str]
    extras: dict[str, str]


def field_token(field: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(field or "").lower())


def is_phone_token(token: str) -> bool:
    return "phone" in token or token in PHONE_TOKENS


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_sex(value: str) -> str:
    value = normalize_text(value)
    if value in {"m", "male"}:
        return "m"
    if value in {"f", "female"}:
        return "f"
    return value[:1]


def normalize_ssn(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits == "999999999":
        return ""
    return digits


def parse_birthdate(value: str) -> tuple[str, str]:
    value = (value or "").strip()
    if not value:
        return "", ""
    # Only unambiguous layouts: the day/month order of other dates is decided per column
    # during preprocessing, and this layer must not guess it again.
    formats = ["%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"]
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y")
        except ValueError:
            pass
    year_match = re.search(r"(19|20)\d{2}", value)
    return "", year_match.group(0) if year_match else ""


def extract_zip(value: str) -> str:
    zip_match = re.search(r"\b\d{5}(?:-\d{4})?\b", value or "")
    return zip_match.group(0)[:5] if zip_match else ""


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def normalized_extra_values(field: str, value: str) -> dict[str, Any]:
    """Return the field's display value plus derived helper columns (email_user, phone_last4, ...)."""
    token = field_token(field)
    text_value = (value or "").strip()
    if not text_value:
        return {field: ""}
    if "email" in token:
        email = normalize_email(text_value)
        user, _, domain = email.partition("@")
        return {field: email, "email": email, "email_user": user, "email_domain": domain}
    if is_phone_token(token):
        phone = normalize_phone(text_value)
        return {
            field: phone,
            "phone": phone,
            "phone_last7": phone[-7:] if len(phone) >= 7 else "",
            "phone_last4": phone[-4:] if len(phone) >= 4 else "",
        }
    if token in {"city", "state"}:
        return {field: normalize_text(text_value), token: normalize_text(text_value)}
    if token in {"zipcode", "zip", "postalcode", "postal"}:
        zip_value = extract_zip(text_value) or text_value
        return {field: zip_value, "zip_code": zip_value}
    return {field: text_value}


def comparison_value_for_field(field: str, value: str) -> str:
    token = field_token(field)
    text_value = value or ""
    if "email" in token:
        return normalize_email(text_value)
    if is_phone_token(token):
        return normalize_phone(text_value)
    if token in {"socsec", "ssn", "socialsecurity", "socialsecuritynumber"}:
        return normalize_ssn(text_value)
    if token in {"birthdate", "dateofbirth", "dob"} or token.endswith("date"):
        return parse_birthdate(text_value)[0] or text_value.strip()
    if token in {"sex", "gender"}:
        return normalize_sex(text_value)
    if "address" in token or token in {"city", "state"}:
        return normalize_text(text_value)
    if token in {"zipcode", "zip", "postalcode", "postal"}:
        return extract_zip(text_value) or text_value.strip()
    if "name" in token:
        return normalize_text(text_value)
    return text_value.strip()


def comparison_column_for_field(field: str) -> str:
    return field


def is_blank_display_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"null", "none", "nan"}


def prepare_record(row: dict[str, str], id_col: str, row_index: int = 0) -> PreparedRecord:
    return PreparedRecord(
        source_id=row.get(id_col, "") if id_col else "",
        row_index=row_index,
        raw={key: row.get(key, "") for key in row.keys()},
        extras={},
    )


def _build_record(
    row: dict[str, str],
    prepared: PreparedRecord,
    record_id: str,
    source_dataset: str,
    row_index: int,
    selected_fields: list[str] | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "record_id": record_id,
        "source_dataset": source_dataset,
        "source_row_index": row_index,
        "source_id": prepared.source_id,
    }
    real_fields = {field for field in row if field and not field.startswith("__") and field not in RESERVED_RECORD_COLUMNS}
    for field, value in row.items():
        if field not in real_fields:
            continue
        for key, derived in normalized_extra_values(field, value).items():
            # A derived helper column must never overwrite a real source field.
            if key != field and key in real_fields:
                continue
            record[key] = derived
        cval = comparison_value_for_field(field, value)
        record[comparison_column_for_field(field)] = None if is_blank_display_value(cval) else cval
    for field in selected_fields or []:
        if field not in real_fields:
            continue
        comparison_column = comparison_column_for_field(field)
        if is_blank_display_value(record.get(comparison_column)):
            cval = comparison_value_for_field(field, row.get(field, ""))
            record[comparison_column] = None if is_blank_display_value(cval) else cval
    # Splink and DuckDB must see blanks as NULL, never as ''.
    for key, value in list(record.items()):
        if key not in RESERVED_RECORD_COLUMNS and is_blank_display_value(value):
            record[key] = None
    return record


def build_splink_inputs(
    rows1: list[dict[str, str]],
    rows2: list[dict[str, str]] | None,
    mode: str,
    selected_fields: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, PreparedRecord], dict[str, dict[str, str]]]:
    prepared_by_id: dict[str, PreparedRecord] = {}
    raw_by_id: dict[str, dict[str, str]] = {}
    records: list[dict[str, Any]] = []
    sources = [("file1", rows1 or [])]
    if mode != "dedupe":
        sources.append(("file2", rows2 or []))
    for source_dataset, rows in sources:
        id_col = SOURCE_ID_FIELD if rows and SOURCE_ID_FIELD in rows[0] else ""
        for row_index, row in enumerate(rows, start=1):
            prepared = prepare_record(row, id_col, row_index)
            prepared.extras = {field: value for field, value in row.items() if field and not field.startswith("__")}
            record_id = f"{source_dataset}::{row_index}"
            prepared_by_id[record_id] = prepared
            raw_by_id[record_id] = row
            records.append(_build_record(row, prepared, record_id, source_dataset, row_index, selected_fields))
    return records, prepared_by_id, raw_by_id


def records_table(records: list[dict[str, Any]], columns: list[str] | None = None) -> Any:
    """The records as an Arrow table for DuckDB and Splink.

    Every field is a text column with NULL for blanks, including fields that are blank
    in every record, so blocking counts and Splink see exactly the same values and types.
    """
    import pyarrow as pa

    names = list(dict.fromkeys([*(key for record in records for key in record), *(columns or [])]))
    arrays = {}
    for name in names:
        values = [record.get(name) for record in records]
        if name == "source_row_index":
            arrays[name] = pa.array(values, type=pa.int64())
        else:
            arrays[name] = pa.array([None if value is None else str(value) for value in values], type=pa.string())
    return pa.table(arrays)


def record_columns(records: list[dict[str, Any]]) -> list[str]:
    """Columns a blocking rule or comparator may reference."""
    return sorted({key for record in records for key in record.keys()} - RESERVED_RECORD_COLUMNS)
