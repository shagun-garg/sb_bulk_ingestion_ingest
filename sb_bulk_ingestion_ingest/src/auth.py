import hmac
import jwt
from datetime import datetime, timezone
from fastapi import Header, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict, Any

from src.config import JWT_SECRET, JWT_ALGORITHM, API_KEYS, API_KEY_ROLES, API_KEY_LABELS

security_scheme = HTTPBearer(auto_error=False)

ROLES = {
    "viewer": 1,
    "operator": 2,
    "admin": 3
}

class UserIdentity:
    def __init__(self, username: str, role: str, auth_method: str):
        self.username = username
        self.role = role
        self.auth_method = auth_method

def verify_role(required_role: str, user: UserIdentity):
    required_level = ROLES.get(required_role, 0)
    user_level = ROLES.get(user.role, 0)
    if user_level < required_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operation requires {required_role} role or above. User has {user.role}."
        )

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_skybridge_api_key: Optional[str] = Header(None, alias="x-skybridge-api-key")
) -> UserIdentity:
    
    if x_skybridge_api_key:
        valid_key = None
        for key in API_KEYS:
            if hmac.compare_digest(x_skybridge_api_key, key):
                valid_key = key
                break
        
        if valid_key:
            role = API_KEY_ROLES.get(valid_key, "viewer")
            label = API_KEY_LABELS.get(valid_key, "api-key-caller")
            return UserIdentity(username=label, role=role, auth_method="api_key")
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key"
            )

    if credentials:
        token = credentials.credentials
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            sub = payload.get("sub")
            role = payload.get("role")
            if not sub or not role:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token claims. sub and role are required."
                )
            return UserIdentity(username=sub, role=role, auth_method="jwt")
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired"
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid authentication credentials"
    )

def require_viewer(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
    verify_role("viewer", user)
    return user

def require_operator(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
    verify_role("operator", user)
    return user

def require_admin(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
    verify_role("admin", user)
    return user
