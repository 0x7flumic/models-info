import asyncio
import logging
import os
import threading
from typing import List, Callable, Optional, Union, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
import json
import io
import re

from .api_client import ServiceName, ApiClient
from .arch_inspector import detect_architecture
from .config import (
    KEY_PRIMARY_SOURCE, KEY_CONCURRENT_REQUESTS, KEY_NSFW_FILTER,
    KEY_PREVIEW_WIDTH, KEY_AUTO_SORT_LORA, KEY_HASH_ALGO,
    KEY_AUTO_RENAME, KEY_RENAME_FORMAT, KEY_DUPLICATE_ACTION, KEY_RENAME_CACHED,
    DEFAULT_PRIMARY_SOURCE, DEFAULT_CONCURRENT_REQUESTS, DEFAULT_NSFW_FILTER,
    DEFAULT_PREVIEW_WIDTH, DEFAULT_HASH_ALGO,
    DEFAULT_AUTO_RENAME, DEFAULT_RENAME_FORMAT, DEFAULT_DUPLICATE_ACTION, DEFAULT_RENAME_CACHED
)

logger = logging.getLogger("models-info")

try:
    from modules import shared
except ImportError:
    shared = None

@dataclass
class ScanResult:
    filepath: str
    filename: str
    status: str       # "found", "not_found", "error", "skipped", "cached"
    source: str       # "civitai", "arcenciel", ""
    base_model: str
    message: str

class ScannerOrchestrator:
    """Orchestrates the full scan pipeline: enumerate → hash → query → save."""

    def __init__(self, hash_engine, api_client: ApiClient, metadata_manager, file_operations):
        self.hash_engine = hash_engine
        self.api_client = api_client
        self.metadata_mgr = metadata_manager
        self.file_ops = file_operations
        self._cancel_event = threading.Event()
        self._active_thread: Optional[threading.Thread] = None

    def cancel(self):
        self._cancel_event.set()

    def is_running(self) -> bool:
        return self._active_thread is not None and self._active_thread.is_alive()

    def start_scan(self, directory: Union[str, List[str]], model_type: str,
                   scan_missing_only: bool,
                   progress_callback: Callable[[int, int, str], None],
                   completion_callback: Callable[[List[ScanResult]], None]):
        """Launch scan in a background thread (owns its own asyncio event loop)."""
        if self.is_running():
            logger.warning("Scan already in progress")
            return

        self._cancel_event.clear()
        self._active_thread = threading.Thread(
            target=self._run_scan_thread,
            args=(directory, model_type,
                  scan_missing_only, progress_callback, completion_callback),
            daemon=True
        )
        self._active_thread.start()

    def _run_scan_thread(self, directory, model_type,
                          scan_missing_only, progress_callback, completion_callback):
        """Background thread entry point — creates its own asyncio loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        thread_api_client = ApiClient()
        try:
            results = loop.run_until_complete(
                self._scan_pipeline(directory, model_type,
                                     scan_missing_only, progress_callback, api_client=thread_api_client)
            )
            completion_callback(results)
        except Exception as e:
            logger.error(f"Scan failed: {e}", exc_info=True)
            completion_callback([ScanResult("", "", "error", "", "", str(e))])
        finally:
            loop.run_until_complete(thread_api_client.close())
            loop.close()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def _run_sorting_logic(self, filepath: str, model_type: str, metadata: Any, directory: str) -> str:
        """Helper to run category-based LoRA sorting logic."""
        if model_type != "LoRA":
            return filepath

        auto_sort = False
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            auto_sort = shared.opts.data.get(KEY_AUTO_SORT_LORA, False)

        if not auto_sort:
            return filepath

        if isinstance(metadata, dict):
            tags = metadata.get("tags")
        else:
            tags = getattr(metadata, "tags", None)

        if not tags:
            filename = os.path.basename(filepath)
            logger.debug(f"LoRA '{filename}' has no tags metadata, keeping at root")
            return filepath

        CATEGORIES_MAPPING = {
            "Character": {"character", "celebrity"},
            "Style": {"style"},
            "Clothing": {"clothing", "fashion"},
            "Concept": {"concept"},
            "Pose": {"pose", "action"},
            "Background": {"background", "environment", "objects"},
            "Utility": {"utility"}
        }

        matched_category = None
        matched_tag = None
        for tag in tags:
            tag_clean = str(tag).strip().lower()
            for category, tags_set in CATEGORIES_MAPPING.items():
                for t in tags_set:
                    if re.search(rf"\b{re.escape(t)}\b", tag_clean):
                        matched_category = category
                        matched_tag = tag
                        break
                if matched_category:
                    break
            if matched_category:
                break

        if matched_category:
            expected_subfolder = os.path.join(directory, matched_category)
            if os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(expected_subfolder):
                return filepath

            filename = os.path.basename(filepath)
            logger.debug(f"Auto-sorting LoRA '{filename}' (matched tag: '{matched_tag}') to '{matched_category}'")
            return self.file_ops.sort_into_subfolder(filepath, matched_category, directory, metadata)
        else:
            filename = os.path.basename(filepath)
            logger.debug(f"LoRA '{filename}' tags {tags} have no category matches, keeping at root")
            return filepath

    async def _scan_pipeline(self, directory: Union[str, List[str]], model_type: str,
                               scan_missing_only: bool, progress_callback: Callable, api_client: Optional[ApiClient] = None) -> List[ScanResult]:
        """Core async pipeline."""
        if api_client is None:
            api_client = self.api_client

        model_type_singular = "Checkpoint" if "checkpoint" in model_type.lower() else "LoRA"

        # Step 1: Enumerate model files across all directories
        extensions = ('.safetensors', '.ckpt', '.pt', '.bin')
        if isinstance(directory, (list, tuple)):
            scan_dirs = [os.path.abspath(d) for d in directory if os.path.isdir(d)]
        elif isinstance(directory, str) and os.path.isdir(directory):
            scan_dirs = [os.path.abspath(directory)]
        else:
            scan_dirs = []

        model_files = []
        for d in scan_dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(extensions):
                        model_files.append(os.path.join(root, f))

        def get_model_root(fpath: str) -> str:
            abs_fp = os.path.abspath(fpath)
            for d in scan_dirs:
                if abs_fp.startswith(d + os.sep) or abs_fp == d:
                    return d
            return scan_dirs[0] if scan_dirs else os.path.dirname(fpath)

        total = len(model_files)
        if total == 0:
            logger.info(f"Scan started: No {model_type} files found to scan")
            return []

        logger.info(f"Scan started: {total} {model_type_singular}{'s' if total > 1 else ''} queued")

        # Determine primary/secondary service order
        primary_str = DEFAULT_PRIMARY_SOURCE
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            primary_str = shared.opts.data.get(KEY_PRIMARY_SOURCE, DEFAULT_PRIMARY_SOURCE)
            
        primary = ServiceName(primary_str)
        secondary = (ServiceName.ARCENCIEL if primary == ServiceName.CIVITAI
                      else ServiceName.CIVITAI)

        max_concurrent = DEFAULT_CONCURRENT_REQUESTS
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            max_concurrent = int(shared.opts.data.get(KEY_CONCURRENT_REQUESTS, DEFAULT_CONCURRENT_REQUESTS))

        auto_rename = DEFAULT_AUTO_RENAME
        rename_format = DEFAULT_RENAME_FORMAT
        duplicate_action = DEFAULT_DUPLICATE_ACTION
        rename_cached = DEFAULT_RENAME_CACHED
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            auto_rename = bool(shared.opts.data.get(KEY_AUTO_RENAME, DEFAULT_AUTO_RENAME))
            rename_format = shared.opts.data.get(KEY_RENAME_FORMAT, DEFAULT_RENAME_FORMAT)
            duplicate_action = shared.opts.data.get(KEY_DUPLICATE_ACTION, DEFAULT_DUPLICATE_ACTION)
            rename_cached = bool(shared.opts.data.get(KEY_RENAME_CACHED, DEFAULT_RENAME_CACHED))
            
        semaphore = asyncio.Semaphore(max_concurrent)
        hash_semaphore = asyncio.Semaphore(1)

        # Monotonic, thread-safe counter variables for progress updates
        completed_count = 0
        completed_lock = asyncio.Lock()

        async def process_model(index: int, filepath: str):
            nonlocal completed_count
            if self._cancel_event.is_set():
                return ScanResult(filepath, os.path.basename(filepath),
                                  "skipped", "", "", "Cancelled")

            filename = os.path.basename(filepath)

            # Smart check: skip already scanned models instantly to make rescans subsecond.
            # Only skip if BOTH metadata AND a local preview image already exist.
            if self.metadata_mgr.has_metadata(filepath):
                existing = self.metadata_mgr.read(filepath) or {}
                preview_local = existing.get("previewLocalPath", "")
                preview_exists = (
                    preview_local and
                    os.path.exists(os.path.join(os.path.dirname(filepath), preview_local))
                )
                if existing.get("model_id") and existing.get("baseModel") and preview_exists:
                    # Deterministically refine baseModel parameter architecture if needed
                    current_base = existing.get("baseModel", "")
                    refined_base = detect_architecture(filepath, current_base)
                    need_write = False
                    if refined_base and refined_base != current_base:
                        existing["baseModel"] = refined_base
                        need_write = True
                        logger.debug(f"Refined baseModel for cached model '{filename}': {current_base} -> {refined_base}")

                    if not existing.get("displayName") and (existing.get("name") or existing.get("versionName")):
                        existing["displayName"] = self.file_ops.generate_display_name(
                            existing.get("name", ""), existing.get("versionName", "")
                        )
                        need_write = True

                    if need_write:
                        self.metadata_mgr.write(filepath, existing)

                    async with completed_lock:
                        completed_count += 1
                        logger.debug(f"[{completed_count}/{total}] Cached: '{filename}' ({existing.get('baseModel', 'Unknown')})")
                        progress_callback(completed_count, total, f"Cached: {filename}")

                    # When scan_missing_only is active, bypass auto-rename and category sorting to leave existing models intact
                    if scan_missing_only:
                        return ScanResult(filepath, filename, "cached",
                                          existing.get("source", ""), existing.get("baseModel", ""),
                                          "Already has metadata")

                    # If auto_rename and rename_cached are enabled, check if rename is needed (force rescan only)
                    cached_final_filepath = filepath
                    if auto_rename and rename_cached:
                        c_model_name = existing.get("name", "")
                        c_version_name = existing.get("versionName", "")
                        c_orig_name = (existing.get("file_metadata") or {}).get("name", "")
                        _, c_ext = os.path.splitext(filepath)
                        c_target = self.file_ops.generate_model_filename(
                            c_model_name, c_version_name, c_orig_name, c_ext, rename_format
                        )
                        if c_target != os.path.basename(filepath):
                            loop = asyncio.get_running_loop()
                            try:
                                renamed_cached = await loop.run_in_executor(
                                    None,
                                    self.file_ops.rename_model,
                                    filepath,
                                    c_target,
                                    True,
                                    duplicate_action,
                                    existing.get("sha256"),
                                    self.hash_engine
                                )
                                if renamed_cached != filepath:
                                    try:
                                        from scripts.models_info import invalidate_metadata_cache
                                        invalidate_metadata_cache(filepath)
                                        invalidate_metadata_cache(renamed_cached)
                                    except Exception:
                                        pass
                                    cached_final_filepath = renamed_cached
                                    filename = os.path.basename(cached_final_filepath)
                            except Exception as e:
                                logger.error(f"Failed to auto-rename cached model '{filename}': {e}")
                    
                    # Run category-based LoRA sorting logic even if cached (force rescan only)
                    final_filepath = cached_final_filepath
                    if model_type == "LoRA":
                        root_dir = get_model_root(cached_final_filepath)
                        loop = asyncio.get_running_loop()
                        final_filepath = await loop.run_in_executor(
                            None, self._run_sorting_logic, cached_final_filepath, model_type, existing, root_dir
                        )
                        
                    return ScanResult(final_filepath, os.path.basename(final_filepath), "cached",
                                      existing.get("source", ""), existing.get("baseModel", ""),
                                      "Already has metadata")
                elif existing.get("model_id") and existing.get("baseModel"):
                    logger.debug(f"Metadata exists but no preview — will retry download: '{filename}'")

            # Step 2: Hash calculation (configurable Fast AutoV1 with seamless SHA256 fallback)
            def hash_progress(bytes_read, total_bytes):
                pct = bytes_read / total_bytes
                progress_callback(completed_count, total, f"Hashing {filename}... {pct:.0%}")

            configured_algo = DEFAULT_HASH_ALGO
            if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
                configured_algo = shared.opts.data.get(KEY_HASH_ALGO, DEFAULT_HASH_ALGO)
            try_quick = "Quick" in configured_algo or "AutoV1" in configured_algo

            computed_hash = ""
            if try_quick:
                computed_hash = self.hash_engine.get_quick_hash(filepath) or ""

            if not computed_hash:
                logger.debug(f"Computing SHA256 hash for '{filename}'")
                try:
                    loop = asyncio.get_running_loop()
                    async with hash_semaphore:
                        computed_hash = await loop.run_in_executor(
                            None, self.hash_engine.get_hash, filepath, hash_progress, "sha256"
                        )
                except Exception as e:
                    logger.error(f"Hash calculation failed for '{filename}': {e}")
                    async with completed_lock:
                        completed_count += 1
                        progress_callback(completed_count, total, f"Error: {filename}")
                    return ScanResult(filepath, filename, "error", "", "", f"Hash error: {e}")

            if self._cancel_event.is_set():
                return ScanResult(filepath, filename, "skipped", "", "", "Cancelled")

            # Step 3: API query with fallback
            async with semaphore:
                metadata = await api_client.lookup_by_hash(computed_hash, primary, secondary, filename, model_type_singular)

            # If quick hash missed on primary/secondary, seamlessly compute full SHA256 and retry
            if metadata is None and len(computed_hash) < 64:
                logger.info(f"Quick hash missed for '{filename}', calculating full SHA256...")
                try:
                    loop = asyncio.get_running_loop()
                    async with hash_semaphore:
                        computed_hash = await loop.run_in_executor(
                            None, self.hash_engine.get_hash, filepath, hash_progress, "sha256"
                        )
                    async with semaphore:
                        metadata = await api_client.lookup_by_hash(computed_hash, primary, secondary, filename, model_type_singular)
                except Exception as e:
                    logger.warning(f"Fallback SHA256 lookup failed for '{filename}': {e}")

            sha256 = computed_hash

            if metadata is None:
                async with completed_lock:
                    completed_count += 1
                    logger.info(f"[{completed_count}/{total}] Not Found: '{filename}'")
                    progress_callback(completed_count, total, f"Not Found: {filename}")
                return ScanResult(filepath, filename, "not_found", "", "",
                                  "Not found on either service")

            # Step 4: Save metadata sidecar
            existing_sidecar = self.metadata_mgr.read(filepath) or {}
            existing_sha256 = existing_sidecar.get("sha256", "")

            # Deduce sha256 if returned in metadata hashes dict, otherwise keep previous
            sidecar_sha256 = sha256 if len(sha256) == 64 else ((metadata.hashes or {}).get("SHA256", "").lower() or existing_sha256 or sha256)

            # Deterministically refine baseModel architecture & parameter size from file header
            refined_base = detect_architecture(filepath, metadata.base_model)
            if refined_base:
                metadata.base_model = refined_base

            # Auto-rename model if enabled
            if auto_rename:
                orig_file_name = (metadata.file_metadata or {}).get("name", "")
                _, current_ext = os.path.splitext(filepath)
                target_filename = self.file_ops.generate_model_filename(
                    metadata.name, metadata.version_name, orig_file_name, current_ext, rename_format
                )
                if target_filename != filename:
                    loop = asyncio.get_running_loop()
                    try:
                        new_fp = await loop.run_in_executor(
                            None,
                            self.file_ops.rename_model,
                            filepath,
                            target_filename,
                            True,
                            duplicate_action,
                            sidecar_sha256,
                            self.hash_engine
                        )
                        if new_fp != filepath:
                            try:
                                from scripts.models_info import invalidate_metadata_cache
                                invalidate_metadata_cache(filepath)
                                invalidate_metadata_cache(new_fp)
                            except Exception:
                                pass
                            filepath = new_fp
                            filename = os.path.basename(filepath)
                    except Exception as e:
                        logger.error(f"Auto-rename failed for '{filename}': {e}")

            # Step 5: Download preview image (static format only)
            preview_path = await self._download_preview(
                filepath, metadata.preview_images, api_client=api_client
            )

            display_name = self.file_ops.generate_display_name(metadata.name, metadata.version_name)

            sidecar_data = {
                "schema_version": 1,
                "sha256": sidecar_sha256,
                "source": metadata.source,
                "fetchedAt": self._now_iso(),
                "previewLocalPath": os.path.basename(preview_path) if preview_path else "",
                "model_id": metadata.model_id,
                "version_id": metadata.version_id,
                "versionName": metadata.version_name,
                "name": metadata.name,
                "displayName": display_name,
                "baseModel": metadata.base_model,
                "type": metadata.model_type,
                "tags": metadata.tags,
                "trainedWords": metadata.trained_words,
                "externalUrl": metadata.external_url,
                "description": metadata.description,
                "hashes": metadata.hashes,
            }

            self.metadata_mgr.write(filepath, sidecar_data)

            # Step 7: Auto-sort LoRA by category if enabled and matches predefined tags
            final_filepath = filepath
            if model_type == "LoRA":
                root_dir = get_model_root(filepath)
                loop = asyncio.get_running_loop()
                final_filepath = await loop.run_in_executor(
                    None, self._run_sorting_logic, filepath, model_type, metadata, root_dir
                )
            
            source_tag = metadata.source.capitalize() if metadata.source else "Online"
            base_tag = metadata.base_model or "Unknown"
            async with completed_lock:
                completed_count += 1
                logger.info(f"[{completed_count}/{total}] Fetched: '{filename}' -> {base_tag} ({source_tag})")
                progress_callback(completed_count, total, f"Done: {filename}")

            return ScanResult(final_filepath, os.path.basename(final_filepath), "found",
                               metadata.source, metadata.base_model,
                               f"{metadata.base_model} via {metadata.source}")

        # Run all models concurrently (bounded by semaphore)
        tasks = [process_model(i, fp) for i, fp in enumerate(model_files)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error results
        final_results = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Scanner pipeline error: {r}", exc_info=True)
                final_results.append(ScanResult("", "", "error", "", "", str(r)))
            else:
                final_results.append(r)

        found = sum(1 for r in final_results if r.status == "found")
        cached = sum(1 for r in final_results if r.status == "cached")
        not_found = sum(1 for r in final_results if r.status == "not_found")
        errors = sum(1 for r in final_results if r.status == "error")
        return final_results

    async def _download_preview(self, filepath: str, preview_images: List[dict], api_client: Optional[ApiClient] = None) -> Optional[str]:
        """
        Download first non-animated preview image and save as JPEG.
        """
        if api_client is None:
            api_client = self.api_client

        if not preview_images:
            return None

        try:
            from PIL import Image
        except ImportError:
            logger.warning("PIL (Pillow) is not installed; skipping preview image download.")
            return None

        # Filter out animated formats and NSFW if configured
        nsfw_filter = DEFAULT_NSFW_FILTER
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            raw_filter = shared.opts.data.get(KEY_NSFW_FILTER, DEFAULT_NSFW_FILTER)
            # Extract first word, e.g. "None", "Soft", "Mature", "X"
            nsfw_filter = str(raw_filter).split()[0]

        static_images = []
        for img in preview_images:
            url = img.get("url", "")
            if not url:
                continue

            # 1. Skip if API metadata explicitly marks it as video/animated
            img_type = str(img.get("type", "image")).lower()
            if img_type in ("video", "animated"):
                logger.debug(f"Skipping animated preview (API marked type: {img_type})")
                continue

            # 2. Skip by URL extension check (video, gif, webm, etc.)
            parsed = urlparse(url)
            path_lower = parsed.path.lower()
            animated_exts = ('.gif', '.webm', '.mp4', '.m4v', '.mov', '.avi', '.ogg', '.ogv', '.hevc')
            if path_lower.endswith(animated_exts) or any(ext in path_lower for ext in ('.gif', '.webm', '.mp4')):
                logger.debug("Skipping animated preview by URL extension")
                continue

            # 3. Apply NSFW filter.
            # Arcenciel uses 'safe' as the clean rating. Civitai uses 'None'.
            # Always pass 'safe', empty/missing ratings through.
            # Only filter out explicit content based on user setting.
            nsfw = str(img.get("nsfw", "")).strip().lower()
            # Treat blank, 'none', 'safe' as always-safe regardless of filter level
            is_safe_rating = nsfw in ("", "none", "safe")
            if not is_safe_rating:
                if nsfw_filter == "None":
                    # Strict: block everything that isn't explicitly safe
                    continue
                if nsfw_filter == "Soft" and nsfw in ("mature", "x", "nsfw", "adult", "explicit"):
                    continue
                if nsfw_filter == "Mature" and nsfw in ("x", "explicit", "adult"):
                    continue

            static_images.append(img)

        if not static_images:
            return None

        # Try downloading suitable static images in order until one succeeds
        dest_path = os.path.splitext(filepath)[0] + ".preview.jpg"

        for target_img in static_images:
            url = target_img.get("url", "")
            if not url:
                continue

            try:
                # Pass API key header if URL points to Arcenciel API (media CDN media.arcenciel.io rejects x-api-key with 403)
                headers = {}
                if "arcenciel.io/api" in url:
                    headers = api_client._get_headers(ServiceName.ARCENCIEL)

                await api_client._ensure_session()
                async with api_client.session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.debug(f"Preview candidate returned HTTP {resp.status}, trying next image: {url}")
                        continue

                    # Read body
                    data = await resp.read()
                    
                    # Check Content-Type header to prevent downloading animated formats if mislabeled
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if any(anim_type in content_type for anim_type in ("image/gif", "video/webm", "video/mp4")):
                        logger.debug("Skipping animated preview by Content-Type header")
                        continue

                    # Convert to PIL Image
                    image = Image.open(io.BytesIO(data))
                    
                    # Double check frame count for formats like webp/png/apng which can contain animations
                    if getattr(image, "is_animated", False) or (getattr(image, "n_frames", 1) > 1):
                        logger.debug("Skipping animated multi-frame preview")
                        continue

                    # Resize image if configured (with default fallback width)
                    width = DEFAULT_PREVIEW_WIDTH
                    if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
                        width = shared.opts.data.get(KEY_PREVIEW_WIDTH, DEFAULT_PREVIEW_WIDTH)

                    # Downscale while preserving aspect ratio if not 'original'
                    if width != "original":
                        try:
                            width_int = int(width)
                            image.thumbnail((width_int, width_int), Image.Resampling.LANCZOS)
                        except Exception:
                            pass
                    
                    # Flatten alpha transparency onto white RGB for JPEG compatibility
                    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                        background = Image.new("RGB", image.size, (255, 255, 255))
                        background.paste(image, mask=image.split()[3] if image.mode == "RGBA" else None)
                        image = background
                    elif image.mode != "RGB":
                        image = image.convert("RGB")

                    # Save image as JPEG
                    image.save(dest_path, "JPEG", quality=90)
                    return dest_path

            except Exception as e:
                logger.debug(f"Error trying preview candidate {url}: {e}")
                if os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                continue

        logger.warning(f"Failed to download any preview image for '{filename}'")
        return None
