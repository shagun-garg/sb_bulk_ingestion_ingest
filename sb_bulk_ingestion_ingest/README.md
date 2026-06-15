# Architecture Design Decisions

## 1. DynamoDB Primary Keys and GSIs

* **BulkJobs Table**

  * `job_id` is used as the partition key because most operations fetch jobs directly by ID.
  * `IdempotencyKeyIndex` allows quick lookup of requests using the same idempotency key and prevents duplicate processing.
  * `EnvCreatedAtIndex` supports listing jobs by environment and creation date.
  * `AllCreatedAtIndex` enables listing jobs across all environments from a single index.

## 2. Record Results Storage

Detailed record results are stored in S3 instead of DynamoDB. A single bulk job can contain thousands of records, and storing all results in DynamoDB could exceed the 400 KB item limit. DynamoDB stores only job metadata and summary counts.

## 3. Audit Log Design

Audit logs use `target_id` as the partition key so all events related to a job can be retrieved together. The sort key combines a timestamp and UUID to maintain ordering and avoid collisions.

## 4. Pagination Cursor Design

Offset-based pagination becomes inefficient for large datasets. The API uses signed cursors containing the DynamoDB continuation token and query filters. This prevents cursor tampering and ensures cursors cannot be reused with different filters.


## Local Run and Installation Instructions

### 1. Start the Local Stack (Docker Compose)
Start DynamoDB Local, S3 Moto Server, and the FastAPI application locally:
```bash
docker-compose up --build
```
The API will be available at `http://localhost:8090`.

---

## Running Automated Tests

Run the test suite using `pytest` inside the project root:
```bash
# 1. Install dependencies locally (if not using docker)
pip install -r requirements.txt

# 2. Run pytest
pytest -v tests/
```

---

## AWS Deployment with Terraform

### 1. Prerequisites
- Configure AWS credentials (`aws configure`).
- Terraform CLI installed.

### 2. Deployment
```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### 3. Terraform Outputs
The deployment will output:
- `api_gateway_url`: The entry URL for your API.
- `lambda_arn`: Identifier for the AWS Lambda function.
- `dynamodb_table_jobs_name`: Table storing jobs metadata.
- `dynamodb_table_audit_name`: Table storing the audit logs.
- `s3_bucket_name`: S3 Bucket storing digests.



#### ACHITECTURE 

    Client
      |
    FastAPI(LAMBDA) -> VALIDATION
      |
    DynamoDB(DRYRUN/COMMIT)
      |
  Ingest Pipeline .
      |
     ZOHO .
      |
      S3 .
##end
