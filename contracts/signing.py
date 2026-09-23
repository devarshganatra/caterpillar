import hmac
import hashlib
import json
import secrets
from copy import deepcopy

def canonical_payload(frame: dict) -> bytes:
    """
    Deterministic canonical form:
    1. Deep copy
    2. Remove 'sig' key
    3. Stringify datetime fields to ISO 8601 UTC
    4. json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False)
    5. Encode UTF-8
    """
    copy_frame = deepcopy(frame)
    if "sig" in copy_frame:
        del copy_frame["sig"]
    
    class CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, "isoformat"):
                # Pydantic v2 usually outputs Z for UTC, we just want standard ISO 8601
                # The python datetime.isoformat() is safe here.
                return obj.isoformat().replace('+00:00', 'Z')
            return super().default(obj)
            
    # Deterministic JSON
    json_str = json.dumps(
        copy_frame,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        cls=CustomEncoder
    )
    return json_str.encode("utf-8")

def sign(frame: dict, secret: str) -> str:
    """HMAC-SHA256 hex of canonical_payload(frame)."""
    payload = canonical_payload(frame)
    h = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256)
    return h.hexdigest()

def verify(frame: dict, secret: str) -> bool:
    """Constant-time comparison. False on any error."""
    if "sig" not in frame:
        return False
    expected_sig = sign(frame, secret)
    # Constant time comparison to prevent timing attacks
    return hmac.compare_digest(frame["sig"], expected_sig)
