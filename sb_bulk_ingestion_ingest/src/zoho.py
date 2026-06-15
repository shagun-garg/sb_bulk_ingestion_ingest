import time
import random
import httpx
from typing import Dict, Any

_MOCK_ZOHO_SEEN_IDS = set()

def clear_mock_zoho_seen_ids():
    global _MOCK_ZOHO_SEEN_IDS
    _MOCK_ZOHO_SEEN_IDS.clear()

class ZohoError(Exception):
    def __init__(self, message: str, code: str = "ZOHO_ERROR"):
        super().__init__(message)
        self.code = code

class BaseZohoClient:
    def upsert_settlement(self, record: dict) -> dict:
        raise NotImplementedError()

class RealZohoClient(BaseZohoClient):
    def __init__(self, environment: str):
        self.environment = environment
        self.base_url = "https://www.zohoapis.com/crm/v7/Settlement_Request"

    def upsert_settlement(self, record: dict) -> dict:
        
        try:
            print("call to zoho service")
        except httpx.RequestError as e:
            raise ZohoError(f"Failed to connect to Zoho: {str(e)}", "CONNECTION_ERROR")

class StubZohoClient(BaseZohoClient):
    def __init__(self, environment: str):
        self.environment = environment

    def upsert_settlement(self, record: dict) -> dict:
        global _MOCK_ZOHO_SEEN_IDS
        settlement_id = record.get("settlement_id")
        amount = record.get("settlement_amount")

        if random.random() < 0.01:
            latency = random.uniform(2.0, 5.0)
            time.sleep(latency)

        if settlement_id and settlement_id.endswith("9"):
            raise ZohoError(f"Settlement ID {settlement_id} not found in Zoho", "NOT_FOUND")

        if amount == 0.0:
            raise ZohoError("Settlement amount cannot be exactly zero", "INVALID_AMOUNT")

        if settlement_id in _MOCK_ZOHO_SEEN_IDS:
            action = "updated"
        else:
            _MOCK_ZOHO_SEEN_IDS.add(settlement_id)
            action = "created"

        hash_digits = str(abs(hash(settlement_id)))[:19]
        zoho_id = hash_digits.zfill(19)

        return {
            "zoho_record_id": zoho_id,
            "status": action
        }

def get_zoho_client(environment: str, force_real: bool = False) -> BaseZohoClient:
    if force_real or environment == "production":
        return RealZohoClient(environment)
    return StubZohoClient(environment)
