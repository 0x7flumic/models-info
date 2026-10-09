import os
import logging
import json
import threading
from typing import List, Tuple, Dict, Any, Optional

import gradio as gr
from fastapi import FastAPI, APIRouter, Request, HTTPException, Form, File, UploadFile

# Defensive imports for AUTOMATIC1111 modules
try:
    from modules import script_callbacks, shared, sd_models, paths
except ImportError:
    script_callbacks = None
    shared = None
    sd_models = None
    paths = None

from scripts.models_info_helpers.config import (
    KEY_CIVITAI_API_KEY, KEY_ARCENCIEL_API_KEY, KEY_PRIMARY_SOURCE,
    KEY_PREVIEW_WIDTH, KEY_AUTO_SORT_LORA,
    KEY_NSFW_FILTER, KEY_CONCURRENT_REQUESTS, KEY_ADDITIONAL_PATHS, KEY_ENFORCE_CARD_SIZE, KEY_HASH_ALGO,
    KEY_AUTO_RENAME, KEY_RENAME_FORMAT, KEY_DUPLICATE_ACTION, KEY_RENAME_CACHED,
    DEFAULT_PREVIEW_WIDTH, DEFAULT_CONCURRENT_REQUESTS,
    DEFAULT_PRIMARY_SOURCE, DEFAULT_NSFW_FILTER, DEFAULT_ENFORCE_CARD_SIZE, DEFAULT_HASH_ALGO,
    DEFAULT_AUTO_RENAME, DEFAULT_RENAME_FORMAT, DEFAULT_DUPLICATE_ACTION, DEFAULT_RENAME_CACHED
)
from scripts.models_info_helpers.hash_engine import HashEngine
from scripts.models_info_helpers.api_client import ApiClient, ServiceName
from scripts.models_info_helpers.metadata import MetadataManager, sanitize_base_model
from scripts.models_info_helpers.file_ops import FileOperations
from scripts.models_info_helpers.scanner import ScannerOrchestrator, ScanResult

def sanitize_settings():
    if shared is not None and hasattr(shared, 'opts') and hasattr(shared.opts, 'data'):
        # Sanitize concurrent requests
        val = shared.opts.data.get(KEY_CONCURRENT_REQUESTS)
        if val is not None and not isinstance(val, int):
            try:
                shared.opts.data[KEY_CONCURRENT_REQUESTS] = int(float(val))
            except Exception:
                shared.opts.data[KEY_CONCURRENT_REQUESTS] = DEFAULT_CONCURRENT_REQUESTS

        # Sanitize preview width (could be "original" or numeric string/int)
        width_val = shared.opts.data.get(KEY_PREVIEW_WIDTH)
        if width_val is not None:
            if width_val != "original" and width_val not in ("256", "512", "1024", "2048"):
                try:
                    parsed_w = str(int(float(width_val)))
                    if parsed_w in ("256", "512", "1024", "2048"):
                        shared.opts.data[KEY_PREVIEW_WIDTH] = parsed_w
                    else:
                        shared.opts.data[KEY_PREVIEW_WIDTH] = DEFAULT_PREVIEW_WIDTH
                except Exception:
                    shared.opts.data[KEY_PREVIEW_WIDTH] = DEFAULT_PREVIEW_WIDTH


logger = logging.getLogger("models-info")

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[1;36m",    # Cyan
        logging.INFO: "\033[1;32m",     # Green
        logging.WARNING: "\033[1;33m",  # Yellow
        logging.ERROR: "\033[1;31m",    # Red
        logging.CRITICAL: "\033[1;41m", # Red background
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        header = "\033[1;36m[models-info]\033[0m"
        level = f"{color}{record.levelname}{self.RESET}"
        msg = record.getMessage()
        return f"{header} {level}: {msg}"

if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)
    logger.propagate = False

logger.setLevel(logging.INFO)


# Initialize shared engines
hash_engine = HashEngine()
api_client = ApiClient()
metadata_mgr = MetadataManager()
file_ops = FileOperations()

# Fully isolated scanners for Checkpoints and LoRA
scanners = {
    "Checkpoints": ScannerOrchestrator(hash_engine, api_client, metadata_mgr, file_ops),
    "LoRA": ScannerOrchestrator(hash_engine, api_client, metadata_mgr, file_ops)
}

class ScanState:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_running = False
        self.scanned_count = 0
        self.total_count = 0
        self.progress_message = "Idle"
        self.results: List[ScanResult] = []
        self.log_messages: List[str] = []

# Fully isolated scan states
active_scan_states = {
    "Checkpoints": ScanState(),
    "LoRA": ScanState()
}

def make_progress_callback(model_type: str):
    state = active_scan_states[model_type]
    def cb(current: int, total: int, message: str):
        with state.lock:
            state.scanned_count = current
            state.total_count = total
            state.progress_message = message
            
            log_line = f"[{current}/{total}] {message}"
            if log_line not in state.log_messages:
                state.log_messages.append(log_line)
                if len(state.log_messages) > 100:
                    state.log_messages.pop(0)
    return cb

def make_completion_callback(model_type: str):
    state = active_scan_states[model_type]
    def cb(results: List[ScanResult]):
        with state.lock:
            state.is_running = False
            state.results = results
            
            found = sum(1 for r in results if r.status == "found")
            cached = sum(1 for r in results if r.status == "cached")
            not_found = sum(1 for r in results if r.status == "not_found")
            errors = sum(1 for r in results if r.status == "error")
            
            summary = f"Scan complete for {model_type}: {found} fetched, {cached} cached, {not_found} not found, {errors} errors"
            logger.info(summary)
            state.log_messages.append(summary)
            state.progress_message = "Scan Complete"
    return cb

def resolve_model_directories(model_type: str) -> List[str]:
    """Safely resolve all directory paths based on the selected type, including external and extra folders."""
    dirs = []
    
    # 1. Standard WebUI paths and Forge modern model subfolders
    if paths is not None and hasattr(paths, 'models_path') and paths.models_path:
        if model_type == "Checkpoints":
            dirs.append(os.path.join(paths.models_path, "Stable-diffusion"))
            dirs.append(os.path.join(paths.models_path, "diffusion_models"))
            dirs.append(os.path.join(paths.models_path, "unet"))
        else:
            dirs.append(os.path.join(paths.models_path, "Lora"))
            dirs.append(os.path.join(paths.models_path, "loras"))

    # 2. WebUI Extra Networks registered pages (detects all configured drives/paths across UI)
    try:
        from modules import ui_extra_networks
        for page in getattr(ui_extra_networks, 'extra_pages', []):
            page_name = getattr(page, 'name', '').lower()
            if (model_type == "Checkpoints" and "checkpoint" in page_name) or (model_type == "LoRA" and "lora" in page_name):
                if hasattr(page, 'allowed_directories_for_previews'):
                    page_dirs = page.allowed_directories_for_previews()
                    if isinstance(page_dirs, (list, tuple, set)):
                        dirs.extend(page_dirs)
    except Exception:
        pass
            
    # 3. WebUI Command-line options
    if shared is not None and hasattr(shared, 'cmd_opts') and shared.cmd_opts is not None:
        opts = shared.cmd_opts
        if model_type == "Checkpoints":
            if getattr(opts, 'ckpt_dir', None):
                dirs.append(opts.ckpt_dir)
            if getattr(opts, 'ckpt_dirs', None):
                c_dirs = opts.ckpt_dirs
                if isinstance(c_dirs, (list, tuple)):
                    dirs.extend(c_dirs)
                elif isinstance(c_dirs, str):
                    dirs.append(c_dirs)
            if getattr(opts, 'diffusion_models_dir', None):
                dirs.append(opts.diffusion_models_dir)
            if sd_models is not None and getattr(sd_models, 'model_path', None):
                dirs.append(sd_models.model_path)
        else:
            if getattr(opts, 'lora_dir', None):
                dirs.append(opts.lora_dir)
            if getattr(opts, 'lora_dirs', None):
                l_dirs = opts.lora_dirs
                if isinstance(l_dirs, (list, tuple)):
                    dirs.extend(l_dirs)
                elif isinstance(l_dirs, str):
                    dirs.append(l_dirs)

    # 4. WebUI Settings & models-info custom additional paths (multi-drive support)
    if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
        opts_data = getattr(shared.opts, 'data', {})
        # Native WebUI extra paths
        for key in ('extra_networks_extra_paths', 'lora_extra_paths' if model_type == 'LoRA' else 'ckpt_extra_paths'):
            extra_val = opts_data.get(key)
            if extra_val:
                if isinstance(extra_val, (list, tuple)):
                    dirs.extend(extra_val)
                elif isinstance(extra_val, str):
                    for p in extra_val.replace(',', '\n').split('\n'):
                        if p.strip():
                            dirs.append(p.strip())

        # models-info custom external paths
        custom_paths = opts_data.get(KEY_ADDITIONAL_PATHS, "")
        if custom_paths and isinstance(custom_paths, str):
            for line in custom_paths.replace(',', '\n').split('\n'):
                line = line.strip().strip('"').strip("'")
                if line:
                    dirs.append(line)

    # Normalize, deduplicate while preserving order
    seen = set()
    cleaned = []
    for d in dirs:
        if d and isinstance(d, str):
            try:
                abs_d = os.path.abspath(os.path.realpath(d))
                if abs_d not in seen and os.path.isdir(abs_d):
                    seen.add(abs_d)
                    cleaned.append(abs_d)
            except Exception:
                pass

    if not cleaned:
        default_fallback = os.path.join(paths.models_path if paths else "./models", "Stable-diffusion" if model_type == "Checkpoints" else "Lora")
        cleaned = [os.path.abspath(default_fallback)]

    return cleaned

def handle_scan(model_type: str, scan_missing_only: bool = False):
    """Trigger background scanning under strict functional isolation."""
    scanner = scanners.get(model_type)
    state = active_scan_states.get(model_type)
    if not scanner or not state:
        return "Invalid model type."
        
    if scanner.is_running():
        return "A scan is already in progress for this type."
        
    directories = resolve_model_directories(model_type)
    for directory in directories:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception:
                pass

    state.is_running = True
    state.scanned_count = 0
    state.total_count = 0
    dir_names = ", ".join(directories)
    state.progress_message = f"Scanning {model_type} directories..."
    state.log_messages = [f"Starting scan in {dir_names}..."]
    state.results = []
    
    scanner.start_scan(
        directory=directories,
        model_type=model_type,
        scan_missing_only=scan_missing_only,
        progress_callback=make_progress_callback(model_type),
        completion_callback=make_completion_callback(model_type)
    )
    return "Scan started."

def handle_cancel(model_type: str):
    scanner = scanners.get(model_type)
    state = active_scan_states.get(model_type)
    if not scanner or not state:
        return "Invalid model type."
        
    if not scanner.is_running():
        return "No scan in progress for this type."
        
    scanner.cancel()
    state.is_running = False
    state.progress_message = "Scan Cancelled"
    state.log_messages.append("Scan cancelled by user.")
    return "Scan cancellation requested."

# ----------------- Gradio Callbacks -----------------

def on_ui_settings():
    section = ("models_info", "Models Info")
    sanitize_settings()

    if shared is not None and hasattr(shared, 'opts'):
        shared.opts.add_option(KEY_CIVITAI_API_KEY, shared.OptionInfo(
            "", "Civitai API Key",
            gr.Textbox, {"type": "password"},
            section=section
        ))
        shared.opts.add_option(KEY_ARCENCIEL_API_KEY, shared.OptionInfo(
            "", "Arcenciel API Key",
            gr.Textbox, {"type": "password"},
            section=section
        ))
        shared.opts.add_option(KEY_PRIMARY_SOURCE, shared.OptionInfo(
            DEFAULT_PRIMARY_SOURCE, "Primary Metadata Source",
            gr.Dropdown, {"choices": ["civitai", "arcenciel"]},
            section=section
        ))
        shared.opts.add_option(KEY_PREVIEW_WIDTH, shared.OptionInfo(
            DEFAULT_PREVIEW_WIDTH, "Preview Image Width",
            gr.Dropdown, {"choices": ["256", "512", "1024", "2048", "original"]},
            section=section
        ))
        shared.opts.add_option(KEY_AUTO_SORT_LORA, shared.OptionInfo(
            False, "Auto-sort LoRA into category subfolders",
            section=section
        ))
        shared.opts.add_option(KEY_NSFW_FILTER, shared.OptionInfo(
            DEFAULT_NSFW_FILTER, "Maximum NSFW level for preview images",
            gr.Dropdown, {"choices": [
                "None (Strict: block all mature content)",
                "Soft (Hide explicit/adult content)",
                "Mature (Hide extreme/X-rated content only)",
                "X (Allow all content)"
            ]},
            section=section
        ))
        shared.opts.add_option(KEY_CONCURRENT_REQUESTS, shared.OptionInfo(
            DEFAULT_CONCURRENT_REQUESTS, "Max concurrent API requests",
            gr.Slider, {"minimum": 1, "maximum": 10, "step": 1},
            section=section
        ))
        shared.opts.add_option(KEY_ENFORCE_CARD_SIZE, shared.OptionInfo(
            DEFAULT_ENFORCE_CARD_SIZE, "Enforce Custom Card Dimensions (280x420)",
            section=section
        ))
        shared.opts.add_option(KEY_HASH_ALGO, shared.OptionInfo(
            DEFAULT_HASH_ALGO, "Hash Algorithm",
            gr.Dropdown, {"choices": [
                "SHA256 (Full file, most accurate)",
                "AutoV1 / Quick Hash (Instant, Civitai only)"
            ]},
            section=section
        ))
        shared.opts.add_option(KEY_AUTO_RENAME, shared.OptionInfo(
            DEFAULT_AUTO_RENAME, "Auto-rename model files to Civitai/Arcenciel names",
            section=section
        ))
        shared.opts.add_option(KEY_RENAME_FORMAT, shared.OptionInfo(
            DEFAULT_RENAME_FORMAT, "Rename Format",
            gr.Dropdown, {"choices": [
                "Model_Version",
                "Model - Version",
                "Original Civitai Filename",
                "Model Only"
            ]},
            section=section
        ))
        shared.opts.add_option(KEY_DUPLICATE_ACTION, shared.OptionInfo(
            DEFAULT_DUPLICATE_ACTION, "Duplicate Handling Policy",
            gr.Dropdown, {"choices": [
                "Auto-number (e.g. model - Duplicate 1.safetensors)",
                "Skip renaming on conflict"
            ]},
            section=section
        ))
        shared.opts.add_option(KEY_RENAME_CACHED, shared.OptionInfo(
            DEFAULT_RENAME_CACHED, "Also rename already-cached models on rescan",
            section=section
        ))

# ----------------- FastAPI Integration -----------------

def is_safe_filepath(filepath: str) -> bool:
    """Validate that the requested path resides strictly inside authorized model folders."""
    if not filepath:
        return False
    try:
        abs_path = os.path.abspath(os.path.realpath(filepath))
        all_allowed = []
        for m_type in ["Checkpoints", "LoRA"]:
            all_allowed.extend(resolve_model_directories(m_type))
        for allowed_dir in all_allowed:
            abs_allowed = os.path.abspath(os.path.realpath(allowed_dir))
            if abs_path.startswith(abs_allowed + os.sep) or abs_path == abs_allowed:
                return True
    except Exception:
        pass
    return False

# In-memory sidecar metadata cache: filepath -> (mtime, sidecar_data)
_metadata_cache: Dict[str, Tuple[float, dict]] = {}
_metadata_cache_lock = threading.Lock()

def invalidate_metadata_cache(filepath: Optional[str] = None):
    with _metadata_cache_lock:
        if filepath:
            _metadata_cache.pop(filepath, None)
            _metadata_cache.pop(os.path.abspath(filepath), None)
            _metadata_cache.pop(filepath.replace("\\", "/"), None)
        else:
            _metadata_cache.clear()

def verify_same_origin(request: Request):
    """Validate request Origin and Referer headers to block unauthorized cross-site (CSRF) calls."""
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host")

    from urllib.parse import urlparse
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host and host and origin_host.lower() != host.lower():
            raise HTTPException(status_code=403, detail="Cross-origin request blocked")
    elif referer:
        referer_host = urlparse(referer).netloc
        if referer_host and host and referer_host.lower() != host.lower():
            raise HTTPException(status_code=403, detail="Cross-origin request blocked")

def register_api(demo: Optional[Any], app: FastAPI):
    router = APIRouter(prefix="/models-info/api", tags=["models-info"])

    @router.get("/progress/{model_type}")
    async def get_progress(model_type: str):
        if model_type not in active_scan_states:
            raise HTTPException(status_code=400, detail="Invalid model type. Must be Checkpoints or LoRA")
            
        state = active_scan_states[model_type]
        with state.lock:
            return {
                "is_running": state.is_running,
                "scanned": state.scanned_count,
                "total": state.total_count,
                "percent": int((state.scanned_count / state.total_count * 100) if state.total_count > 0 else 0),
                "message": state.progress_message,
                "logs": list(state.log_messages),
                "results": [
                    {
                        "filepath": r.filepath,
                        "filename": r.filename,
                        "status": r.status,
                        "source": r.source,
                        "base_model": r.base_model,
                        "message": r.message
                    }
                    for r in state.results
                ]
            }

    @router.post("/scan")
    async def api_scan(request: Request):
        verify_same_origin(request)
        try:
            body = await request.json()
        except:
            body = {}
            
        model_type = body.get("model_type", "Checkpoints")
        if model_type not in active_scan_states:
            raise HTTPException(status_code=400, detail="Invalid model type. Must be Checkpoints or LoRA")
            
        scan_missing_only = body.get("scan_missing_only", False)
        status = handle_scan(model_type, scan_missing_only=scan_missing_only)
        return {"status": "started", "detail": status}

    @router.post("/cancel")
    async def api_cancel(request: Request):
        verify_same_origin(request)
        try:
            body = await request.json()
        except:
            body = {}
        model_type = body.get("model_type", "Checkpoints")
        if model_type not in active_scan_states:
            raise HTTPException(status_code=400, detail="Invalid model type. Must be Checkpoints or LoRA")
            
        status = handle_cancel(model_type)
        return {"status": "cancelled", "detail": status}

    @router.post("/rename")
    async def api_rename(request: Request):
        verify_same_origin(request)
        try:
            body = await request.json()
        except:
            body = {}
        filepath = body.get("filepath")
        new_name = body.get("new_name")
        if not filepath or not new_name:
            raise HTTPException(status_code=400, detail="Missing filepath or new_name")
            
        if not is_safe_filepath(filepath):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal attempt blocked")
            
        try:
            new_filepath = file_ops.rename_model(filepath, new_name)
            invalidate_metadata_cache(filepath)
            invalidate_metadata_cache(new_filepath)
            return {"status": "success", "new_filepath": new_filepath}
        except Exception as e:
            logger.error(f"API rename failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/delete")
    async def api_delete(request: Request):
        verify_same_origin(request)
        try:
            body = await request.json()
        except:
            body = {}
        filepath = body.get("filepath")
        if not filepath:
            raise HTTPException(status_code=400, detail="Missing filepath")
            
        if not is_safe_filepath(filepath):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal attempt blocked")
            
        try:
            file_ops.delete_model(filepath)
            invalidate_metadata_cache(filepath)
            return {"status": "success"}
        except Exception as e:
            logger.error(f"API delete failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/toggle-favorite")
    async def api_toggle_favorite(request: Request):
        verify_same_origin(request)
        from datetime import datetime, timezone
        try:
            body = await request.json()
        except Exception:
            body = {}
        filepath = body.get("filepath")
        if not filepath:
            raise HTTPException(status_code=400, detail="Missing filepath")

        if not is_safe_filepath(filepath):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal attempt blocked")

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Model file not found")

        data = metadata_mgr.read(filepath) or {
            "schema_version": 1,
            "sha256": "",
            "source": "custom",
            "fetchedAt": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        }

        current_fav = bool(data.get("favorite", False))
        new_fav = not current_fav
        data["favorite"] = new_fav

        try:
            metadata_mgr.write(filepath, data)
            invalidate_metadata_cache(filepath)
            return {"status": "success", "favorite": new_fav}
        except Exception as e:
            logger.error(f"Failed to toggle favorite for {filepath}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _collect_all_metadata():
        meta_by_path = {}
        meta_by_filename = {}
        extensions = ('.safetensors', '.ckpt', '.pt', '.bin')

        all_dirs = []
        for m_type in ["Checkpoints", "LoRA"]:
            all_dirs.extend(resolve_model_directories(m_type))

        with _metadata_cache_lock:
            for directory in all_dirs:
                if not os.path.isdir(directory):
                    continue
                for root, _, files in os.walk(directory):
                    for f in files:
                        if f.lower().endswith(extensions):
                            model_path = os.path.join(root, f)
                            sidecar_path = metadata_mgr.get_sidecar_path(model_path)
                            sidecar_data = None
                            if os.path.exists(sidecar_path):
                                try:
                                    mtime = os.path.getmtime(sidecar_path)
                                    cached_entry = _metadata_cache.get(model_path)
                                    if cached_entry and cached_entry[0] == mtime:
                                        sidecar_data = cached_entry[1]
                                    else:
                                        sidecar_data = metadata_mgr.read(model_path)
                                        if sidecar_data:
                                            _metadata_cache[model_path] = (mtime, sidecar_data)
                                except Exception:
                                    pass

                            if sidecar_data:
                                if "displayName" not in sidecar_data or not sidecar_data["displayName"]:
                                    m_name = sidecar_data.get("name", "")
                                    v_name = sidecar_data.get("versionName", "")
                                    if m_name or v_name:
                                        from scripts.models_info_helpers.file_ops import generate_display_name
                                        sidecar_data["displayName"] = generate_display_name(m_name, v_name)

                                normalized_path = model_path.replace("\\", "/")
                                stem, _ = os.path.splitext(f)

                                # Absolute paths
                                meta_by_path[normalized_path] = sidecar_data
                                meta_by_path[model_path] = sidecar_data

                                # Filename mappings (with ext, without ext, lowercase)
                                meta_by_filename[f] = sidecar_data
                                meta_by_filename[stem] = sidecar_data
                                meta_by_filename[f.lower()] = sidecar_data
                                meta_by_filename[stem.lower()] = sidecar_data

                                # Relative path mappings (e.g. "Anime/my_lora.safetensors")
                                try:
                                    rel_path = os.path.relpath(model_path, directory).replace("\\", "/")
                                    rel_stem, _ = os.path.splitext(rel_path)
                                    meta_by_path[rel_path] = sidecar_data
                                    meta_by_path[rel_stem] = sidecar_data
                                    meta_by_filename[rel_path] = sidecar_data
                                    meta_by_filename[rel_stem] = sidecar_data
                                    meta_by_filename[rel_path.lower()] = sidecar_data
                                    meta_by_filename[rel_stem.lower()] = sidecar_data
                                except Exception:
                                    pass

        enforce_card_size = DEFAULT_ENFORCE_CARD_SIZE
        if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
            enforce_card_size = shared.opts.data.get(KEY_ENFORCE_CARD_SIZE, DEFAULT_ENFORCE_CARD_SIZE)

        return {
            "by_path": meta_by_path,
            "by_filename": meta_by_filename,
            "settings": {
                "enforce_card_size": bool(enforce_card_size)
            }
        }

    @router.get("/metadata-all")
    async def api_metadata_all():
        """Scans both Checkpoints and LoRA directories and loads all JSON metadata asynchronously with mtime caching."""
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(_collect_all_metadata)

    @router.get("/base-models")
    async def api_base_models():
        """Retrieve the configured list of base models for manual metadata selection."""
        base_models = [
            "Anima (2B)", "Anima (2.9B)", "Anima (3.8B)", "Anima", "AuraFlow", "Chroma", "Ernie", "Flux.1 S", "Flux.1 D", "Flux.1 Krea", "Flux.1 Kontext",
            "Flux.2 D", "Flux.2 Klein 9B", "Flux.2 Klein 9B-base", "Flux.2 Klein 4B", "Flux.2 Klein 4B-base",
            "Grok", "HappyHorse", "HiDream", "HiDream-O1", "Illustrious", "Imagen4", "Kolors", "Lumina",
            "Nano Banana", "NoobAI", "ODOR", "OpenAI", "Other", "PixArt a", "PixArt E", "Playground v2",
            "Pony", "Pony V7", "Qwen", "Qwen 2", "Stable Cascade", "SD 1.4", "SD 1.5", "SD 1.5 LCM",
            "SD 1.5 Hyper", "SD 2.0", "SD 2.0 768", "SD 2.1", "SD 2.1 768", "SD 2.1 Unclip", "SD 3",
            "SD 3.5", "SD 3.5 Large", "SD 3.5 Large Turbo", "SD 3.5 Medium", "SDXL 0.9", "SDXL 1.0",
            "SDXL 1.0 LCM", "SDXL Lightning", "SDXL Hyper", "SDXL Turbo", "SDXL Distilled", "Seedream",
            "Wan Image 2.7", "ZImageTurbo", "ZImageBase",
            "CogVideoX", "Hunyuan 1", "Hunyuan Video", "LTXV", "LTXV2", "LTXV 2.3", "Mochi", "SVD",
            "SVD XT", "Sora 2", "Veo 3", "Wan Video", "Wan Video 1.3B t2v", "Wan Video 14B t2v",
            "Wan Video 14B i2v 480p", "Wan Video 14B i2v 720p", "Wan Video 2.2 TI2V-5B",
            "Wan Video 2.2 I2V-A14B", "Wan Video 2.2 T2V-A14B", "Wan Video 2.5 T2V", "Wan Video 2.5 I2V",
            "Wan Video 2.7", "Vidu Q1", "Hailuo by MiniMax", "Kling", "Seedance"
        ]
        return {"base_models": base_models}

    @router.get("/model-metadata")
    async def api_model_metadata(filepath: str):
        if not filepath:
            raise HTTPException(status_code=400, detail="Missing filepath")
        if not is_safe_filepath(filepath):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal attempt blocked")
        data = metadata_mgr.read(filepath) or {}
        if "displayName" not in data or not data["displayName"]:
            m_name = data.get("name", "")
            v_name = data.get("versionName", "")
            if m_name or v_name:
                from scripts.models_info_helpers.file_ops import generate_display_name
                data["displayName"] = generate_display_name(m_name, v_name)
        return data

    @router.post("/edit-metadata")
    def api_edit_metadata(
        request: Request,
        filepath: str = Form(...),
        base_model: str = Form(...),
        version_name: str = Form(...),
        display_name: Optional[str] = Form(None),
        preview_local_path: Optional[str] = Form(None),
        preview_file: Optional[UploadFile] = File(None),
        trained_words: Optional[str] = Form(None),
        external_url: Optional[str] = Form(None),
        favorite: Optional[bool] = Form(None)
    ):
        verify_same_origin(request)
        from datetime import datetime, timezone
        if not filepath:
            raise HTTPException(status_code=400, detail="Missing filepath")

        if not is_safe_filepath(filepath):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal attempt blocked")

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail=f"Model file not found: {filepath}")

        # 1. Update/Create Sidecar JSON
        sidecar_path = metadata_mgr.get_sidecar_path(filepath)
        raw_data = {}
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
            except:
                pass

        words = [w.strip() for w in trained_words.split(",") if w.strip()] if trained_words else []

        # Set values directly at the root of the flat dictionary (Schema Version 1)
        raw_data["schema_version"] = 1
        raw_data["baseModel"] = sanitize_base_model(base_model)
        raw_data["versionName"] = version_name
        
        if display_name is not None:
            clean_display_name = display_name.strip()
            if clean_display_name:
                raw_data["displayName"] = clean_display_name
            else:
                raw_data.pop("displayName", None)

        if trained_words is not None:
            raw_data["trainedWords"] = words
        if external_url is not None:
            raw_data["externalUrl"] = external_url
        if favorite is not None:
            raw_data["favorite"] = bool(favorite)
        if "name" not in raw_data:
            raw_data["name"] = os.path.splitext(os.path.basename(filepath))[0]
        if "source" not in raw_data:
            raw_data["source"] = "custom"
        if "fetchedAt" not in raw_data:
            raw_data["fetchedAt"] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        # Always synchronize previewLocalPath with the actual preview file on disk if it exists
        base_no_ext, _ = os.path.splitext(filepath)
        for ext_val in (".preview.jpg", ".preview.png", ".preview.webp", ".png", ".jpg", ".jpeg", ".webp"):
            if os.path.exists(base_no_ext + ext_val):
                raw_data["previewLocalPath"] = os.path.basename(base_no_ext + ext_val)
                break

        # 2. Local Preview Image Copying & Downscaling
        preview_image_source = None
        preview_is_file = False

        if preview_file and preview_file.filename:
            preview_image_source = preview_file
            preview_is_file = True
        elif preview_local_path:
            clean_preview_path = str(preview_local_path).strip().strip('"').strip("'")
            if clean_preview_path:
                ext = os.path.splitext(clean_preview_path)[1].lower()
                if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
                    raise HTTPException(status_code=400, detail="Invalid image file extension for preview.")

                abs_preview = os.path.abspath(os.path.realpath(clean_preview_path))
                import tempfile
                temp_dir = os.path.abspath(os.path.realpath(tempfile.gettempdir()))
                is_safe = is_safe_filepath(abs_preview) or abs_preview.startswith(temp_dir + os.sep) or abs_preview == temp_dir
                if not is_safe:
                    raise HTTPException(status_code=403, detail="Access denied: Preview file outside authorized directories")

                if os.path.exists(abs_preview):
                    preview_image_source = abs_preview
                else:
                    raise HTTPException(status_code=400, detail=f"Local preview image not found: {clean_preview_path}")

        if preview_image_source:
            try:
                from PIL import Image
                import io
                dest_path = os.path.splitext(filepath)[0] + ".preview.jpg"
                
                if preview_is_file:
                    contents = preview_image_source.file.read()
                    image = Image.open(io.BytesIO(contents))
                else:
                    image = Image.open(preview_image_source)
                
                width = "512"
                if shared is not None and hasattr(shared, 'opts') and shared.opts is not None:
                    width = shared.opts.data.get("models_info_preview_width", "512")
                
                if width != "original":
                    try:
                        width_int = int(width)
                        image.thumbnail((width_int, width_int), Image.Resampling.LANCZOS)
                    except:
                        pass
                
                # Flatten alpha transparency onto white RGB for JPEG compatibility
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    background.paste(image, mask=image.split()[3] if image.mode == "RGBA" else None)
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")

                image.save(dest_path, "JPEG", quality=90)
                preview_basename = os.path.basename(dest_path)
                raw_data["previewLocalPath"] = preview_basename
            except Exception as e:
                logger.error(f"Failed to copy preview image: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to process preview image: {e}")

        try:
            metadata_mgr.write(filepath, raw_data)
            invalidate_metadata_cache(filepath)
        except Exception as e:
            logger.error(f"Failed to save edited metadata: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save metadata: {e}")

        return {"status": "success"}

    app.include_router(router)

# ----------------- Extension Registration -----------------

if script_callbacks is not None:
    script_callbacks.on_ui_settings(on_ui_settings)
    script_callbacks.on_app_started(register_api)
