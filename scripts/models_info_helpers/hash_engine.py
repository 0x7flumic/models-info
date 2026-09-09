import hashlib
import os
import json
import tempfile
import logging
from typing import Optional

logger = logging.getLogger("models-info")

class HashEngine:
    CHUNK_SIZE = 1024 * 1024 * 8  # 8MB chunks for progress reporting

    def get_quick_hash(self, filepath: str) -> Optional[str]:
        """
        Calculate fast AutoV1 hash (reads 64KB slice at 1MB offset).
        Executes in <1ms and matches Civitai's legacy model index.
        """
        try:
            if not os.path.isfile(filepath):
                return None
            with open(filepath, "rb") as f:
                m = hashlib.sha256()
                f.seek(0x100000)
                chunk = f.read(0x10000)
                if not chunk:
                    return None
                m.update(chunk)
                return m.hexdigest()[:8].lower()
        except Exception as e:
            logger.debug(f"Quick hash failed for {filepath}: {e}")
            return None

    def get_hash(self, filepath: str, progress_callback=None, algo: str = "sha256") -> str:
        """
        Return cached or computed SHA256 hash for a model file.
        Uses WebUI's native optimized hashes cache if available.
        """
        if algo == "autov1":
            q_hash = self.get_quick_hash(filepath)
            if q_hash:
                return q_hash

        cached = self._check_cache(filepath)
        if cached:
            return cached

        # Try WebUI native cache first
        try:
            from modules import hashes
            val = hashes.sha256_from_cache(filepath, filepath)
            if val:
                val_lower = val.lower()
                self._save_cache(filepath, val_lower)
                return val_lower
        except Exception:
            pass

        # Fallback to computing SHA256 from file
        hash_hex = self._calculate_sha256(filepath, progress_callback)
        self._save_cache(filepath, hash_hex)

        # Feed hash back into WebUI cache and flush
        try:
            from modules import hashes
            db_cache = hashes.cache("hashes")
            db_cache[filepath] = {
                "mtime": os.path.getmtime(filepath),
                "sha256": hash_hex,
            }
            if hasattr(hashes, 'dump_cache'):
                hashes.dump_cache()
        except Exception:
            pass

        return hash_hex

    def _calculate_sha256(self, filepath: str, progress_callback=None) -> str:
        """Calculate full file SHA256 hash with progress."""
        file_size = os.path.getsize(filepath)
        sha256 = hashlib.sha256()
        bytes_read = 0

        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
                bytes_read += len(chunk)
                if progress_callback:
                    try:
                        progress_callback(bytes_read, file_size)
                    except Exception as e:
                        logger.warning(f"SHA256 progress callback failed: {e}")

        return sha256.hexdigest().lower()

    def _check_cache(self, filepath: str) -> Optional[str]:
        """Check metadata sidecar for existing SHA256 hash with mtime validation."""
        base_no_ext, _ = os.path.splitext(filepath)
        metadata_path = base_no_ext + ".metadata"
        if os.path.exists(metadata_path) and os.path.exists(filepath):
            try:
                # Invalidate cache if model file has been modified after metadata was written
                if os.path.getmtime(filepath) > os.path.getmtime(metadata_path):
                    return None

                with open(metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    val = (data.get("sha256") or "").strip().lower()
                    if len(val) == 64:
                        return val
            except Exception:
                pass

        return None

    def _save_cache(self, filepath: str, hash_hex: str) -> None:
        """Persist SHA256 hash to sidecar file atomically."""
        base_no_ext, _ = os.path.splitext(filepath)
        metadata_path = base_no_ext + ".metadata"
        # Read existing metadata if present to avoid clobbering other fields
        data = {}
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                pass

        data.setdefault("schema_version", 1)
        data["sha256"] = hash_hex

        # Write to JSON sidecar file atomically
        dir_name = os.path.dirname(metadata_path)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        try:
            fd, temp_path = tempfile.mkstemp(dir=dir_name or ".", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(temp_path, metadata_path)
        except Exception as e:
            logger.warning(f"Failed to save SHA256 to metadata sidecar for {os.path.basename(filepath)}: {e}")
