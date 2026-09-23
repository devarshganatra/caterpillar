import hmac
import hashlib
import json
from copy import deepcopy

def canonical_payload(frame: dict) -> bytes:
    """
    Deterministic canonical form for HMAC signing.
    - Sort keys alphabetically (recursive)
    - Remove the 'sig' key before signing
    - json.dumps with separators=(',', ':'), ensure_ascii=False
    - Encode UTF-8
    """
    f = deepcopy(frame)
    if 'sig' in f:
        del f['sig']
    
    # ensure datetime is string for hashing
    if 'ts' in f and hasattr(f['ts'], 'isoformat'):
        f['ts'] = f['ts'].isoformat().replace("+00:00", "Z")
        if not f['ts'].endswith('Z'):
            f['ts'] = f['ts'] + 'Z'

    return json.dumps(f, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

def sign(frame: dict, secret: str) -> str:
    """Returns HMAC-SHA256 hex digest of canonical_payload(frame)."""
    payload = canonical_payload(frame)
    return hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()

def verify(frame: dict, secret: str) -> bool:
    """Constant-time comparison. Returns False on any error."""
    if 'sig' not in frame:
        return False
    expected_sig = sign(frame, secret)
    return hmac.compare_digest(expected_sig, frame['sig'])
