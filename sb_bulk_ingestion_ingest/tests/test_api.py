import pytest
import time
import csv
import io
import json
from fastapi import status
from src.database import get_job, get_db_resource, DYNAMODB_TABLE_JOBS, get_job_audit_trail
from src.s3_client import get_s3_client, S3_BUCKET_NAME, fetch_per_record_results

def make_valid_record(index: int, amount: float = 100.0, ending_digit: str = "1") -> dict:
    return {
        "settlement_id": f"SR-{str(index).zfill(3)}{ending_digit}",
        "loan_number": f"LN-500{index}",
        "creditor_name": "Acme Credit",
        "settlement_amount": amount,
        "date_of_funds": "2026-06-10",
        "status": "Pending"
    }

def make_invalid_record(index: int) -> dict:
    return {
        "settlement_id": f"SR-{str(index).zfill(3)}",
        "loan_number": "",
        "creditor_name": "Acme Credit",
        "settlement_amount": -10.0,
        "date_of_funds": "invalid-date",
        "status": "Pending"
        # date and empty string
    }

# ==================== AUTHENTICATION TESTS ====================

def test_authentication_paths(client, operator_jwt, viewer_jwt, expired_jwt):
    headers_operator = {"Authorization": f"Bearer {operator_jwt}"}
    headers_viewer = {"Authorization": f"Bearer {viewer_jwt}"}
    headers_expired = {"Authorization": f"Bearer {expired_jwt}"}
    headers_api_operator = {"x-skybridge-api-key": "test-operator-key"}
    headers_api_viewer = {"x-skybridge-api-key": "test-viewer-key"}
    headers_api_wrong = {"x-skybridge-api-key": "bad-key"}

    # missing token 401
    response = client.post("/bulk/ingestion/ingest", json={})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # expirt token 401
    response = client.post("/bulk/ingestion/ingest", json={}, headers=headers_expired)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in response.json()["detail"].lower()

    # worng key -> 401
    response = client.post("/bulk/ingestion/ingest", json={}, headers=headers_api_wrong)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # failiure wronng role  403
    payload = {
        "environment": "uat",
        "confirm_live": True,
        "idempotency_key": "test-auth-1",
        "records": [make_valid_record(1)]
    }
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_viewer)
    assert response.status_code == status.HTTP_403_FORBIDDEN

    #  /ingest 200
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_operator)
    assert response.status_code == status.HTTP_200_OK

    #  valid key /ingest 200
    payload["idempotency_key"] = "test-auth-2"
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_api_operator)
    assert response.status_code == status.HTTP_200_OK

    #  Viewer /validate 200
    validate_payload = {
        "environment": "uat",
        "records": [make_valid_record(1)]
    }
    response = client.post("/bulk/ingestion/validate", json=validate_payload, headers=headers_viewer)
    assert response.status_code == status.HTTP_200_OK

    # valid key /validate -> 200
    response = client.post("/bulk/ingestion/validate", json=validate_payload, headers=headers_api_viewer)
    assert response.status_code == status.HTTP_200_OK


# ==================== INGESTION TESTS ====================

def test_ingest_all_valid(client, operator_jwt):
    headers = {"Authorization": f"Bearer {operator_jwt}"}
    records = [make_valid_record(i) for i in range(20)]
    payload = {
        "environment": "uat",
        "confirm_live": True,
        "skip_s3": False,
        "idempotency_key": "ik-valid-20",
        "records": records
    }

    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()["data"]

    assert res_data["final_state"] == "SUCCESS"
    assert res_data["received_count"] == 20
    assert res_data["valid_count"] == 20
    assert res_data["invalid_count"] == 0
    assert res_data["zoho_updated_count"] == 20
    assert res_data["zoho_failed_count"] == 0
    assert res_data["s3_written"] is True
    assert res_data["s3_key"] is not None
    assert len(res_data["per_record_results"]) == 20
    assert len(res_data["errors"]) == 0

    s3_key = res_data["s3_key"]
    s3 = get_s3_client()
    s3_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
    csv_content = s3_obj["Body"].read().decode("utf-8")
    assert "settlement_id,loan_number" in csv_content

    job_id = res_data["job_id"]
    job = get_job(job_id)
    assert job["status"] == "SUCCESS"
    assert job["received_count"] == 20
    assert job["zoho_updated_count"] == 20


def test_ingest_partial_success(client, operator_jwt):
    headers = {"Authorization": f"Bearer {operator_jwt}"}
    
    records = []
    for i in range(3):
        records.append(make_invalid_record(i))
    for i in range(3, 9):
        records.append(make_valid_record(i, ending_digit="1"))
    records.append(make_valid_record(9, ending_digit="9"))

    payload = {
        "environment": "uat",
        "confirm_live": True,
        "skip_s3": False,
        "idempotency_key": "ik-partial-10",
        "records": records
    }

    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()["data"]

    assert res_data["final_state"] == "PARTIAL_SUCCESS"
    assert res_data["received_count"] == 10
    assert res_data["valid_count"] == 7
    assert res_data["invalid_count"] == 3
    assert res_data["zoho_updated_count"] == 6
    assert res_data["zoho_failed_count"] == 1 
    assert len(res_data["errors"]) == 4


def test_ingest_all_failed(client, operator_jwt):
    headers = {"Authorization": f"Bearer {operator_jwt}"}
    records = [make_invalid_record(i) for i in range(10)]
    payload = {
        "environment": "uat",
        "confirm_live": True,
        "idempotency_key": "ik-failed-10",
        "records": records
    }

    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()["data"]

    assert res_data["final_state"] == "FAILED"
    assert res_data["received_count"] == 10
    assert res_data["valid_count"] == 0
    assert res_data["invalid_count"] == 10
    assert res_data["zoho_updated_count"] == 0
    assert res_data["zoho_failed_count"] == 0
    assert len(res_data["errors"]) == 10


# ==================== VALIDATION & EDGE CASES ====================

def test_validation_edge_cases(client, operator_jwt, viewer_jwt):
    headers_op = {"Authorization": f"Bearer {operator_jwt}"}
    headers_vw = {"Authorization": f"Bearer {viewer_jwt}"}

    # confirm_live
    payload = {
        "environment": "uat",
        "confirm_live": False,
        "idempotency_key": "ik-confirm-live-missing",
        "records": [make_valid_record(1)]
    }
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_op)
    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED

    payload = {
        "environment": "uat",
        "confirm_live": True,
        "skip_s3": True,
        "idempotency_key": "ik-skip-s3",
        "records": [make_valid_record(1)]
    }
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_op)
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()["data"]
    assert res_data["s3_written"] is False
    assert res_data["s3_key"] is None
    per_records = fetch_per_record_results(res_data["job_id"])
    assert len(per_records) == 1

    # invalid environment 400
    payload = {
        "environment": "invalid-env",
        "confirm_live": True,
        "idempotency_key": "ik-invalid-env",
        "records": [make_valid_record(1)]
    }
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_op)
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    # Payload > 10k records 413
    payload = {
        "environment": "uat",
        "confirm_live": True,
        "idempotency_key": "ik-too-many",
        "records": [make_valid_record(i) for i in range(10001)]
    }
    response = client.post("/bulk/ingestion/ingest", json=payload, headers=headers_op)
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


# ==================== IDEMPOTENCY ====================

def test_idempotency(client, operator_jwt):
    headers = {"Authorization": f"Bearer {operator_jwt}"}
    payload = {
        "environment": "uat",
        "confirm_live": True,
        "idempotency_key": "ik-dup-key",
        "records": [make_valid_record(1)]
    }

    response1 = client.post("/bulk/ingestion/ingest", json=payload, headers=headers)
    assert response1.status_code == status.HTTP_200_OK
    res1 = response1.json()

    response2 = client.post("/bulk/ingestion/ingest", json=payload, headers=headers)
    assert response2.status_code == status.HTTP_200_OK
    res2 = response2.json()

    assert res1["data"]["job_id"] == res2["data"]["job_id"]
    assert res1["data"]["final_state"] == res2["data"]["final_state"]


# ==================== RETRIEVAL, FILTERS & PAGINATION ====================

def test_retrieval_and_filters(client, operator_jwt, viewer_jwt):
    headers_op = {"Authorization": f"Bearer {operator_jwt}"}
    headers_vw = {"Authorization": f"Bearer {viewer_jwt}"}

    client.post("/bulk/ingestion/ingest", json={
        "environment": "uat", "confirm_live": True, "idempotency_key": "ik-ret-1",
        "records": [make_valid_record(1)]
    }, headers=headers_op)

    client.post("/bulk/ingestion/ingest", json={
        "environment": "uat", "confirm_live": True, "idempotency_key": "ik-ret-2",
        "records": [make_invalid_record(2)]
    }, headers=headers_op)

    client.post("/bulk/ingestion/ingest", json={
        "environment": "production", "confirm_live": True, "idempotency_key": "ik-ret-3",
        "records": [make_valid_record(3)]
    }, headers=headers_op)

    jobs_response = client.get("/bulk/jobs?limit=5", headers=headers_vw)
    job_list = jobs_response.json()["data"]["jobs"]
    assert len(job_list) >= 3
    sample_job_id = job_list[0]["job_id"]

    job_detail_resp = client.get(f"/bulk/jobs/{sample_job_id}", headers=headers_vw)
    assert job_detail_resp.status_code == status.HTTP_200_OK
    detail_data = job_detail_resp.json()["data"]
    assert detail_data["job_id"] == sample_job_id
    assert "audit_trail" in detail_data
    assert len(detail_data["audit_trail"]) > 0

    job_detail_resp_404 = client.get("/bulk/jobs/job_invalid123", headers=headers_vw)
    assert job_detail_resp_404.status_code == status.HTTP_404_NOT_FOUND

    prod_jobs_resp = client.get("/bulk/jobs?environment=production", headers=headers_vw)
    prod_jobs = prod_jobs_resp.json()["data"]["jobs"]
    for j in prod_jobs:
        assert j["environment"] == "production"

    failed_jobs_resp = client.get("/bulk/jobs?status=FAILED", headers=headers_vw)
    failed_jobs = failed_jobs_resp.json()["data"]["jobs"]
    for j in failed_jobs:
        assert j["status"] == "FAILED"

    pag_resp = client.get("/bulk/jobs?limit=2", headers=headers_vw)
    pag_data = pag_resp.json()["data"]
    assert len(pag_data["jobs"]) <= 2
    token = pag_data["next_page_token"]
    assert token is not None

    page2_resp = client.get(f"/bulk/jobs?limit=2&page_token={token}", headers=headers_vw)
    assert page2_resp.status_code == status.HTTP_200_OK

    replay_resp = client.get(f"/bulk/jobs?limit=2&status=FAILED&page_token={token}", headers=headers_vw)
    assert replay_resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "replay" in replay_resp.json()["detail"].lower()


# ==================== STATE CONTROL (CANCEL & REPLAY) ====================

def test_cancel_operations(client, operator_jwt):
    headers = {"Authorization": f"Bearer {operator_jwt}"}
    
    resp = client.post("/bulk/ingestion/ingest", json={
        "environment": "uat", "confirm_live": True, "idempotency_key": "ik-cancel-test",
        "records": [make_valid_record(1)]
    }, headers=headers)
    job_id = resp.json()["data"]["job_id"]

    cancel_resp = client.post(f"/bulk/jobs/{job_id}/cancel", headers=headers)
    assert cancel_resp.status_code == status.HTTP_409_CONFLICT
    assert cancel_resp.json()["detail"]["final_state"] == "SUCCESS"

    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :created",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":created": "CREATED"}
    )

    cancel_resp = client.post(f"/bulk/jobs/{job_id}/cancel", json={"reason": "upstream bad"}, headers=headers)
    assert cancel_resp.status_code == status.HTTP_200_OK
    assert cancel_resp.json()["final_state"] == "CANCELLING"

    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :cancelled",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":cancelled": "CANCELLED"}
    )
    cancel_resp = client.post(f"/bulk/jobs/{job_id}/cancel", headers=headers)
    assert cancel_resp.status_code == status.HTTP_200_OK
    assert cancel_resp.json()["final_state"] == "CANCELLED"


def test_replay_operations(client, operator_jwt, viewer_jwt):
    headers_op = {"Authorization": f"Bearer {operator_jwt}"}
    headers_vw = {"Authorization": f"Bearer {viewer_jwt}"}

    records = [make_invalid_record(i) for i in range(3)] + [make_valid_record(i) for i in range(3, 10)]
    resp = client.post("/bulk/ingestion/ingest", json={
        "environment": "uat", "confirm_live": True, "idempotency_key": "ik-replay-parent",
        "records": records
    }, headers=headers_op)
    parent_job_id = resp.json()["data"]["job_id"]
    assert resp.json()["data"]["final_state"] == "PARTIAL_SUCCESS"

    replay_resp = client.post(f"/bulk/jobs/{parent_job_id}/replay", headers=headers_op)
    assert replay_resp.status_code == status.HTTP_200_OK
    replay_data = replay_resp.json()
    assert replay_data["parent_job_id"] == parent_job_id
    child_job_id = replay_data["child_job_id"]
    assert replay_data["state"] == "REPLAYED"

    parent_job = get_job(parent_job_id)
    assert parent_job["status"] == "REPLAYED"
    assert parent_job["child_job_id"] == child_job_id

    child_job = get_job(child_job_id)
    assert child_job["status"] == "PARTIAL_SUCCESS"
    assert child_job["received_count"] == 10
    assert child_job["valid_count"] == 7
    assert child_job["invalid_count"] == 3

    dup_replay_resp = client.post(f"/bulk/jobs/{parent_job_id}/replay", headers=headers_op)
    assert dup_replay_resp.status_code == status.HTTP_200_OK
    assert dup_replay_resp.json()["child_job_id"] == child_job_id

    resp_success = client.post("/bulk/ingestion/ingest", json={
        "environment": "uat", "confirm_live": True, "idempotency_key": "ik-replay-success",
        "records": [make_valid_record(1)]
    }, headers=headers_op)
    success_job_id = resp_success.json()["data"]["job_id"]

    replay_fail = client.post(f"/bulk/jobs/{success_job_id}/replay", headers=headers_op)
    assert replay_fail.status_code == status.HTTP_409_CONFLICT


# ==================== DRY RUN VALIDATION ====================

def test_dry_run_validate(client, viewer_jwt):
    headers = {"Authorization": f"Bearer {viewer_jwt}"}
    records = [make_valid_record(1), make_invalid_record(2)]
    payload = {
        "environment": "uat",
        "records": records
    }

    response = client.post("/bulk/ingestion/validate", json=payload, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()["data"]

    assert "job_id" not in res_data
    assert res_data["s3_key"] is None
    assert res_data["final_state"] == "PARTIAL_SUCCESS"
    assert res_data["received_count"] == 2
    assert res_data["valid_count"] == 1
    assert res_data["invalid_count"] == 1

    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    audit_table = get_db_resource().Table("TestAuditLog")
    scan_resp = audit_table.scan()
    validation_audits = [item for item in scan_resp["Items"] if item["action"] == "validation_requested"]
    assert len(validation_audits) == 1
    assert validation_audits[0]["details"]["received_count"] == 2
