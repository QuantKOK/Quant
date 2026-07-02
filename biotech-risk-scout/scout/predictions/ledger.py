"""Tamper-evident, append-only ledger for clinical-trial risk predictions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 1
RECEIPT_VERSION = 1
GENESIS_HASH = "0" * 64
NCT_ID_PATTERN = re.compile(r"^NCT\d{8}$")
_LEDGER_LOCK = threading.Lock()


class PredictionLedgerError(ValueError):
    """Raised when a prediction draft or ledger violates the ledger contract."""


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for record hashes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_prediction_record(
    draft: dict[str, Any],
    previous_hash: str = GENESIS_HASH,
    *,
    created_at: str | None = None,
    prediction_id: str | None = None,
) -> dict[str, Any]:
    """Validate a prediction draft and return a hash-chained ledger record."""
    if not isinstance(draft, dict):
        raise PredictionLedgerError("prediction draft must be a JSON object")

    target = _require_dict(draft, "target")
    prediction = _require_dict(draft, "prediction")
    explanation = _require_dict(draft, "explanation")

    created = _parse_timestamp(created_at or _utc_now_iso(), "created_at")
    evidence_as_of = _parse_timestamp(
        _require_text(draft, "evidence_as_of"),
        "evidence_as_of",
    )
    if evidence_as_of > created:
        raise PredictionLedgerError("evidence_as_of cannot be later than created_at")

    nct_id = _require_text(target, "nct_id").upper()
    if not NCT_ID_PATTERN.fullmatch(nct_id):
        raise PredictionLedgerError("target.nct_id must match NCT followed by 8 digits")

    probability = _require_probability(prediction, "probability_of_success")
    risk_factors = explanation.get("risk_factors")
    if not isinstance(risk_factors, list) or not risk_factors or not all(
        isinstance(item, str) and item.strip() for item in risk_factors
    ):
        raise PredictionLedgerError("explanation.risk_factors must be a list of non-empty strings")
    evidence_urls = explanation.get("evidence_urls")
    if not isinstance(evidence_urls, list) or not evidence_urls or not all(
        isinstance(item, str) and item.strip() for item in evidence_urls
    ):
        raise PredictionLedgerError("explanation.evidence_urls must be a list of non-empty strings")
    if not all(_is_http_url(item.strip()) for item in evidence_urls):
        raise PredictionLedgerError("explanation.evidence_urls must use http or https")

    forecast_horizon_date = _require_text(prediction, "forecast_horizon_date")
    _parse_date(forecast_horizon_date, "prediction.forecast_horizon_date")
    estimated_completion_date = target.get("estimated_completion_date")
    if estimated_completion_date is not None:
        if not isinstance(estimated_completion_date, str):
            raise PredictionLedgerError(
                "target.estimated_completion_date must be an ISO date or null"
            )
        _parse_partial_date(
            estimated_completion_date,
            "target.estimated_completion_date",
        )

    if not isinstance(previous_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_hash):
        raise PredictionLedgerError("previous_hash must be a lowercase SHA-256 hex digest")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "prediction_id": prediction_id or str(uuid.uuid4()),
        "created_at": created.isoformat(),
        "evidence_as_of": evidence_as_of.isoformat(),
        "model_version": _require_text(draft, "model_version"),
        "target": {
            "nct_id": nct_id,
            "sponsor": _require_text(target, "sponsor"),
            "trial_title": _require_text(target, "trial_title"),
            "phase": _require_text(target, "phase"),
            "therapeutic_area": _require_text(target, "therapeutic_area"),
            "primary_endpoint": _require_text(target, "primary_endpoint"),
            "estimated_completion_date": estimated_completion_date,
        },
        "prediction": {
            "probability_of_success": probability,
            "probability_of_failure": round(1.0 - probability, 10),
            "outcome_definition": _require_text(prediction, "outcome_definition"),
            "forecast_horizon_date": forecast_horizon_date,
        },
        "explanation": {
            "risk_factors": [item.strip() for item in risk_factors],
            "rationale": _require_text(explanation, "rationale"),
            "evidence_urls": [item.strip() for item in evidence_urls],
        },
        "previous_hash": previous_hash,
    }
    payload["record_hash"] = _record_hash(payload)
    return payload


def append_prediction(
    ledger_path: str,
    draft: dict[str, Any],
    *,
    created_at: str | None = None,
    prediction_id: str | None = None,
) -> dict[str, Any]:
    """Append one validated prediction to a ledger and fsync it."""
    ledger_path = os.path.abspath(ledger_path)
    with _LEDGER_LOCK:
        verification = verify_ledger(ledger_path)
        if not verification["ok"]:
            raise PredictionLedgerError(
                "cannot append to an invalid ledger: " + "; ".join(verification["errors"])
            )
        record = build_prediction_record(
            draft,
            previous_hash=verification["head_hash"],
            created_at=created_at,
            prediction_id=prediction_id,
        )
        last_created_at = verification.get("last_created_at")
        if last_created_at and _parse_timestamp(record["created_at"], "created_at") < _parse_timestamp(
            last_created_at,
            "last_created_at",
        ):
            raise PredictionLedgerError("created_at cannot be earlier than the ledger head")
        if record["prediction_id"] in verification["prediction_ids"]:
            raise PredictionLedgerError(
                f"duplicate prediction_id: {record['prediction_id']}"
            )

        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def verify_ledger(ledger_path: str) -> dict[str, Any]:
    """Verify schema, hashes, order, probabilities, and identifiers."""
    ledger_path = os.path.abspath(ledger_path)
    if not os.path.exists(ledger_path):
        return _verification_result([], GENESIS_HASH, set())

    errors: list[str] = []
    expected_previous = GENESIS_HASH
    prediction_ids: set[str] = set()
    previous_created_at: datetime | None = None
    count = 0

    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            lines = list(handle)
    except OSError as exc:
        return _verification_result([f"could not read ledger: {exc}"], GENESIS_HASH, set())

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            errors.append(f"line {line_number}: blank lines are not allowed")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {line_number}: record must be a JSON object")
            continue

        count += 1
        record_hash = record.get("record_hash")
        if record.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"line {line_number}: unsupported schema_version")
        if record.get("previous_hash") != expected_previous:
            errors.append(f"line {line_number}: previous_hash does not match ledger head")
        if record_hash != _record_hash(record):
            errors.append(f"line {line_number}: record_hash mismatch")

        prediction_id = record.get("prediction_id")
        if not isinstance(prediction_id, str) or not prediction_id:
            errors.append(f"line {line_number}: missing prediction_id")
        elif prediction_id in prediction_ids:
            errors.append(f"line {line_number}: duplicate prediction_id")
        else:
            prediction_ids.add(prediction_id)

        try:
            created = _parse_timestamp(record.get("created_at"), "created_at")
            evidence = _parse_timestamp(record.get("evidence_as_of"), "evidence_as_of")
            if evidence > created:
                errors.append(f"line {line_number}: evidence_as_of is after created_at")
            if previous_created_at is not None and created < previous_created_at:
                errors.append(f"line {line_number}: created_at is earlier than prior record")
            previous_created_at = created
        except PredictionLedgerError as exc:
            errors.append(f"line {line_number}: {exc}")

        _verify_record_fields(record, line_number, errors)
        if isinstance(record_hash, str):
            expected_previous = record_hash

    return _verification_result(
        errors,
        expected_previous,
        prediction_ids,
        count=count,
        last_created_at=previous_created_at.isoformat() if previous_created_at else None,
    )


def build_ledger_receipt(
    ledger_path: str,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a portable receipt for a valid ledger's exact byte content."""
    ledger_path = os.path.abspath(ledger_path)
    generated = _parse_timestamp(generated_at or _utc_now_iso(), "generated_at")

    with _LEDGER_LOCK:
        verification = verify_ledger(ledger_path)
        if not verification["ok"]:
            raise PredictionLedgerError(
                "cannot create a receipt for an invalid ledger: "
                + "; ".join(verification["errors"])
            )
        try:
            with open(ledger_path, "rb") as handle:
                ledger_bytes = handle.read()
        except FileNotFoundError:
            ledger_bytes = b""
        except OSError as exc:
            raise PredictionLedgerError(f"could not read ledger: {exc}") from exc

    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "generated_at": generated.isoformat(),
        "record_count": verification["record_count"],
        "head_hash": verification["head_hash"],
        "last_created_at": verification["last_created_at"],
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
    }
    receipt["receipt_hash"] = hashlib.sha256(
        canonical_json(receipt).encode("utf-8")
    ).hexdigest()
    return receipt


def write_ledger_receipt(
    ledger_path: str,
    output_path: str,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Atomically write a portable verification receipt as canonical JSON."""
    ledger_path = os.path.abspath(ledger_path)
    output_path = os.path.abspath(output_path)
    if ledger_path == output_path:
        raise PredictionLedgerError("receipt output cannot overwrite the ledger")

    receipt = build_ledger_receipt(ledger_path, generated_at=generated_at)
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    temporary_path = f"{output_path}.tmp-{uuid.uuid4()}"
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(receipt))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    except OSError as exc:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise PredictionLedgerError(f"could not write receipt: {exc}") from exc
    return receipt


def _verify_record_fields(
    record: dict[str, Any],
    line_number: int,
    errors: list[str],
) -> None:
    target = record.get("target")
    prediction = record.get("prediction")
    explanation = record.get("explanation")
    for key in ("model_version",):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(f"line {line_number}: {key} must be a non-empty string")
    if not isinstance(target, dict):
        errors.append(f"line {line_number}: target must be an object")
    else:
        nct_id = str(target.get("nct_id", ""))
        if not NCT_ID_PATTERN.fullmatch(nct_id):
            errors.append(f"line {line_number}: invalid target.nct_id")
        for key in (
            "sponsor",
            "trial_title",
            "phase",
            "therapeutic_area",
            "primary_endpoint",
        ):
            if not isinstance(target.get(key), str) or not target[key].strip():
                errors.append(f"line {line_number}: target.{key} must be a non-empty string")
        estimated_completion = target.get("estimated_completion_date")
        if estimated_completion is not None:
            try:
                if not isinstance(estimated_completion, str):
                    raise PredictionLedgerError(
                        "target.estimated_completion_date must be an ISO date or null"
                    )
                _parse_partial_date(
                    estimated_completion,
                    "target.estimated_completion_date",
                )
            except PredictionLedgerError as exc:
                errors.append(f"line {line_number}: {exc}")

    if not isinstance(prediction, dict):
        errors.append(f"line {line_number}: prediction must be an object")
    else:
        success = prediction.get("probability_of_success")
        failure = prediction.get("probability_of_failure")
        if not _is_probability(success) or not _is_probability(failure):
            errors.append(f"line {line_number}: probabilities must be between 0 and 1")
        elif abs(float(success) + float(failure) - 1.0) > 1e-9:
            errors.append(f"line {line_number}: probabilities must sum to 1")
        for key in ("outcome_definition", "forecast_horizon_date"):
            if not isinstance(prediction.get(key), str) or not prediction[key].strip():
                errors.append(
                    f"line {line_number}: prediction.{key} must be a non-empty string"
                )
        try:
            _parse_date(
                prediction.get("forecast_horizon_date"),
                "prediction.forecast_horizon_date",
            )
        except PredictionLedgerError as exc:
            errors.append(f"line {line_number}: {exc}")

    if not isinstance(explanation, dict):
        errors.append(f"line {line_number}: explanation must be an object")
    else:
        if not isinstance(explanation.get("rationale"), str) or not explanation["rationale"].strip():
            errors.append(f"line {line_number}: explanation.rationale must be a non-empty string")
        for key in ("risk_factors", "evidence_urls"):
            values = explanation.get(key)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                errors.append(
                    f"line {line_number}: explanation.{key} must contain non-empty strings"
                )
        urls = explanation.get("evidence_urls")
        if isinstance(urls, list) and urls and not all(
            isinstance(item, str) and _is_http_url(item.strip()) for item in urls
        ):
            errors.append(
                f"line {line_number}: explanation.evidence_urls must use http or https"
            )


def _record_hash(record: dict[str, Any]) -> str:
    hash_input = dict(record)
    hash_input.pop("record_hash", None)
    return hashlib.sha256(canonical_json(hash_input).encode("utf-8")).hexdigest()


def _verification_result(
    errors: list[str],
    head_hash: str,
    prediction_ids: set[str],
    *,
    count: int = 0,
    last_created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "record_count": count,
        "head_hash": head_hash,
        "last_created_at": last_created_at,
        "prediction_ids": prediction_ids,
        "errors": errors,
    }


def _require_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise PredictionLedgerError(f"{key} must be a JSON object")
    return result


def _require_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PredictionLedgerError(f"{key} must be a non-empty string")
    return result.strip()


def _require_probability(value: dict[str, Any], key: str) -> float:
    result = value.get(key)
    if not _is_probability(result):
        raise PredictionLedgerError(f"{key} must be a number between 0 and 1")
    return float(result)


def _is_probability(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 <= float(value) <= 1.0
    )


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PredictionLedgerError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PredictionLedgerError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PredictionLedgerError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise PredictionLedgerError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PredictionLedgerError(f"{field_name} must be an ISO date") from exc


def _parse_partial_date(value: str, field_name: str) -> None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            date.fromisoformat(f"{value}-01")
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise PredictionLedgerError(
            f"{field_name} must be YYYY-MM or YYYY-MM-DD"
        ) from exc


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
