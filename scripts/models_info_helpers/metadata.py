import os
import json
import logging
import tempfile
from typing import Optional

logger = logging.getLogger("models-info")

def sanitize_base_model(base_model: Optional[str]) -> str:
    """Consolidated sanitization utility for model architecture names."""
    if not base_model:
        return "Unknown"
    base_model_str = str(base_model).strip()
    base_lower = base_model_str.lower()
    if "noobai" in base_lower:
        return "NoobAI"
    if "anima" in base_lower:
        if "3.8b" in base_lower or "38b" in base_lower or "delta" in base_lower:
            return "Anima (3.8B)"
        elif "2.9b" in base_lower or "29b" in base_lower:
            return "Anima (2.9B)"
        elif "2b" in base_lower or "(2b)" in base_lower:
            return "Anima (2B)"
    return base_model_str

class MetadataManager:
    SIDECAR_SUFFIX = ".metadata"
    SCHEMA_VERSION = 1

    def get_sidecar_path(self, model_path: str) -> str:
        base_no_ext, _ = os.path.splitext(model_path)
        return base_no_ext + self.SIDECAR_SUFFIX

    def has_metadata(self, model_path: str) -> bool:
        sidecar_path = self.get_sidecar_path(model_path)
        return os.path.exists(sidecar_path)

    def read(self, model_path: str) -> Optional[dict]:
        """Read flat metadata sidecar directly without legacy fallbacks."""
        sidecar_path = self.get_sidecar_path(model_path)
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data["schema_version"] = self.SCHEMA_VERSION
                        if "baseModel" in data:
                            data["baseModel"] = sanitize_base_model(data["baseModel"])
                        return data
            except Exception as e:
                logger.error(f"Error reading sidecar {os.path.basename(sidecar_path)}: {e}")
        return None

    def write(self, model_path: str, data: dict) -> None:
        """Write metadata sidecar atomically (write-to-temp + rename)."""
        sidecar_path = self.get_sidecar_path(model_path)
        
        dir_name = os.path.dirname(sidecar_path)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            
        fd, temp_path = tempfile.mkstemp(dir=dir_name or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(temp_path, sidecar_path)
        except Exception as e:
            logger.error(f"Error writing sidecar {os.path.basename(sidecar_path)}: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise e
