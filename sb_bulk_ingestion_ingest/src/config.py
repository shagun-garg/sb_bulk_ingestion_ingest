import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
DOTENV_PATH = BASE_DIR / ".env"
load_dotenv(DOTENV_PATH)

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

DYNAMODB_TABLE_JOBS = os.getenv("DYNAMODB_TABLE_JOBS", "BulkJobs")
DYNAMODB_TABLE_AUDIT = os.getenv("DYNAMODB_TABLE_AUDIT", "AuditLog")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "bulk-ingestion-digest")

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-skybridge-session-key-2026")
JWT_ALGORITHM = "HS256"

API_KEYS_RAW = os.getenv("API_KEYS", "test-operator-key,test-viewer-key")
API_KEYS = [k.strip() for k in API_KEYS_RAW.split(",") if k.strip()]

API_KEY_LABELS = {}
API_KEY_ROLES = {}


def _build_api_key_maps(keys: list[str], label_prefix: str = "api-key"):
    labels: dict[str, str] = {}
    roles: dict[str, str] = {}
    for key in keys:
        if "operator" in key.lower():
            roles[key] = "operator"
            labels[key] = f"operator-{label_prefix}"
        elif "admin" in key.lower():
            roles[key] = "admin"
            labels[key] = f"admin-{label_prefix}"
        else:
            roles[key] = "viewer"
            labels[key] = f"viewer-{label_prefix}"
    return labels, roles


API_KEY_LABELS, API_KEY_ROLES = _build_api_key_maps(API_KEYS)

DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
CURSOR_SIGNING_SECRET = os.getenv("CURSOR_SIGNING_SECRET", "cursor-token-signing-secret-key-2026")

SSM_JWT_PARAM = os.getenv("SSM_JWT_PARAM", "")
SSM_API_KEYS_PARAM = os.getenv("SSM_API_KEYS_PARAM", "")
SSM_DYNAMODB_TABLE_JOBS_PARAM = os.getenv("SSM_DYNAMODB_TABLE_JOBS_PARAM", "")
SSM_DYNAMODB_TABLE_AUDIT_PARAM = os.getenv("SSM_DYNAMODB_TABLE_AUDIT_PARAM", "")
SSM_S3_BUCKET_NAME_PARAM = os.getenv("SSM_S3_BUCKET_NAME_PARAM", "")
SSM_DYNAMODB_ENDPOINT_URL_PARAM = os.getenv("SSM_DYNAMODB_ENDPOINT_URL_PARAM", "")
SSM_S3_ENDPOINT_URL_PARAM = os.getenv("SSM_S3_ENDPOINT_URL_PARAM", "")
SSM_CURSOR_SIGNING_SECRET_PARAM = os.getenv("SSM_CURSOR_SIGNING_SECRET_PARAM", "")

if any([
    SSM_JWT_PARAM,
    SSM_API_KEYS_PARAM,
    SSM_DYNAMODB_TABLE_JOBS_PARAM,
    SSM_DYNAMODB_TABLE_AUDIT_PARAM,
    SSM_S3_BUCKET_NAME_PARAM,
    SSM_DYNAMODB_ENDPOINT_URL_PARAM,
    SSM_S3_ENDPOINT_URL_PARAM,
    SSM_CURSOR_SIGNING_SECRET_PARAM
]):
    try:
        import boto3

        def _fetch_ssm_value(param_name: str) -> str:
            ssm = boto3.client("ssm", region_name=AWS_REGION)
            result = ssm.get_parameter(Name=param_name, WithDecryption=True)
            return result["Parameter"]["Value"]

        if SSM_JWT_PARAM:
            JWT_SECRET = _fetch_ssm_value(SSM_JWT_PARAM)

        if SSM_API_KEYS_PARAM:
            API_KEYS_RAW = _fetch_ssm_value(SSM_API_KEYS_PARAM)
            API_KEYS = [k.strip() for k in API_KEYS_RAW.split(",") if k.strip()]
            API_KEY_LABELS, API_KEY_ROLES = _build_api_key_maps(API_KEYS, label_prefix="ssm-api-key")

        if SSM_DYNAMODB_TABLE_JOBS_PARAM:
            DYNAMODB_TABLE_JOBS = _fetch_ssm_value(SSM_DYNAMODB_TABLE_JOBS_PARAM)

        if SSM_DYNAMODB_TABLE_AUDIT_PARAM:
            DYNAMODB_TABLE_AUDIT = _fetch_ssm_value(SSM_DYNAMODB_TABLE_AUDIT_PARAM)

        if SSM_S3_BUCKET_NAME_PARAM:
            S3_BUCKET_NAME = _fetch_ssm_value(SSM_S3_BUCKET_NAME_PARAM)

        if SSM_DYNAMODB_ENDPOINT_URL_PARAM:
            DYNAMODB_ENDPOINT_URL = _fetch_ssm_value(SSM_DYNAMODB_ENDPOINT_URL_PARAM)

        if SSM_S3_ENDPOINT_URL_PARAM:
            S3_ENDPOINT_URL = _fetch_ssm_value(SSM_S3_ENDPOINT_URL_PARAM)

        if SSM_CURSOR_SIGNING_SECRET_PARAM:
            CURSOR_SIGNING_SECRET = _fetch_ssm_value(SSM_CURSOR_SIGNING_SECRET_PARAM)
    except Exception:
        pass
