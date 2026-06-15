import csv
import io
import uuid
import datetime
from fastapi import FastAPI, HTTPException, status, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from typing import Optional, List, Dict, Any

from src.config import ENVIRONMENT
from src.models import IngestRequest, ValidateRequest, CancelRequest, IngestResponse, JobData, JobError, PerRecordResult, ReplayResponse
from src.auth import require_viewer, require_operator, UserIdentity
from src.database import (
    ensure_tables_exist,
    check_idempotency_key,
    create_job,
    get_job,
    get_job_audit_trail,
    list_jobs,
    trigger_cancel_request,
    transition_job_state,
    write_audit_log
)
from src.s3_client import get_s3_client, S3_BUCKET_NAME, fetch_per_record_results
from src.ingest import process_bulk_ingest, run_dry_run_validation

app = FastAPI(
    title="Settlement Ingestion Bulk REST API",
    description="Bulk ingest API with state tracking, signed pagination, cooperative cancellation, and retries.",
    version="1.0.0"
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        loc = " -> ".join(str(l) for l in error.get("loc", []))
        errors.append(f"{loc}: {error.get('msg')}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"status": "error", "message": "Validation Error", "details": errors}
    )

@app.on_event("startup")
def startup_event():
    ensure_tables_exist()

@app.get("/health")
def health():
    return {"status": "healthy", "environment": ENVIRONMENT}

def format_job_response(job: dict, per_record_results: List[dict] = None) -> dict:
    if per_record_results is None:
        per_record_results = fetch_per_record_results(job["job_id"])
        
    return {
        "status": "ok",
        "data": {
            "job_id": job["job_id"],
            "environment": job["environment"],
            "received_count": int(job.get("received_count", 0)),
            "valid_count": int(job.get("valid_count", 0)),
            "invalid_count": int(job.get("invalid_count", 0)),
            "zoho_updated_count": int(job.get("zoho_updated_count", 0)),
            "zoho_failed_count": int(job.get("zoho_failed_count", 0)),
            "s3_written": bool(job.get("s3_written", False)),
            "s3_key": job.get("s3_key"),
            "final_state": job["status"],
            "per_record_results": per_record_results,
            "errors": job.get("errors", [])
        }
    }

@app.post("/bulk/ingestion/ingest", response_model=IngestResponse)
async def ingest_jobs(
    request: IngestRequest,
    user: UserIdentity = Depends(require_operator)
):
    if len(request.records) > 10000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large. Maximum 10,000 records allowed per bulk job."
        )

    if request.confirm_live is not True:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="Precondition Failed: confirm_live must be explicitly set to true to execute live writes."
        )

    existing_job = check_idempotency_key(request.idempotency_key)
    if existing_job:
        created_at_str = existing_job.get("created_at")
        if created_at_str:
            created_at = datetime.datetime.fromisoformat(created_at_str)
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - created_at).total_seconds() < 48 * 3600:
                per_records = fetch_per_record_results(existing_job["job_id"])
                return format_job_response(existing_job, per_records)

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    raw_records = [r.model_dump() for r in request.records]
    
    create_job(
        job_id=job_id,
        environment=request.environment,
        idempotency_key=request.idempotency_key,
        caller=user.username,
        received_count=len(request.records),
        confirm_live=request.confirm_live,
        skip_s3=request.skip_s3
    )

    job_result = process_bulk_ingest(
        job_id=job_id,
        environment=request.environment,
        skip_s3=request.skip_s3,
        records=raw_records,
        caller=user.username
    )

    return format_job_response(job_result, job_result.get("per_record_results"))

@app.post("/bulk/ingestion/validate")
async def validate_jobs(
    request: ValidateRequest,
    user: UserIdentity = Depends(require_viewer)
):
    if len(request.records) > 10000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large. Maximum 10,000 records allowed."
        )

    raw_records = [r.model_dump() for r in request.records]
    
    results = run_dry_run_validation(raw_records, request.environment, user.username)
    
    return {
        "status": "ok",
        "data": {
            "environment": results["environment"],
            "received_count": results["received_count"],
            "valid_count": results["valid_count"],
            "invalid_count": results["invalid_count"],
            "zoho_updated_count": results["zoho_updated_count"],
            "zoho_failed_count": results["zoho_failed_count"],
            "s3_written": results["s3_written"],
            "s3_key": results["s3_key"],
            "final_state": results["final_state"],
            "per_record_results": results["per_record_results"],
            "errors": results["errors"]
        }
    }

@app.get("/bulk/jobs/{job_id}")
async def get_job_by_id(
    job_id: str,
    user: UserIdentity = Depends(require_viewer)
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} was not found."
        )
        
    per_records = fetch_per_record_results(job_id)
    audit_trail_raw = get_job_audit_trail(job_id)
    
    audit_trail = []
    for audit in audit_trail_raw:
        audit_trail.append({
            "action": audit.get("action"),
            "caller": audit.get("caller"),
            "timestamp": audit.get("timestamp"),
            "previous_state": audit.get("previous_state"),
            "new_state": audit.get("new_state"),
            "details": audit.get("details")
        })
        
    response_data = format_job_response(job, per_records)
    response_data["data"]["caller"] = job.get("caller")
    response_data["data"]["audit_trail"] = audit_trail
    return response_data

@app.get("/bulk/jobs")
async def get_jobs_list(
    status: Optional[str] = None,
    environment: Optional[str] = None,
    caller: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 25,
    page_token: Optional[str] = None,
    user: UserIdentity = Depends(require_viewer)
):
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Limit must be between 1 and 100."
        )
        
    try:
        jobs, next_token = list_jobs(
            status_filter=status,
            environment_filter=environment,
            caller_filter=caller,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            page_token=page_token
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err)
        )
        
    formatted_jobs = []
    for job in jobs:
        formatted_jobs.append({
            "job_id": job["job_id"],
            "environment": job["environment"],
            "status": job["status"],
            "caller": job.get("caller"),
            "received_count": int(job.get("received_count", 0)),
            "valid_count": int(job.get("valid_count", 0)),
            "invalid_count": int(job.get("invalid_count", 0)),
            "created_at": job.get("created_at")
        })
        
    return {
        "status": "ok",
        "data": {
            "jobs": formatted_jobs,
            "next_page_token": next_token
        }
    }

@app.post("/bulk/jobs/{job_id}/cancel")
async def cancel_job_by_id(
    job_id: str,
    request: Optional[CancelRequest] = None,
    user: UserIdentity = Depends(require_operator)
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} was not found."
        )

    current_status = job.get("status")

    if current_status == "CANCELLED":
        return {
            "status": "ok",
            "message": "Job is already cancelled.",
            "final_state": "CANCELLED"
        }

    terminals = ["SUCCESS", "FAILED", "PARTIAL_SUCCESS", "CANCELLED", "REPLAYED"]
    if current_status in terminals:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Cannot cancel job in terminal state: {current_status}",
                "final_state": current_status
            }
        )

    reason = request.reason if request else None
    
    try:
        trigger_cancel_request(job_id, reason, user.username)
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(val_err)
        )

    return {
        "status": "ok",
        "message": "Cancellation request submitted.",
        "final_state": "CANCELLING"
    }

@app.post("/bulk/jobs/{job_id}/replay", response_model=ReplayResponse)
async def replay_job_by_id(
    job_id: str,
    user: UserIdentity = Depends(require_operator)
):
    parent_job = get_job(job_id)
    if not parent_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Parent job with ID {job_id} was not found."
        )

    parent_status = parent_job.get("status")

    if parent_status == "REPLAYED":
        child_id = parent_job.get("child_job_id", "")
        return {
            "status": "ok",
            "parent_job_id": job_id,
            "child_job_id": child_id,
            "state": "REPLAYED"
        }

    allowed_states = ["FAILED", "PARTIAL_SUCCESS"]
    if parent_status not in allowed_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Replay is only valid for jobs in FAILED or PARTIAL_SUCCESS states. Current parent state is {parent_status}."
        )

    s3_key = parent_job.get("s3_key")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot replay job: Parent job has no record summary S3 CSV file stored."
        )

    s3 = get_s3_client()
    try:
        response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        csv_data = response["Body"].read().decode("utf-8")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read parent record summary from S3: {str(e)}"
        )

    csv_reader = csv.DictReader(io.StringIO(csv_data))
    records = []
    
    successful_outcomes = {}
    failed_records = []
    
    for row in csv_reader:
        settlement_id = row.get("settlement_id")
        process_status = row.get("process_status", "failed")
        
        record_obj = {
            "settlement_id": settlement_id,
            "loan_number": row.get("loan_number"),
            "creditor_name": row.get("creditor_name"),
            "settlement_amount": float(row.get("settlement_amount", 0.0)),
            "date_of_funds": row.get("date_of_funds"),
            "status": row.get("status")
        }
        records.append(record_obj)
        
        if process_status in ("created", "updated"):
            successful_outcomes[settlement_id] = {
                "settlement_id": settlement_id,
                "status": process_status,
                "zoho_record_id": row.get("zoho_record_id")
            }
        else:
            failed_records.append(record_obj)

    child_job_id = f"job_{uuid.uuid4().hex[:10]}"

    try:
        transition_job_state(
            job_id=job_id,
            from_states=["FAILED", "PARTIAL_SUCCESS"],
            to_state="REPLAYING",
            additional_updates={"child_job_id": child_job_id},
            caller=user.username
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(val_err)
        )

    create_job(
        job_id=child_job_id,
        environment=parent_job["environment"],
        idempotency_key=f"replay-{job_id}-{child_job_id}",
        caller=user.username,
        received_count=len(records),
        confirm_live=parent_job.get("confirm_live", True),
        skip_s3=parent_job.get("skip_s3", False)
    )

    transition_job_state(
        job_id=job_id,
        from_states=["REPLAYING"],
        to_state="REPLAYED",
        additional_updates={"child_job_id": child_job_id},
        caller=user.username
    )

    process_bulk_ingest(
        job_id=child_job_id,
        environment=parent_job["environment"],
        skip_s3=parent_job.get("skip_s3", False),
        records=records,
        caller=user.username,
        successful_outcomes=successful_outcomes
    )

    return {
        "status": "ok",
        "parent_job_id": job_id,
        "child_job_id": child_job_id,
        "state": "REPLAYED"
    }

from mangum import Mangum
handler = Mangum(app)

