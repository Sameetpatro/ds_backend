import math
import os
import urllib.parse
from typing import Optional


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def normalize_qr_value(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a QR code value or URL into a clean node identifier.
    Supports:
    - Plain node IDs: "NODE_1", "IIITS-0-KING"
    - HTTPS URLs: "https://my.domain.vercel.app/node/NODE_1"
    - URL with query params / fragments / trailing slashes / URL encoded chars
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None

    # If it's a URL or contains /node/
    if "/node/" in cleaned:
        cleaned = cleaned.split("/node/")[-1]
    elif cleaned.startswith("http://") or cleaned.startswith("https://"):
        try:
            parsed = urllib.parse.urlparse(cleaned)
            cleaned = parsed.path.lstrip("/")
        except Exception:
            pass

    # Strip trailing slashes, query params, fragments
    if "?" in cleaned:
        cleaned = cleaned.split("?")[0]
    if "#" in cleaned:
        cleaned = cleaned.split("#")[0]
    cleaned = cleaned.strip("/").strip()

    # URL decode
    try:
        cleaned = urllib.parse.unquote(cleaned)
    except Exception:
        pass

    cleaned = cleaned.strip()

    # Reject path traversal / invalid chars
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned or not cleaned:
        return None

    return cleaned


def format_qr_url(qr_code_value: str, base_url: Optional[str] = None) -> str:
    """
    Builds the public HTTPS QR URL for a given node identifier.
    """
    base = (base_url or os.getenv("PUBLIC_NODE_BASE_URL", "https://humsafar.vercel.app/node")).rstrip("/")
    clean_id = qr_code_value.strip()
    return f"{base}/{clean_id}"

# ── Base62 Node ID Utilities (Counter starting at 100001) ──────────────────────
BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(BASE62_ALPHABET)


def encode_base62(num: int) -> str:
    """
    Encodes a non-negative integer into a Base62 alphanumeric string.
    E.g. 100001 -> 'Q0v', 100002 -> 'Q0w'
    """
    if num < 0:
        raise ValueError("Cannot encode negative integers in Base62.")
    if num == 0:
        return BASE62_ALPHABET[0]
    digits = []
    n = num
    while n > 0:
        digits.append(BASE62_ALPHABET[n % BASE])
        n //= BASE
    return "".join(reversed(digits))


def decode_base62(code: str) -> int:
    """
    Decodes a Base62 string back into an integer counter.
    E.g. 'Q0v' -> 100001
    """
    if not code:
        raise ValueError("Cannot decode empty string.")
    num = 0
    for ch in code.strip():
        idx = BASE62_ALPHABET.find(ch)
        if idx == -1:
            raise ValueError(f"Invalid character '{ch}' in Base62 code '{code}'.")
        num = num * BASE + idx
    return num


def is_base62(candidate: Optional[str]) -> bool:
    """
    Checks if a string consists entirely of Base62 alphanumeric characters.
    """
    if not candidate or not isinstance(candidate, str):
        return False
    c = candidate.strip()
    return bool(c) and all(ch in BASE62_ALPHABET for ch in c)


def get_next_node_base62(db, start_counter: int = 100001) -> str:
    """
    Allocates the next sequential counter (starting at 100001) and returns its Base62 encoding.
    Uses PostgreSQL sequence 'node_qr_counter_seq' in production, or safe fallback for SQLite.
    """
    from sqlalchemy import text
    try:
        bind = db.get_bind()
        dialect = bind.dialect.name if bind else "postgresql"
    except Exception:
        dialect = "postgresql"

    if dialect == "postgresql":
        try:
            res = db.execute(text("SELECT nextval('node_qr_counter_seq')"))
            val = res.scalar()
            return encode_base62(int(val))
        except Exception:
            db.rollback()
            db.execute(text(f"CREATE SEQUENCE IF NOT EXISTS node_qr_counter_seq START WITH {start_counter};"))
            db.commit()
            res = db.execute(text("SELECT nextval('node_qr_counter_seq')"))
            val = res.scalar()
            return encode_base62(int(val))
    else:
        # SQLite / in-memory fallback
        from app.models import Node
        count = db.query(Node).count()
        counter = start_counter + count
        while True:
            code = encode_base62(counter)
            if not db.query(Node).filter(Node.qr_code_value == code).first():
                return code
            counter += 1
