import time
from typing import List, Dict, Any, Tuple, Optional
from pydantic import ValidationError

from src.models import SettlementRecord
from src.zoho import get_zoho_client, ZohoError
from src.s3_client import upload_digest_csv, upload_per_record_results
from src.database import (
    get_job,
    transition_job_state,
    finalize_job,
    finalize_cancel,
    write_audit_log
)

def is_cancel_requested(job_id: str) -> bool:
    job = get_job(job_id)
    if job and (job.get("cancel_requested") is True or job.get("status") == "CANCELLING"):
        return True
    return False

def run_dry_run_validation(records: List[dict], environment: str, caller: str) -> dict:
    received_count = len(records)
    valid_count = 0
    invalid_count = 0
    per_record_results = []
    errors = []

    for raw_rec in records:
        settlement_id = raw_rec.get("settlement_id", "unknown")
        try:
            SettlementRecord(**raw_rec)
            valid_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "valid",
                "zoho_record_id": None
            })
        except ValidationError as val_err:
            invalid_count += 1
            err_msg = "; ".join([f"{e['loc'][0]}: {e['msg']}" for e in val_err.errors()])
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "invalid",
                "error": err_msg
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": err_msg
            })
        except Exception as e:
            invalid_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "invalid",
                "error": str(e)
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": str(e)
            })

    if received_count == invalid_count:
        final_state = "FAILED"
    elif valid_count == received_count:
        final_state = "SUCCESS"
    else:
        final_state = "PARTIAL_SUCCESS"

    import uuid
    val_id = f"validation_{uuid.uuid4()}"
    write_audit_log(
        target_id=val_id,
        action="validation_requested",
        caller=caller,
        details={"received_count": received_count, "valid_count": valid_count, "invalid_count": invalid_count}
    )

    return {
        "environment": environment,
        "received_count": received_count,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "zoho_updated_count": 0,
        "zoho_failed_count": 0,
        "s3_written": False,
        "s3_key": None,
        "final_state": final_state,
        "per_record_results": per_record_results,
        "errors": errors
    }

def process_bulk_ingest(
    job_id: str,
    environment: str,
    skip_s3: bool,
    records: List[dict],
    caller: str,
    successful_outcomes: Optional[Dict[str, dict]] = None
) -> dict:
    
    received_count = len(records)
    valid_records = []
    per_record_results = []
    errors = []
    
    zoho_updated_count = 0
    zoho_failed_count = 0
    invalid_count = 0
    carried_forward_count = 0

    job = transition_job_state(job_id, ["CREATED"], "VALIDATING", caller=caller)

    for raw_rec in records:
        if is_cancel_requested(job_id):
            finalize_cancel(job_id, {
                "received_count": received_count,
                "invalid_count": invalid_count,
                "valid_count": len(valid_records) + carried_forward_count
            }, caller)
            return get_job(job_id)

        settlement_id = raw_rec.get("settlement_id", "unknown")
        
        if successful_outcomes and settlement_id in successful_outcomes:
            outcome = successful_outcomes[settlement_id]
            zoho_updated_count += 1
            carried_forward_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": outcome["status"],
                "zoho_record_id": outcome["zoho_record_id"]
            })
            continue

        try:
            validated = SettlementRecord(**raw_rec)
            valid_records.append(validated.model_dump())
        except ValidationError as val_err:
            invalid_count += 1
            err_msg = "; ".join([f"{e['loc'][0]}: {e['msg']}" for e in val_err.errors()])
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "invalid",
                "error": err_msg
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": err_msg
            })
        except Exception as e:
            invalid_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "invalid",
                "error": str(e)
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": str(e)
            })

    transition_job_state(
        job_id,
        ["VALIDATING"],
        "VALIDATING",
        additional_updates={
            "valid_count": len(valid_records) + carried_forward_count,
            "invalid_count": invalid_count
        },
        caller=caller
    )

    if is_cancel_requested(job_id):
        finalize_cancel(job_id, {
            "received_count": received_count,
            "invalid_count": invalid_count,
            "valid_count": len(valid_records) + carried_forward_count,
            "zoho_updated_count": zoho_updated_count,
            "zoho_failed_count": zoho_failed_count
        }, caller)
        return get_job(job_id)

    transition_job_state(job_id, ["VALIDATING"], "ZOHOW_WRITING", caller=caller)

    zoho_client = get_zoho_client(environment)

    for rec in valid_records:
        if is_cancel_requested(job_id):
            finalize_cancel(job_id, {
                "received_count": received_count,
                "invalid_count": invalid_count,
                "valid_count": len(valid_records) + carried_forward_count,
                "zoho_updated_count": zoho_updated_count,
                "zoho_failed_count": zoho_failed_count
            }, caller)
            upload_per_record_results(job_id, per_record_results)
            return get_job(job_id)

        settlement_id = rec["settlement_id"]
        try:
            zoho_res = zoho_client.upsert_settlement(rec)
            zoho_updated_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": zoho_res["status"],
                "zoho_record_id": zoho_res["zoho_record_id"]
            })
        except ZohoError as ze:
            zoho_failed_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "failed",
                "error": ze.code
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": ze.code
            })
        except Exception as ex:
            zoho_failed_count += 1
            per_record_results.append({
                "settlement_id": settlement_id,
                "status": "failed",
                "error": str(ex)
            })
            errors.append({
                "settlement_id": settlement_id,
                "error": str(ex)
            })

    transition_job_state(
        job_id,
        ["ZOHOW_WRITING"],
        "ZOHOW_WRITING",
        additional_updates={
            "zoho_updated_count": zoho_updated_count,
            "zoho_failed_count": zoho_failed_count
        },
        caller=caller
    )

    if is_cancel_requested(job_id):
        finalize_cancel(job_id, {
            "received_count": received_count,
            "invalid_count": invalid_count,
            "valid_count": len(valid_records) + carried_forward_count,
            "zoho_updated_count": zoho_updated_count,
            "zoho_failed_count": zoho_failed_count
        }, caller)
        upload_per_record_results(job_id, per_record_results)
        return get_job(job_id)

    transition_job_state(job_id, ["ZOHOW_WRITING"], "S3_WRITING", caller=caller)

    upload_per_record_results(job_id, per_record_results)

    s3_written = False
    s3_key = None
    if not skip_s3:
        import datetime as dt
        date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        try:
            s3_key = upload_digest_csv(job_id, date_str, records, per_record_results)
            s3_written = True
        except Exception as s3_err:
            errors.append({"error": f"S3 CSV write failed: {str(s3_err)}"})

    if received_count == (invalid_count + zoho_failed_count):
        final_state = "FAILED"
    elif zoho_updated_count == received_count:
        final_state = "SUCCESS"
    else:
        final_state = "PARTIAL_SUCCESS"

    final_updates = {
        "zoho_updated_count": zoho_updated_count,
        "zoho_failed_count": zoho_failed_count,
        "s3_written": s3_written,
        "s3_key": s3_key,
        "errors": errors
    }

    finalize_job(job_id, final_state, final_updates, caller=caller)
    
    completed_job = get_job(job_id)
    completed_job["per_record_results"] = per_record_results
    return completed_job

