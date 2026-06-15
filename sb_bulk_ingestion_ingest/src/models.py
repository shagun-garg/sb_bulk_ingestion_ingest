from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any, Union
import re

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class SettlementRecord(BaseModel):
    settlement_id: str = Field(..., min_length=1, description="Unique identifier for the settlement")
    loan_number: str = Field(..., min_length=1, description="Loan identifier")
    creditor_name: str = Field(..., min_length=1, description="Creditor company name")
    settlement_amount: float = Field(..., description="Negotiated settlement amount")
    date_of_funds: str = Field(..., description="ISO 8601 Date format YYYY-MM-DD")
    status: str = Field(..., min_length=1, description="Current workflow status")

    @field_validator("date_of_funds")
    @classmethod
    def validate_date(cls, v: str) -> str:
        if not DATE_PATTERN.match(v):
            raise ValueError("date_of_funds must be in YYYY-MM-DD format")
        return v

class IngestRequest(BaseModel):
    environment: str = Field(..., description="Target environment: uat or production")
    confirm_live: Optional[bool] = Field(None, description="Must be true for writing to Zoho")
    skip_s3: bool = Field(default=False, description="Skip upload of final digest CSV to S3")
    idempotency_key: str = Field(..., min_length=1, description="Idempotency key for deduplication")
    records: List[dict] = Field(..., description="Array of settlement records to ingest")

    @field_validator("environment")
    @classmethod
    def validate_env(cls, v: str) -> str:
        env_lower = v.lower()
        if env_lower in ("uat", "sandbox"):
            return "uat"
        elif env_lower in ("production", "prod"):
            return "production"
        elif env_lower == "local":
            return "local"
        else:
            raise ValueError("Invalid environment. Allowed values: uat, production, local (or sandbox, prod)")

class ValidateRequest(BaseModel):
    environment: str = Field(..., description="Target environment: uat or production")
    skip_s3: bool = Field(default=False, description="Not used in validate but allowed")
    idempotency_key: Optional[str] = Field(None, description="Optional for dry-run")
    records: List[dict] = Field(..., description="Array of settlement records to validate")

    @field_validator("environment")
    @classmethod
    def validate_env(cls, v: str) -> str:
        env_lower = v.lower()
        if env_lower in ("uat", "sandbox"):
            return "uat"
        elif env_lower in ("production", "prod"):
            return "production"
        elif env_lower == "local":
            return "local"
        else:
            raise ValueError("Invalid environment. Allowed values: uat, production, local (or sandbox, prod)")

class PerRecordResult(BaseModel):
    settlement_id: str
    status: str  
    zoho_record_id: Optional[str] = None
    error: Optional[str] = None

class JobError(BaseModel):
    settlement_id: Optional[str] = None
    error: str

class JobData(BaseModel):
    job_id: Optional[str] = None
    environment: str
    received_count: int
    valid_count: int
    invalid_count: int
    zoho_updated_count: int
    zoho_failed_count: int
    s3_written: bool
    s3_key: Optional[str] = None
    final_state: str
    per_record_results: List[PerRecordResult] = []
    errors: List[JobError] = []

class IngestResponse(BaseModel):
    status: str = "ok"
    data: JobData

class CancelRequest(BaseModel):
    reason: Optional[str] = None

class ReplayResponse(BaseModel):
    status: str = "ok"
    parent_job_id: str
    child_job_id: str
    state: str
