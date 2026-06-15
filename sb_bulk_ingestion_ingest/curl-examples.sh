#!/usr/bin/env bash

# Base API URL
API_URL="http://localhost:8090"

# Authorized credentials (based on local config defaults)
OPERATOR_API_KEY="skybridge-key-operator-1234"
VIEWER_API_KEY="skybridge-key-viewer-5678"

# Alternatively, you can use HMAC-signed JWT.
# For local testing, a pre-generated token for 'operator' user:
# Header: {"alg": "HS256", "typ": "JWT"}
# Payload: {"sub": "developer", "role": "operator", "iat": 1780000000, "exp": 1880000000}
# Signature signed with secret: "super-secret-skybridge-session-key-2026"
JWT_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXZlbG9wZXIiLCJyb2xlIjoib3BlcmF0b3IiLCJpYXQiOjE3ODAwMDAwMDAsImV4cCI6MTg4MDAwMDAwMH0.s_q88hH5y1qI302p_vU7t6g7z044b-4q3028u923j12"

echo "=== 1. Dry Run Validate (Viewer Access) ==="
curl -X POST "${API_URL}/bulk/ingestion/validate" \
  -H "Content-Type: application/json" \
  -H "x-skybridge-api-key: ${VIEWER_API_KEY}" \
  -d '{
    "environment": "uat",
    "records": [
      {
        "settlement_id": "SR-001",
        "loan_number": "LN-50001",
        "creditor_name": "Acme Credit",
        "settlement_amount": 12450.75,
        "date_of_funds": "2026-06-10",
        "status": "Pending"
      },
      {
        "settlement_id": "SR-009",
        "loan_number": "",
        "creditor_name": "Bad Creditor",
        "settlement_amount": -100.0,
        "date_of_funds": "invalid-date",
        "status": "Pending"
      }
    ]
  }'
echo -e "\n\n"

echo "=== 2. Ingest 1 Valid Record (Operator Access) ==="
# Generate a random idempotency key
IDEMP_KEY="partner-sync-$(date +%s)"
curl -X POST "${API_URL}/bulk/ingestion/ingest" \
  -H "Content-Type: application/json" \
  -H "x-skybridge-api-key: ${OPERATOR_API_KEY}" \
  -d "{
    \"environment\": \"uat\",
    \"confirm_live\": true,
    \"idempotency_key\": \"${IDEMP_KEY}\",
    \"records\": [
      {
        \"settlement_id\": \"SR-101\",
        \"loan_number\": \"LN-60001\",
        \"creditor_name\": \"Apex Financials\",
        \"settlement_amount\": 8500.50,
        \"date_of_funds\": \"2026-06-12\",
        \"status\": \"Pending\"
      }
    ]
  }"
echo -e "\n\n"

echo "=== 3. Ingest Duplicate (Test Idempotency) ==="
# We reuse the same idempotency key
curl -X POST "${API_URL}/bulk/ingestion/ingest" \
  -H "Content-Type: application/json" \
  -H "x-skybridge-api-key: ${OPERATOR_API_KEY}" \
  -d "{
    \"environment\": \"uat\",
    \"confirm_live\": true,
    \"idempotency_key\": \"${IDEMP_KEY}\",
    \"records\": [
      {
        \"settlement_id\": \"SR-101\",
        \"loan_number\": \"LN-60001\",
        \"creditor_name\": \"Apex Financials\",
        \"settlement_amount\": 8500.50,
        \"date_of_funds\": \"2026-06-12\",
        \"status\": \"Pending\"
      }
    ]
  }"
echo -e "\n\n"

echo "=== 4. Ingest with Missing confirm_live (Returns 412) ==="
curl -X POST "${API_URL}/bulk/ingestion/ingest" \
  -H "Content-Type: application/json" \
  -H "x-skybridge-api-key: ${OPERATOR_API_KEY}" \
  -d '{
    "environment": "uat",
    "confirm_live": false,
    "idempotency_key": "partner-sync-missing-live",
    "records": [
      {
        "settlement_id": "SR-102",
        "loan_number": "LN-60002",
        "creditor_name": "Apex Financials",
        "settlement_amount": 1500.0,
        "date_of_funds": "2026-06-12",
        "status": "Pending"
      }
    ]
  }'
echo -e "\n\n"

echo "=== 5. List Jobs with Filters (Viewer Access) ==="
curl -X GET "${API_URL}/bulk/jobs?limit=5&environment=uat" \
  -H "x-skybridge-api-key: ${VIEWER_API_KEY}"
echo -e "\n\n"

echo "=== 6. Get Job Details (Use the job_id from step 2) ==="
echo "Replace 'job_id_placeholder' with the actual job_id returned in step 2:"
# curl -X GET "${API_URL}/bulk/jobs/job_id_placeholder" -H "x-skybridge-api-key: ${VIEWER_API_KEY}"
echo -e "\n\n"
