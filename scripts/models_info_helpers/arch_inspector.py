import struct
import json
import os
import re
import logging
from typing import Optional, Tuple, Set

logger = logging.getLogger("models-info")

def read_safetensors_header(filepath: str) -> Tuple[dict, dict]:
    """
    Read only the length prefix and JSON header of a .safetensors file.
    Never loads or reads weight tensors into memory (<2ms execution).
    Returns (tensor_headers, metadata_dict).
    """
    if not os.path.isfile(filepath):
        return {}, {}
    try:
        with open(filepath, "rb") as f:
            length_bytes = f.read(8)
            if len(length_bytes) < 8:
                return {}, {}
            header_size = struct.unpack("<Q", length_bytes)[0]
            if header_size <= 0 or header_size > 100 * 1024 * 1024:
                return {}, {}
            header_json = f.read(header_size).decode("utf-8")
            data = json.loads(header_json)
            meta = data.get("__metadata__", {})
            tensors = {k: v for k, v in data.items() if k != "__metadata__"}
            return tensors, meta
    except Exception as e:
        logger.debug(f"Failed to read safetensors header from '{filepath}': {e}")
        return {}, {}

def extract_block_indices(tensor_keys) -> Set[int]:
    """Extract numeric block indices from tensor keys for DiT/UNet structures."""
    indices = set()
    for k in tensor_keys:
        parts = k.replace("_", ".").split(".")
        for i, part in enumerate(parts):
            if part in ("blocks", "layers") and i + 1 < len(parts) and parts[i + 1].isdigit():
                indices.add(int(parts[i + 1]))
    return indices

def detect_architecture(filepath: str, reported_base: Optional[str] = None) -> Optional[str]:
    """
    Deterministically identify the exact architecture and parameter size from the .safetensors header.
    Refines ambiguous base model tags (like 'Anima' -> 'Anima (2B)' / 'Anima (2.9B)' / 'Anima (3.8B)').
    """
    if not filepath.lower().endswith(".safetensors"):
        return reported_base

    tensors, meta = read_safetensors_header(filepath)
    if not tensors and not meta:
        return reported_base

    reported_clean = (reported_base or "").strip()
    reported_lower = reported_clean.lower()

    ss_base = str(meta.get("ss_base_model_version", "")).lower()
    modelspec_arch = str(meta.get("modelspec.architecture", "")).lower()
    ss_sd_model_name = str(meta.get("ss_sd_model_name", "")).lower()

    block_indices = extract_block_indices(tensors.keys())
    block_count = len(block_indices)
    max_block = max(block_indices) if block_indices else -1

    has_double = any("double_blocks" in k or "double.blocks" in k.replace("_", ".") for k in tensors.keys())
    has_single = any("single_blocks" in k or "single.blocks" in k.replace("_", ".") for k in tensors.keys())

    # ------------------ Anima Family (2B vs 2.9B vs 3.8B) ------------------
    is_anima_hint = (
        "anima" in reported_lower
        or "anima" in ss_base
        or "anima" in modelspec_arch
        or "anima" in ss_sd_model_name
        or "anima_main_dit_blocks" in meta
    )

    if is_anima_hint:
        dit_blocks_meta = meta.get("anima_main_dit_blocks") or meta.get("main_dit_blocks")
        if dit_blocks_meta:
            try:
                meta_blocks = int(dit_blocks_meta)
                if meta_blocks == 28:
                    return "Anima (2B)"
                elif meta_blocks == 40:
                    return "Anima (2.9B)"
                elif meta_blocks == 52:
                    return "Anima (3.8B)"
            except (ValueError, TypeError):
                pass

        if block_count == 28 or max_block == 27:
            return "Anima (2B)"
        elif block_count == 40 or max_block == 39:
            return "Anima (2.9B)"
        elif block_count == 52 or max_block == 51:
            return "Anima (3.8B)"
        elif "2.9b" in filepath.lower() or "29b" in filepath.lower():
            return "Anima (2.9B)"
        elif "delta" in filepath.lower() or "3.8b" in filepath.lower() or "38b" in filepath.lower():
            return "Anima (3.8B)"
        else:
            return "Anima (2B)"

    # ------------------ Flux.1 & Flux.2 Family ------------------
    if "flux" in reported_lower or "flux" in modelspec_arch or has_double or has_single:
        if "klein" in reported_lower or "klein" in filepath.lower():
            if "4b" in reported_lower or "4b" in filepath.lower():
                return "Flux.2 Klein 4B"
            return "Flux.2 Klein 9B"
        if "flux.1" in reported_lower or "flux" in reported_lower:
            return reported_clean if reported_clean else "Flux.1 D"

    # ------------------ SD 3 / SD 3.5 Family ------------------
    if "sd 3" in reported_lower or "sd3" in reported_lower or "sd3" in modelspec_arch:
        if block_count == 24 or max_block == 23 or "medium" in reported_lower:
            return "SD 3.5 Medium" if "3.5" in reported_lower else "SD 3"
        elif block_count == 38 or max_block == 37 or "large" in reported_lower:
            return "SD 3.5 Large"
        return reported_clean or "SD 3.5 Large"

    # ------------------ Wan Video Family ------------------
    if "wan" in reported_lower or "wan" in filepath.lower():
        if "1.3b" in reported_lower or "1.3b" in filepath.lower():
            return "Wan Video 1.3B t2v"
        elif "14b" in reported_lower or "14b" in filepath.lower():
            return "Wan Video 14B t2v"
        return reported_clean or "Wan Video"

    # ------------------ Hunyuan Video ------------------
    if "hunyuan" in reported_lower or "hunyuan" in filepath.lower():
        return "Hunyuan Video"

    # ------------------ SDXL / Illustrious / Pony / NoobAI ------------------
    if any(sub in reported_lower for sub in ("illustrious", "pony", "noobai", "sdxl")):
        return reported_clean

    return reported_clean if reported_clean else "Unknown"
