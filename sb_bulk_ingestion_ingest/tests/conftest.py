import os
import pytest
import jwt
from datetime import datetime, timezone, timedelta

os.environ["ENVIRONMENT"] = "testing"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["DYNAMODB_TABLE_JOBS"] = "TestBulkJobs"
os.environ["DYNAMODB_TABLE_AUDIT"] = "TestAuditLog"
os.environ["S3_BUCKET_NAME"] = "test-bulk-ingestion-digest"
os.environ["JWT_SECRET"] = "test-jwt-secret-key-12345"
os.environ["API_KEYS"] = "test-operator-key,test-viewer-key"
os.environ["DYNAMODB_ENDPOINT_URL"] = ""
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["CURSOR_SIGNING_SECRET"] = "test-cursor-signing-secret"

from moto import mock_aws
from fastapi.testclient import TestClient
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.main import app
from src.database import ensure_tables_exist
from src.zoho import clear_mock_zoho_seen_ids

@pytest.fixture(scope="function")
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"

@pytest.fixture(scope="function")
def mocked_aws(aws_credentials):
    with mock_aws():
        ensure_tables_exist()
        
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bulk-ingestion-digest")
        
        yield
        clear_mock_zoho_seen_ids()

@pytest.fixture(scope="function")
def client(mocked_aws):
    return TestClient(app)

@pytest.fixture
def operator_jwt():
    payload = {
        "sub": "test-operator-user",
        "role": "operator",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    }
    return jwt.encode(payload, "test-jwt-secret-key-12345", algorithm="HS256")

@pytest.fixture
def viewer_jwt():
    payload = {
        "sub": "test-viewer-user",
        "role": "viewer",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    }
    return jwt.encode(payload, "test-jwt-secret-key-12345", algorithm="HS256")

@pytest.fixture
def expired_jwt():
    payload = {
        "sub": "test-expired-user",
        "role": "operator",
        "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    }
    return jwt.encode(payload, "test-jwt-secret-key-12345", algorithm="HS256")
