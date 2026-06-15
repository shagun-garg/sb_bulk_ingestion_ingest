import boto3
import csv
import io
import json
from botocore.exceptions import ClientError
from typing import List, Dict, Any, Optional

from src.config import S3_BUCKET_NAME, S3_ENDPOINT_URL, AWS_REGION

def get_s3_client():
    if S3_ENDPOINT_URL:
        return boto3.client(
            "s3",
            region_name=AWS_REGION,
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id="mock",
            aws_secret_access_key="mock"
        )
    return boto3.client("s3", region_name=AWS_REGION)

def ensure_bucket_exists():
    s3 = get_s3_client()
    try:
        s3.head_bucket(Bucket=S3_BUCKET_NAME)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "404" or error_code == "NoSuchBucket":
            if AWS_REGION == "us-east-1":
                s3.create_bucket(Bucket=S3_BUCKET_NAME)
            else:
                s3.create_bucket(
                    Bucket=S3_BUCKET_NAME,
                    CreateBucketConfiguration={"LocationConstraint": AWS_REGION}
                )
        else:
            raise

def upload_digest_csv(job_id: str, date_str: str, records: List[Dict[str, Any]], per_record_results: List[Dict[str, Any]]) -> str:
    ensure_bucket_exists()
    s3 = get_s3_client()
    
    results_map = {res["settlement_id"]: res for res in per_record_results}
    
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    
    writer.writerow([
        "settlement_id", "loan_number", "creditor_name", 
        "settlement_amount", "date_of_funds", "status", 
        "zoho_record_id", "process_status", "error"
    ])
    
    for rec in records:
        sid = rec.get("settlement_id")
        res = results_map.get(sid, {})
        writer.writerow([
            sid,
            rec.get("loan_number"),
            rec.get("creditor_name"),
            rec.get("settlement_amount"),
            rec.get("date_of_funds"),
            rec.get("status"),
            res.get("zoho_record_id", ""),
            res.get("status", "failed"),
            res.get("error", "")
        ])
    
    key = f"ingestion-digest/{date_str}/job_{job_id}.csv"
    
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=csv_buffer.getvalue().encode("utf-8"),
        ContentType="text/csv"
    )
    return key

def upload_per_record_results(job_id: str, results: List[Dict[str, Any]]) -> str:
    ensure_bucket_exists()
    s3 = get_s3_client()
    key = f"job-records/{job_id}.json"
    
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=json.dumps(results).encode("utf-8"),
        ContentType="application/json"
    )
    return key

def fetch_per_record_results(job_id: str) -> List[Dict[str, Any]]:
    s3 = get_s3_client()
    key = f"job-records/{job_id}.json"
    try:
        response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        data = response["Body"].read().decode("utf-8")
        return json.loads(data)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return []
        raise
