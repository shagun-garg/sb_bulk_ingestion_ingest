import boto3
import json
import jwt
import time
import uuid
import hashlib
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from typing import Dict, Any, List, Optional, Tuple

from src.config import (
    DYNAMODB_TABLE_JOBS,
    DYNAMODB_TABLE_AUDIT,
    DYNAMODB_ENDPOINT_URL,
    AWS_REGION,
    CURSOR_SIGNING_SECRET
)

def get_db_client():
    if DYNAMODB_ENDPOINT_URL:
        return boto3.client(
            "dynamodb",
            region_name=AWS_REGION,
            endpoint_url=DYNAMODB_ENDPOINT_URL,
            aws_access_key_id="mock",
            aws_secret_access_key="mock"
        )
    return boto3.client("dynamodb", region_name=AWS_REGION)

def get_db_resource():
    if DYNAMODB_ENDPOINT_URL:
        return boto3.resource(
            "dynamodb",
            region_name=AWS_REGION,
            endpoint_url=DYNAMODB_ENDPOINT_URL,
            aws_access_key_id="mock",
            aws_secret_access_key="mock"
        )
    return boto3.resource("dynamodb", region_name=AWS_REGION)

def ensure_tables_exist():
    client = get_db_client()
    
    try:
        client.describe_table(TableName=DYNAMODB_TABLE_JOBS)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            client.create_table(
                TableName=DYNAMODB_TABLE_JOBS,
                KeySchema=[
                    {"AttributeName": "job_id", "KeyType": "HASH"}
                ],
                AttributeDefinitions=[
                    {"AttributeName": "job_id", "AttributeType": "S"},
                    {"AttributeName": "idempotency_key", "AttributeType": "S"},
                    {"AttributeName": "environment", "AttributeType": "S"},
                    {"AttributeName": "created_at", "AttributeType": "S"},
                    {"AttributeName": "gsi_pk", "AttributeType": "S"}
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "IdempotencyKeyIndex",
                        "KeySchema": [
                            {"AttributeName": "idempotency_key", "KeyType": "HASH"}
                        ],
                        "Projection": {"ProjectionType": "ALL"}
                    },
                    {
                        "IndexName": "EnvCreatedAtIndex",
                        "KeySchema": [
                            {"AttributeName": "environment", "KeyType": "HASH"},
                            {"AttributeName": "created_at", "KeyType": "RANGE"}
                        ],
                        "Projection": {"ProjectionType": "ALL"}
                    },
                    {
                        "IndexName": "AllCreatedAtIndex",
                        "KeySchema": [
                            {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                            {"AttributeName": "created_at", "KeyType": "RANGE"}
                        ],
                        "Projection": {"ProjectionType": "ALL"}
                    }
                ],
                BillingMode="PAY_PER_REQUEST"
            )
            time.sleep(1)
            try:
                client.update_time_to_live(
                    TableName=DYNAMODB_TABLE_JOBS,
                    TimeToLiveSpecification={
                        "Enabled": True,
                        "AttributeName": "ttl"
                    }
                )
            except Exception:
                pass
        else:
            raise

    try:
        client.describe_table(TableName=DYNAMODB_TABLE_AUDIT)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            client.create_table(
                TableName=DYNAMODB_TABLE_AUDIT,
                KeySchema=[
                    {"AttributeName": "target_id", "KeyType": "HASH"},
                    {"AttributeName": "timestamp_id", "KeyType": "RANGE"}
                ],
                AttributeDefinitions=[
                    {"AttributeName": "target_id", "AttributeType": "S"},
                    {"AttributeName": "timestamp_id", "AttributeType": "S"}
                ],
                BillingMode="PAY_PER_REQUEST"
            )
        else:
            raise

def write_audit_log(target_id: str, action: str, caller: str, details: Optional[Dict[str, Any]] = None, previous_state: Optional[str] = None, new_state: Optional[str] = None):
    table = get_db_resource().Table(DYNAMODB_TABLE_AUDIT)
    now_iso = datetime.now(timezone.utc).isoformat()
    unique_id = str(uuid.uuid4())
    timestamp_id = f"{now_iso}#{unique_id}"
    
    item = {
        "target_id": target_id,
        "timestamp_id": timestamp_id,
        "action": action,
        "caller": caller,
        "timestamp": now_iso
    }
    if details is not None:
        item["details"] = details
    if previous_state:
        item["previous_state"] = previous_state
    if new_state:
        item["new_state"] = new_state
        
    table.put_item(Item=item)

def create_job(job_id: str, environment: str, idempotency_key: str, caller: str, received_count: int, confirm_live: bool, skip_s3: bool) -> dict:
    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    job_item = {
        "job_id": job_id,
        "gsi_pk": "JOB",
        "environment": environment,
        "idempotency_key": idempotency_key,
        "caller": caller,
        "status": "CREATED",
        "confirm_live": confirm_live,
        "skip_s3": skip_s3,
        "received_count": received_count,
        "valid_count": 0,
        "invalid_count": 0,
        "zoho_updated_count": 0,
        "zoho_failed_count": 0,
        "s3_written": False,
        "created_at": now_iso,
        "updated_at": now_iso,
        "cancel_requested": False
    }
    
    try:
        table.put_item(
            Item=job_item,
            ConditionExpression="attribute_not_exists(job_id)"
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ValueError(f"Job ID {job_id} already exists")
        raise
        
    write_audit_log(job_id, "job_created", caller, details={"received_count": received_count}, new_state="CREATED")
    return job_item

def check_idempotency_key(idempotency_key: str) -> Optional[dict]:
    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    try:
        response = table.query(
            IndexName="IdempotencyKeyIndex",
            KeyConditionExpression="idempotency_key = :key",
            ExpressionAttributeValues={":key": idempotency_key}
        )
        items = response.get("Items", [])
        if items:
            return items[0]
    except ClientError:
        pass
    return None

def get_job(job_id: str) -> Optional[dict]:
    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    response = table.get_item(Key={"job_id": job_id})
    return response.get("Item")

def get_job_audit_trail(job_id: str) -> List[Dict[str, Any]]:
    table = get_db_resource().Table(DYNAMODB_TABLE_AUDIT)
    response = table.query(
        KeyConditionExpression="target_id = :job_id",
        ExpressionAttributeValues={":job_id": job_id}
    )
    return response.get("Items", [])

def transition_job_state(
    job_id: str,
    from_states: List[str],
    to_state: str,
    additional_updates: Optional[Dict[str, Any]] = None,
    caller: str = "system"
) -> dict:
    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    update_expr = "SET #status = :to_state, updated_at = :now"
    expr_attr_names = {"#status": "status"}
    expr_attr_vals = {
        ":to_state": to_state,
        ":now": now_iso
    }
    
    if additional_updates:
        for k, v in additional_updates.items():
            attr_name = f"#{k}"
            val_name = f":{k}"
            update_expr += f", {attr_name} = {val_name}"
            expr_attr_names[attr_name] = k
            expr_attr_vals[val_name] = v
            
    cond_expr = "#status IN (" + ", ".join(f":from_{i}" for i in range(len(from_states))) + ")"
    for i, state in enumerate(from_states):
        expr_attr_vals[f":from_{i}"] = state
        
    try:
        response = table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expr,
            ConditionExpression=cond_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_vals,
            ReturnValues="ALL_NEW"
        )
        updated_item = response.get("Attributes", {})
        
        write_audit_log(
            target_id=job_id,
            action="state_transition",
            caller=caller,
            details=additional_updates,
            previous_state=", ".join(from_states),
            new_state=to_state
        )
        return updated_item
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ValueError(f"State transition from {from_states} to {to_state} is invalid or job does not exist.")
        raise

def trigger_cancel_request(job_id: str, reason: Optional[str], caller: str) -> dict:
    non_terminals = ["CREATED", "VALIDATING", "ZOHOW_WRITING", "S3_WRITING"]
    additional = {"cancel_requested": True}
    if reason:
        additional["cancel_reason"] = reason
        
    return transition_job_state(
        job_id=job_id,
        from_states=non_terminals,
        to_state="CANCELLING",
        additional_updates=additional,
        caller=caller
    )

def finalize_cancel(job_id: str, final_updates: dict, caller: str) -> dict:
    ttl_epoch = int(time.time()) + (90 * 24 * 60 * 60)
    final_updates["ttl"] = ttl_epoch
    return transition_job_state(
        job_id=job_id,
        from_states=["CANCELLING"],
        to_state="CANCELLED",
        additional_updates=final_updates,
        caller=caller
    )

def finalize_job(job_id: str, final_state: str, final_updates: dict, caller: str) -> dict:
    ttl_epoch = int(time.time()) + (90 * 24 * 60 * 60)
    final_updates["ttl"] = ttl_epoch
    final_updates["completed_at"] = datetime.now(timezone.utc).isoformat()
    
    return transition_job_state(
        job_id=job_id,
        from_states=["CREATED", "VALIDATING", "ZOHOW_WRITING", "S3_WRITING"],
        to_state=final_state,
        additional_updates=final_updates,
        caller=caller
    )


def _hash_filters(status: Optional[str], environment: Optional[str], caller: Optional[str], from_date: Optional[str], to_date: Optional[str], limit: int) -> str:
    payload_str = f"status={status or ''}&env={environment or ''}&caller={caller or ''}&from={from_date or ''}&to={to_date or ''}&limit={limit}"
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

def create_pagination_token(last_evaluated_key: dict, status: Optional[str], environment: Optional[str], caller: Optional[str], from_date: Optional[str], to_date: Optional[str], limit: int) -> str:
    filter_hash = _hash_filters(status, environment, caller, from_date, to_date, limit)
    payload = {
        "lek": last_evaluated_key,
        "fhash": filter_hash
    }
    return jwt.encode(payload, CURSOR_SIGNING_SECRET, algorithm="HS256")

def decode_pagination_token(token: str, status: Optional[str], environment: Optional[str], caller: Optional[str], from_date: Optional[str], to_date: Optional[str], limit: int) -> dict:
    try:
        payload = jwt.decode(token, CURSOR_SIGNING_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ValueError("Invalid, expired, or tampered pagination token.")
        
    expected_hash = _hash_filters(status, environment, caller, from_date, to_date, limit)
    token_hash = payload.get("fhash")
    
    if token_hash != expected_hash:
        raise ValueError("Pagination token cannot be replayed against different query filters.")
        
    return payload.get("lek")


def list_jobs(
    status_filter: Optional[str] = None,
    environment_filter: Optional[str] = None,
    caller_filter: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 25,
    page_token: Optional[str] = None
) -> Tuple[List[dict], Optional[str]]:
    table = get_db_resource().Table(DYNAMODB_TABLE_JOBS)
    
    exclusive_start_key = None
    if page_token:
        exclusive_start_key = decode_pagination_token(
            page_token, status_filter, environment_filter, caller_filter, from_date, to_date, limit
        )

    query_kwargs = {
        "Limit": limit,
        "ScanIndexForward": False
    }
    
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key
        
    filter_expressions = []
    expression_values = {}
    expression_names = {}

    if environment_filter:
        query_kwargs["IndexName"] = "EnvCreatedAtIndex"
        key_cond = "environment = :env"
        expression_values[":env"] = environment_filter
        
        if from_date and to_date:
            key_cond += " AND created_at BETWEEN :from_date AND :to_date"
            expression_values[":from_date"] = from_date
            expression_values[":to_date"] = to_date
        elif from_date:
            key_cond += " AND created_at >= :from_date"
            expression_values[":from_date"] = from_date
        elif to_date:
            key_cond += " AND created_at <= :to_date"
            expression_values[":to_date"] = to_date
            
        query_kwargs["KeyConditionExpression"] = key_cond
    else:
        query_kwargs["IndexName"] = "AllCreatedAtIndex"
        key_cond = "gsi_pk = :gsi_pk"
        expression_values[":gsi_pk"] = "JOB"
        
        if from_date and to_date:
            key_cond += " AND created_at BETWEEN :from_date AND :to_date"
            expression_values[":from_date"] = from_date
            expression_values[":to_date"] = to_date
        elif from_date:
            key_cond += " AND created_at >= :from_date"
            expression_values[":from_date"] = from_date
        elif to_date:
            key_cond += " AND created_at <= :to_date"
            expression_values[":to_date"] = to_date
            
        query_kwargs["KeyConditionExpression"] = key_cond

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if statuses:
            status_conds = []
            for i, st in enumerate(statuses):
                val_key = f":status_{i}"
                status_conds.append(f"#status = {val_key}")
                expression_values[val_key] = st
            filter_expressions.append("(" + " OR ".join(status_conds) + ")")
            expression_names["#status"] = "status"
            
    if caller_filter:
        filter_expressions.append("caller = :caller")
        expression_values[":caller"] = caller_filter

    if filter_expressions:
        query_kwargs["FilterExpression"] = " AND ".join(filter_expressions)
        
    if expression_names:
        query_kwargs["ExpressionAttributeNames"] = expression_names
        
    if expression_values:
        query_kwargs["ExpressionAttributeValues"] = expression_values

    response = table.query(**query_kwargs)
    items = response.get("Items", [])
    
    last_evaluated_key = response.get("LastEvaluatedKey")
    next_page_token = None
    if last_evaluated_key:
        next_page_token = create_pagination_token(
            last_evaluated_key, status_filter, environment_filter, caller_filter, from_date, to_date, limit
        )
        
    return items, next_page_token
