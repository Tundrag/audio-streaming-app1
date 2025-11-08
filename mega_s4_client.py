# mega_s4_client.py - MEGA S4 Object Storage Client with Automatic Retry + Endpoint Failover

import os
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import hashlib
import hmac
from urllib.parse import quote

import aiohttp
import aiofiles
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

def _parse_endpoints() -> List[str]:
    """Read MEGA_S4_ENDPOINTS (comma-separated). Fallback to MEGA_S4_ENDPOINT."""
    endpoints_env = os.getenv("MEGA_S4_ENDPOINTS", "").strip()
    if endpoints_env:
        eps = [e.strip() for e in endpoints_env.split(",") if e.strip()]
        if eps:
            return eps
    single = os.getenv("MEGA_S4_ENDPOINT", "https://s3.eu-central-1.s4.mega.io").strip()
    return [single]

def _is_retryable_status(status: int) -> bool:
    # Typical transient statuses for S3-style services
    return status in (429, 500, 502, 503, 504)

def _is_retryable_exception(e: Exception) -> bool:
    msg = (str(e) or "").lower()
    # Network-ish issues
    keywords = ("timeout", "temporar", "connect", "network", "ssl", "tls", "reset", "unreachable", "refused")
    return any(k in msg for k in keywords)

class MegaS4Client:
    """MEGA S4 Object Storage Client - S3-compatible API with automatic retry + endpoint failover"""

    def __init__(self):
        # Load S4 configuration from environment
        self.access_key = os.getenv("MEGA_S4_ACCESS_KEY")
        self.secret_key = os.getenv("MEGA_S4_SECRET_KEY")
        self.bucket_name = os.getenv("MEGA_S4_BUCKET_NAME", "webaudio")
        self.region = os.getenv("MEGA_S4_REGION", "eu-central-1")

        self.endpoints: List[str] = _parse_endpoints()
        self.endpoint = self.endpoints[0]  # current/active endpoint
        self._endpoint_idx = 0

        # Validate configuration
        if not self.access_key or not self.secret_key:
            raise ValueError("Missing MEGA S4 credentials. Check your .env file.")

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None
        self._started = False

        logger.info(
            "MEGA S4 Client initialized: bucket=%s region=%s endpoints=%s",
            self.bucket_name, self.region, ",".join(self.endpoints)
        )

    # ----------------------- lifecycle -----------------------

    async def start(self):
        """Initialize the HTTP session and probe endpoints to pick a working one."""
        if self._started:
            return

        # Slightly larger timeouts to tolerate slow POPs
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=1800, connect=30, sock_read=600)
        )

        # Probe endpoints in order; pick the first that answers
        await self._select_working_endpoint()

        self._started = True
        logger.info("S4 connection ready on endpoint: %s", self.endpoint)

    async def close(self):
        """Close the HTTP session"""
        if self.session:
            await self.session.close()
            self.session = None
        self._started = False

    def _ensure_started(self):
        if not self._started or not self.session:
            raise RuntimeError("S4 client not started. Call await start() first.")

    # ----------------------- signing -------------------------

    def _create_signature(
        self, method: str, path: str, headers: Dict[str, str],
        query_params: Dict[str, str] = None, payload: bytes = b""
    ) -> str:
        """Create AWS Signature Version 4 with canonical request format"""
        now = datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(payload).hexdigest()

        # Host header must reflect the current endpoint
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = payload_hash
        headers["host"] = self.endpoint.replace("https://", "").replace("http://", "")

        canonical_uri = quote(path, safe="/")

        canonical_querystring = ""
        if query_params:
            sorted_params = sorted(query_params.items())
            canonical_querystring = "&".join(
                [f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in sorted_params]
            )

        canonical_headers = ""
        signed_headers_list = []
        for key in sorted(headers.keys()):
            key_lower = key.lower()
            canonical_headers += f"{key_lower}:{headers[key].strip()}\n"
            signed_headers_list.append(key_lower)

        signed_headers = ";".join(signed_headers_list)
        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        def get_signature_key(key, date_stamp, region_name, service_name):
            k_date = sign(("AWS4" + key).encode("utf-8"), date_stamp)
            k_region = sign(k_date, region_name)
            k_service = sign(k_region, service_name)
            k_signing = sign(k_service, "aws4_request")
            return k_signing

        signing_key = get_signature_key(self.secret_key, date_stamp, self.region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers["Authorization"] = authorization_header
        return authorization_header

    # ----------------------- endpoint selection / failover -----------------------

    async def _probe_head_bucket(self, endpoint: str) -> bool:
        """HEAD the bucket on the given endpoint to test reachability."""
        if not self.session:
            return False
        headers: Dict[str, str] = {}
        # Sign specifically for this endpoint by temporarily swapping self.endpoint
        current = self.endpoint
        try:
            self.endpoint = endpoint
            path = f"/{self.bucket_name}"
            self._create_signature("HEAD", path, headers)
            url = f"{endpoint}{path}"
            async with self.session.head(url, headers=headers) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.debug("Probe failed for %s: %s", endpoint, e)
            return False
        finally:
            self.endpoint = current

    async def _select_working_endpoint(self):
        """Pick the first reachable endpoint from the list."""
        for idx, ep in enumerate(self.endpoints):
            ok = await self._probe_head_bucket(ep)
            if ok:
                self._endpoint_idx = idx
                self.endpoint = ep
                logger.info("Selected S4 endpoint: %s", ep)
                return
            else:
                logger.warning("Endpoint not reachable: %s", ep)
        raise RuntimeError("No reachable S4 endpoints: " + ", ".join(self.endpoints))

    async def _failover_next(self) -> bool:
        """Rotate to the next endpoint that passes probe. Returns True if switched."""
        if len(self.endpoints) <= 1:
            return False
        n = len(self.endpoints)
        start = self._endpoint_idx
        for step in range(1, n + 1):
            idx = (start + step) % n
            ep = self.endpoints[idx]
            if await self._probe_head_bucket(ep):
                self._endpoint_idx = idx
                self.endpoint = ep
                logger.warning("Failover: switched endpoint to %s", ep)
                return True
        return False

    # Helper to try an operation with optional failover on retryable errors
    async def _with_failover(self, op_name: str, action):
        """
        Run an async callable `action()` using the current endpoint.
        If it fails with a retryable error/status, attempt one or more failovers.
        """
        tried: List[Tuple[str, str]] = []
        attempted = 0
        max_switches = len(self.endpoints)

        while attempted < max_switches:
            ep = self.endpoint
            try:
                return await action()
            except aiohttp.ClientResponseError as e:
                tried.append((ep, f"HTTP {e.status}"))
                if _is_retryable_status(e.status) and await self._failover_next():
                    attempted += 1
                    continue
                raise
            except asyncio.TimeoutError as e:
                tried.append((ep, f"Timeout: {e}"))
                if await self._failover_next():
                    attempted += 1
                    continue
                raise
            except aiohttp.ClientError as e:
                tried.append((ep, f"ClientError: {e}"))
                if _is_retryable_exception(e) and await self._failover_next():
                    attempted += 1
                    continue
                raise
            except Exception as e:
                tried.append((ep, f"{type(e).__name__}: {e}"))
                # Only failover for network-ish issues, otherwise bubble up
                if _is_retryable_exception(e) and await self._failover_next():
                    attempted += 1
                    continue
                raise

        detail = "; ".join([f"{ep} -> {err}" for ep, err in tried])
        raise RuntimeError(f"{op_name} failed across endpoints: {detail}")

    # ----------------------- API methods -----------------------

    def generate_object_key(self, filename: str, prefix: str = "") -> str:
        if prefix:
            return f"{prefix}/{filename}"
        return filename

    async def get_bucket_info(self) -> Dict[str, Any]:
        """Get bucket information (lightweight list to count some keys)."""
        self._ensure_started()

        async def _do():
            headers: Dict[str, str] = {}
            self._create_signature("GET", f"/{self.bucket_name}", headers)
            url = f"{self.endpoint}/{self.bucket_name}"
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    text = await response.text()
                    return {
                        "bucket_name": self.bucket_name,
                        "region": self.region,
                        "endpoint": self.endpoint,
                        "object_count": text.count("<Key>"),
                        "total_size_mb": 0,
                    }
                else:
                    error_text = await response.text()
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=error_text
                    )

        return await self._with_failover("get_bucket_info", _do)

    async def upload_file(
        self,
        local_path: Path,
        object_key: str,
        content_type: str = "application/octet-stream",
        max_retries: int = 2,
        retry_delay: float = 2.0,
    ) -> bool:
        """
        Upload file to S4 with per-endpoint retries and automatic failover.

        NOTE: This implementation reads the whole file to compute the SHA256
        for SigV4. It keeps your existing behavior. If you later want true
        streaming + UNSIGNED-PAYLOAD, we can add that too.
        """
        self._ensure_started()

        # Read file (for payload hash in signature)
        async with aiofiles.open(local_path, "rb") as f:
            file_data = await f.read()
        file_size = len(file_data)

        async def _attempt_once():
            headers = {"Content-Type": content_type, "Content-Length": str(file_size)}
            path = f"/{self.bucket_name}/{object_key}"
            self._create_signature("PUT", path, headers, payload=file_data)
            url = f"{self.endpoint}{path}"
            async with self.session.put(url, headers=headers, data=file_data) as response:
                if response.status in (200, 201):
                    logger.info("Uploaded %s (%s bytes) to %s", object_key, file_size, self.endpoint)
                    return True
                error_text = await response.text()
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=error_text
                )

        # Retry loop (per current endpoint) + failover on retryable failures
        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return await self._with_failover("upload_file", _attempt_once)
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    delay = retry_delay * (2 ** (attempt - 1))
                    logger.warning("Upload attempt %d/%d failed: %s; retrying in %.1fs",
                                   attempt, max_retries, e, delay)
                    await asyncio.sleep(delay)
                else:
                    break

        raise Exception(f"Upload failed after {max_retries} attempts: {last_err}")

    async def object_exists(self, object_key: str) -> bool:
        """HEAD an object, with failover."""
        self._ensure_started()

        async def _do():
            headers: Dict[str, str] = {}
            path = f"/{self.bucket_name}/{object_key}"
            self._create_signature("HEAD", path, headers)
            url = f"{self.endpoint}{path}"
            async with self.session.head(url, headers=headers) as response:
                if response.status == 200:
                    return True
                elif response.status == 404:
                    return False
                else:
                    txt = await response.text()
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=txt
                    )

        return await self._with_failover("object_exists", _do)

    async def download_file_stream(self, object_key: str):
        """Download file as stream, with one failover if needed."""
        self._ensure_started()

        async def _do():
            headers: Dict[str, str] = {}
            path = f"/{self.bucket_name}/{object_key}"
            self._create_signature("GET", path, headers)
            url = f"{self.endpoint}{path}"
            resp = await self.session.get(url, headers=headers)
            if resp.status == 200:
                logger.info("Started download stream for %s from %s", object_key, self.endpoint)
                return resp
            # promote non-200 into a handled error so _with_failover can fail over
            txt = await resp.text() if resp.status != 404 else "Not Found"
            await resp.release()
            raise aiohttp.ClientResponseError(
                request_info=resp.request_info,
                history=resp.history,
                status=resp.status,
                message=txt
            )

        return await self._with_failover("download_file_stream", _do)

    async def delete_object(self, object_key: str) -> bool:
        """DELETE object, with failover."""
        self._ensure_started()

        async def _do():
            headers: Dict[str, str] = {}
            path = f"/{self.bucket_name}/{object_key}"
            self._create_signature("DELETE", path, headers)
            url = f"{self.endpoint}{path}"
            async with self.session.delete(url, headers=headers) as response:
                if response.status in (200, 204, 404):
                    logger.info("Deleted object %s (status %s) on %s", object_key, response.status, self.endpoint)
                    return True
                txt = await response.text()
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=txt
                )

        return await self._with_failover("delete_object", _do)

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> List[Dict[str, Any]]:
        """List objects in bucket (simplified XML parse), with failover."""
        self._ensure_started()

        async def _do():
            headers: Dict[str, str] = {}
            query_params = {"max-keys": str(max_keys)}
            if prefix:
                query_params["prefix"] = prefix

            path = f"/{self.bucket_name}"
            self._create_signature("GET", path, headers, query_params)

            qs = "&".join([f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in query_params.items()])
            url = f"{self.endpoint}{path}?{qs}"

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    text = await response.text()
                    objects: List[Dict[str, Any]] = []
                    import re
                    keys = re.findall(r"<Key>(.*?)</Key>", text)
                    sizes = re.findall(r"<Size>(\d+)</Size>", text)
                    modified = re.findall(r"<LastModified>(.*?)</LastModified>", text)
                    for i, key in enumerate(keys):
                        size = int(sizes[i]) if i < len(sizes) else 0
                        mod = modified[i] if i < len(modified) else ""
                        when = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else datetime.utcnow()
                        objects.append({"key": key, "size": size, "last_modified": when, "etag": ""})
                    return objects
                else:
                    txt = await response.text()
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=txt
                    )

        return await self._with_failover("list_objects", _do)

    def generate_presigned_url(self, object_key: str, expires_in: int = 3600) -> str:
        """Simplified direct URL (NOT a signed URL). Kept for backward-compat."""
        path = f"/{self.bucket_name}/{object_key}"
        return f"{self.endpoint}{path}"

# Create singleton instance
mega_s4_client = MegaS4Client()
__all__ = ["mega_s4_client"]
