import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Any
from enum import Enum
from .config import CIVITAI_BASE_URL, ARCENCIEL_BASE_URL, KEY_CIVITAI_API_KEY, KEY_ARCENCIEL_API_KEY
from .metadata import sanitize_base_model

logger = logging.getLogger("models-info")

try:
    from modules import shared
except ImportError:
    shared = None

class ServiceName(Enum):
    CIVITAI = "civitai"
    ARCENCIEL = "arcenciel"

@dataclass
class ServiceConfig:
    name: ServiceName
    base_url: str
    hash_endpoint_template: str  # endpoint pattern for hash lookup
    model_detail_endpoint: str   # endpoint pattern for model detail (if needed)
    auth_header_key: str
    auth_header_value_template: str
    needs_detail_call: bool      # whether tags require a second API call

SERVICE_CONFIGS = {
    ServiceName.CIVITAI: ServiceConfig(
        name=ServiceName.CIVITAI,
        base_url=CIVITAI_BASE_URL,
        hash_endpoint_template="/model-versions/by-hash/{hash}",
        model_detail_endpoint="/models/{model_id}",
        auth_header_key="Authorization",
        auth_header_value_template="Bearer {key}",
        needs_detail_call=True,
    ),
    ServiceName.ARCENCIEL: ServiceConfig(
        name=ServiceName.ARCENCIEL,
        base_url=ARCENCIEL_BASE_URL,
        # hashOnly=true returns only models whose version sha256 matches the query.
        # includeTags=true populates the tags array on each model.
        hash_endpoint_template="/api/models/search?search={hash}&hashOnly=true&includeTags=true",
        model_detail_endpoint="",
        auth_header_key="x-api-key",
        auth_header_value_template="{key}",
        needs_detail_call=False,
    ),
}

@dataclass
class ModelMetadata:
    model_id: int
    version_id: int
    name: str
    base_model: str
    model_type: str
    tags: List[str]
    trained_words: List[str]
    external_url: str
    preview_images: List[dict]  # [{url, width, height, nsfw/rating, type}]
    description: str
    source: str
    version_name: str = ""
    hashes: dict = None
    file_metadata: dict = None
    raw_response: dict = None

class ApiClient:
    """Async HTTP client with automatic fallback between services."""

    def __init__(self, mock_settings: Optional[dict] = None):
        self.session: Optional[Any] = None
        self.mock_settings = mock_settings or {}
        
        # Pacing: minimum seconds between consecutive requests per service
        # Civitai: 0.25s (with key) to 0.6s (without key)
        self._min_interval = {
            ServiceName.CIVITAI: 0.35,
            ServiceName.ARCENCIEL: 0.20
        }
        self._last_request_time = {
            ServiceName.CIVITAI: 0.0,
            ServiceName.ARCENCIEL: 0.0
        }
        # Synchronization lock per service to serialize spacing & 429 handling
        self._service_locks = {
            ServiceName.CIVITAI: asyncio.Lock(),
            ServiceName.ARCENCIEL: asyncio.Lock()
        }
        self._rate_limit_until = {
            ServiceName.CIVITAI: 0.0,
            ServiceName.ARCENCIEL: 0.0
        }

    async def _ensure_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _get_api_key(self, service: ServiceName) -> str:
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            key = shared.opts.data.get(KEY_CIVITAI_API_KEY if service == ServiceName.CIVITAI else KEY_ARCENCIEL_API_KEY, "")
            if key:
                return key
        return self.mock_settings.get(KEY_CIVITAI_API_KEY if service == ServiceName.CIVITAI else KEY_ARCENCIEL_API_KEY, "")

    def _get_headers(self, service: ServiceName) -> dict:
        config = SERVICE_CONFIGS[service]
        key = self._get_api_key(service)
        if not key:
            return {}
        return {
            config.auth_header_key: config.auth_header_value_template.format(key=key)
        }

    async def _pace_request(self, service: ServiceName):
        """Ensure requests to the given service are paced and respect global rate-limit cooldowns."""
        async with self._service_locks[service]:
            now = time.time()
            
            # 1. If currently in a global 429 rate-limit cooldown, wait for it to clear
            wait_cooldown = self._rate_limit_until[service] - now
            if wait_cooldown > 0:
                logger.info(f"Waiting {wait_cooldown:.1f}s for {service.value} rate-limit window to expire...")
                await asyncio.sleep(wait_cooldown)
                now = time.time()

            # 2. Dynamic pacing interval based on whether an API key is configured
            has_key = bool(self._get_api_key(service))
            min_delay = 0.25 if has_key else 0.60
            if service == ServiceName.ARCENCIEL:
                min_delay = 0.20

            # 3. Space out consecutive calls
            elapsed = now - self._last_request_time[service]
            if elapsed < min_delay:
                await asyncio.sleep(min_delay - elapsed)

            self._last_request_time[service] = time.time()

    async def _request_with_retry(self, service: ServiceName, url: str,
                                    max_retries: int = 4) -> Optional[dict]:
        """Execute GET request with global pacing and synchronized backoff on rate limits."""
        await self._ensure_session()
        headers = self._get_headers(service)

        for attempt in range(max_retries):
            # Enforce global pacing before firing request
            await self._pace_request(service)

            try:
                # API calls should not be logged to console
                async with self.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    elif resp.status == 404:
                        logger.debug(f"Not found on {service.value}")
                        return None

                    elif resp.status == 429:
                        # Extract Retry-After header or compute exponential fallback
                        try:
                            retry_after_hdr = resp.headers.get("Retry-After")
                            retry_after = float(retry_after_hdr) if retry_after_hdr else float(2 ** (attempt + 2))
                        except Exception:
                            retry_after = float(2 ** (attempt + 2))

                        # Cap minimum backoff to at least 3 seconds
                        retry_after = max(retry_after, 3.0)

                        # Set global service cooldown so ALL concurrent tasks wait synchronously
                        self._rate_limit_until[service] = time.time() + retry_after
                        logger.warning(f"Rate limited by {service.value} (HTTP 429), pausing all requests for {retry_after:.1f}s (attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        continue

                    elif resp.status == 401:
                        logger.error(f"Invalid API key for {service.value}")
                        return None

                    else:
                        logger.warning(f"HTTP {resp.status} from {service.value}")
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"Timeout querying {service.value} (attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(1.5 ** attempt)
            except Exception as e:
                import aiohttp
                if isinstance(e, aiohttp.ClientError):
                    logger.error(f"Network error for {service.value}: {e}")
                else:
                    logger.error(f"Request error for {service.value}: {e}")
                await asyncio.sleep(1.5 ** attempt)

        logger.error(f"Exhausted retries for {service.value}")
        return None

    async def lookup_by_hash(self, sha256: str, primary: ServiceName,
                               secondary: ServiceName, filename: str = "", model_type: str = "") -> Optional[ModelMetadata]:
        """
        Look up model metadata by SHA256 hash.
        Queries primary service first; falls back to secondary on miss.
        """
        for service in [primary, secondary]:
            if service == ServiceName.ARCENCIEL and len(sha256) < 64:
                logger.debug(f"Skipping Arcenciel for non-SHA256 hash '{sha256}'")
                continue

            service_title = "Civitai" if service == ServiceName.CIVITAI else "Arcenciel"
            logger.debug(f"Checking {model_type} '{filename}' on {service_title}")
            config = SERVICE_CONFIGS[service]

            # Step 1: Hash-based search
            hash_url = f"{config.base_url}{config.hash_endpoint_template.format(hash=sha256)}"
            raw_data = await self._request_with_retry(service, hash_url)
            if not raw_data:
                logger.debug(f"No result from {service.value} for hash")
                continue

            # Step 2: Service-specific parsing + preview images
            if service == ServiceName.CIVITAI:
                meta = await self._parse_civitai(raw_data, config)
            else:
                meta = await self._lookup_arcenciel(raw_data, sha256, config)

            if meta is not None:
                return meta

            logger.debug(f"No matching metadata on {service_title} for hash")

        return None

    async def _lookup_arcenciel(self, search_response: dict, sha256: str,
                                 config: ServiceConfig) -> Optional[ModelMetadata]:
        """
        Full Arcenciel 2-step lookup:
          Step 1 result: parse search response to extract modelId + versionId.
          Step 2: GET /api/models/{modelId}/versions/media-preview?versionIds={versionId}
                  to obtain preview images for the specific version.
        """
        # --- Step 1: Parse search response ---
        logger.debug("Arcenciel: parsing search response...")
        meta = self._parse_arcenciel(search_response, sha256)
        if not meta:
            logger.debug("Arcenciel: no matching version found in search results")
            return None

        logger.debug(
            f"Arcenciel: matched model \"{meta.name}\" "
            f"(modelId={meta.model_id}, versionId={meta.version_id}, "
            f"baseModel={meta.base_model})"
        )

        # --- Step 2: Fetch preview images for this specific version ---
        media_url = (
            f"{config.base_url}/api/models/{meta.model_id}/versions/media-preview"
            f"?versionIds={meta.version_id}&limit=12"
        )
        logger.debug("Arcenciel: fetching media previews")
        media_data = await self._request_with_retry(ServiceName.ARCENCIEL, media_url)

        if not media_data:
            logger.warning("Arcenciel: preview returned no data")
            return meta

        if not isinstance(media_data, dict):
            logger.warning("Arcenciel: unexpected response type")
            return meta

        preview_images = _parse_arcenciel_images(media_data)
        logger.debug("Arcenciel: retrieved preview image(s)")

        meta.preview_images = preview_images
        if meta.raw_response is not None:
            meta.raw_response["media_preview"] = media_data

        return meta

    async def _parse_civitai(self, version_data: dict,
                                 config: ServiceConfig) -> Optional[ModelMetadata]:
        """Parse Civitai response (ModelVersion) and fetch model detail for tags."""
        model_id = version_data.get("modelId", 0)
        version_id = version_data.get("id", 0)

        logger.debug(
            f"Civitai: matched modelId={model_id}, versionId={version_id}, "
            f"baseModel={version_data.get('baseModel', 'Unknown')}"
        )

        # Fetch full model for tags
        model_data = None
        if model_id and config.needs_detail_call:
            detail_url = f"{config.base_url}{config.model_detail_endpoint.format(model_id=model_id)}"
            logger.debug("Civitai: fetching model detail")
            model_data = await self._request_with_retry(ServiceName.CIVITAI, detail_url)

        external_url = f"https://civitai.red/models/{model_id}?modelVersionId={version_id}"

        raw_tags = (model_data or {}).get("tags", [])
        tags = [t if isinstance(t, str) else str(t) for t in raw_tags]
        logger.debug(f"Civitai: model — {len(tags)} tag(s)")

        # Extract deep hashes and file metadata
        hashes_dict = {}
        file_meta = {}
        files = version_data.get("files", [])
        if files:
            primary_file = files[0]
            for f in files:
                if f.get("type") == "Model":
                    primary_file = f
                    break
            hashes_dict = primary_file.get("hashes", {})
            file_meta = {
                "name": primary_file.get("name", ""),
                "sizeKB": primary_file.get("sizeKB"),
                "type": primary_file.get("type", ""),
                "metadata": primary_file.get("metadata", {})
            }

        preview_images = [
            {
                "url": img.get("url", ""),
                "width": img.get("width"),
                "height": img.get("height"),
                "nsfw": resolve_nsfw_rating(img),
                "type": img.get("type", "image")
            }
            for img in version_data.get("images", [])
        ]
        logger.debug("Civitai: preview image(s) available")

        raw_response = {
            "version_data": version_data,
            "model_data": model_data
        }

        return ModelMetadata(
            model_id=model_id,
            version_id=version_id,
            name=(model_data or {}).get("name", version_data.get("name", "Unknown")),
            base_model=sanitize_base_model(version_data.get("baseModel", "Unknown")),
            model_type=(model_data or {}).get("type", "Unknown"),
            tags=tags,
            trained_words=version_data.get("trainedWords", []),
            external_url=external_url,
            preview_images=preview_images,
            description=version_data.get("description", ""),
            source=ServiceName.CIVITAI.value,
            version_name=version_data.get("name", ""),
            hashes=hashes_dict,
            file_metadata=file_meta,
            raw_response=raw_response,
        )

    def _parse_arcenciel(self, search_response: dict,
                          sha256: str) -> Optional[ModelMetadata]:
        """
        Parse Arcenciel /api/models/search response to extract model/version metadata.

        The search response shape (when hashOnly=true) is:
          { page, limit, totalCount, totalPages, data: [Model] }

        Each Model has a versions[] array. We find the version whose sha256
        matches the queried hash and return the associated metadata.
        Preview images are intentionally left empty here — they are fetched
        separately in _lookup_arcenciel via the media-preview endpoint.
        """
        models = search_response.get("data", [])
        if not models:
            return None

        sha256_lower = sha256.lower()

        for model in models:
            for version in model.get("versions", []):
                version_hash = (version.get("sha256") or "").lower()
                if not version_hash:
                    continue
                # Allow prefix match in case the API returns a truncated hash
                if not (sha256_lower.startswith(version_hash) or version_hash.startswith(sha256_lower)):
                    continue

                model_id = model.get("id", 0)
                version_id = version.get("id", 0)

                # Tags: Arcenciel returns tag objects {id, name} when includeTags=true
                raw_tags = model.get("tags", [])
                tags = [
                    t["name"] if isinstance(t, dict) and "name" in t else str(t)
                    for t in raw_tags
                ]

                hashes_dict = {"SHA256": version.get("sha256", "")}
                file_meta = {
                    "name": version.get("versionName", ""),
                    "size": version.get("size", "")
                }

                raw_response = {
                    "model": model,
                    "version": version
                }

                return ModelMetadata(
                    model_id=model_id,
                    version_id=version_id,
                    name=model.get("title", "Unknown"),
                    base_model=sanitize_base_model(version.get("baseModel", "Unknown")),
                    model_type=model.get("type", "Unknown"),
                    tags=tags,
                    trained_words=version.get("activationTags", []),
                    external_url=f"https://arcenciel.io/models/{model_id}",
                    preview_images=[],
                    description=model.get("description", ""),
                    source=ServiceName.ARCENCIEL.value,
                    version_name=version.get("versionName", ""),
                    hashes=hashes_dict,
                    file_metadata=file_meta,
                    raw_response=raw_response,
                )

        return None


def _parse_arcenciel_images(media_data: dict) -> List[dict]:
    """
    Convert Arcenciel media-preview response (nested dictionary) into the
    normalised preview_images format used across the extension.
    """
    result = []
    if not isinstance(media_data, dict):
        return result

    versions = media_data.get("versions", [])
    for version in versions:
        if not isinstance(version, dict):
            continue
        entries = version.get("entries", [])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
            data = entry.get("data")
            if not isinstance(data, dict):
                continue

            # Try to get the w2048 variant first, then fallback to other variants, then to main filePath
            variants = data.get("variants", [])
            path = ""
            if isinstance(variants, list) and variants:
                # Find w2048
                for var in variants:
                    if isinstance(var, dict) and var.get("label") == "w2048":
                        path = var.get("path", "")
                        break
                # If not found, find fallback or w1024
                if not path:
                    for label in ("w1024", "fallback", "w512"):
                        for var in variants:
                            if isinstance(var, dict) and var.get("label") == label:
                                path = var.get("path", "")
                                break
                        if path:
                            break
            
            if not path:
                path = data.get("filePath", "")
                
            if not path:
                continue

            clean_path = path.lstrip("/")
            if not clean_path.startswith("uploads/"):
                clean_path = "uploads/" + clean_path
            url = f"https://media.arcenciel.io/{clean_path}"

            # Detect video/animated formats by extension or entry kind
            kind = str(entry.get("kind", "image")).lower()
            lower_path = clean_path.lower()
            img_type = "video" if (kind == "video" or lower_path.endswith((".gif", ".webm", ".mp4", ".m4v", ".mov", ".avi"))) else "image"

            result.append({
                "url": url,
                "width": None,
                "height": None,
                "nsfw": data.get("rating", "safe"),
                "type": img_type
            })
    return result

def resolve_nsfw_rating(img: dict) -> str:
    """Safely extract and map the NSFW rating string from Civitai's nsfwLevel or nsfw keys."""
    nsfw_level = img.get("nsfwLevel")
    if nsfw_level is not None:
        if isinstance(nsfw_level, str):
            if nsfw_level.isdigit():
                nsfw_level = int(nsfw_level)
            else:
                return nsfw_level

        if isinstance(nsfw_level, int):
            if nsfw_level == 1:
                return "None"
            elif nsfw_level == 2:
                return "Soft"
            elif nsfw_level == 4:
                return "Mature"
            elif nsfw_level >= 8:
                return "X"

    raw_nsfw = img.get("nsfw")
    if isinstance(raw_nsfw, bool):
        return "Mature" if raw_nsfw else "None"
    elif isinstance(raw_nsfw, str):
        return raw_nsfw
    return "None"

