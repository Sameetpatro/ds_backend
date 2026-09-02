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