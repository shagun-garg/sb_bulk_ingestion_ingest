import os

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
for key in API_KEYS:
    if "operator" in key.lower():
        API_KEY_ROLES[key] = "operator"
        API_KEY_LABELS[key] = "operator-api-key"
    elif "admin" in key.lower():
        API_KEY_ROLES[key] = "admin"
        API_KEY_LABELS[key] = "admin-api-key"
    else:
        API_KEY_ROLES[key] = "viewer"
        API_KEY_LABELS[key] = "viewer-api-key"


SSM_JWT_PARAM = os.getenv("SSM_JWT_PARAM", "")
SSM_API_KEYS_PARAM = os.getenv("SSM_API_KEYS_PARAM", "")

if SSM_JWT_PARAM or SSM_API_KEYS_PARAM:
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        if SSM_JWT_PARAM:
            res = ssm.get_parameter(Name=SSM_JWT_PARAM, WithDecryption=True)
            JWT_SECRET = res["Parameter"]["Value"]
        if SSM_API_KEYS_PARAM:
            res = ssm.get_parameter(Name=SSM_API_KEYS_PARAM, WithDecryption=True)
            API_KEYS_RAW = res["Parameter"]["Value"]
            API_KEYS = [k.strip() for k in API_KEYS_RAW.split(",") if k.strip()]
            for key in API_KEYS:
                if "operator" in key.lower() or "admin" in key.lower():
                    API_KEY_ROLES[key] = "operator"
                    API_KEY_LABELS[key] = "operator-ssm-api-key"
                else:
                    API_KEY_ROLES[key] = "viewer"
                    API_KEY_LABELS[key] = "viewer-ssm-api-key"
    except Exception:
        pass


DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")

CURSOR_SIGNING_SECRET = os.getenv("CURSOR_SIGNING_SECRET", "cursor-token-signing-secret-key-2026")
