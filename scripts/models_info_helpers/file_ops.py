import os
import shutil
import logging
import re
from typing import List, Tuple, Optional

logger = logging.getLogger("models-info")

def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    Sanitize filename component:
    1. Normalize unicode (e.g. é -> e).
    2. Strip non-English characters, emojis, and symbols (keeps ASCII letters, digits, spaces, dots, underscores, hyphens).
    3. Capitalize the first letter of each word.
    4. Guard against Windows reserved device names and length boundaries.
    """
    if not name:
        return "unnamed_model"

    # Known file extensions to preserve as lowercase
    KNOWN_EXTENSIONS = (
        '.safetensors', '.ckpt', '.pt', '.bin',
        '.metadata', '.json', '.txt', '.png', '.jpg', '.jpeg', '.webp', '.sha256'
    )
    base_stem, ext = os.path.splitext(name)
    has_known_ext = ext.lower() in KNOWN_EXTENSIONS
    if has_known_ext:
        process_name = base_stem
    else:
        process_name = name
        ext = ""

    import unicodedata
    decomposed = unicodedata.normalize('NFKD', str(process_name))
    ascii_text = decomposed.encode('ascii', 'ignore').decode('ascii')

    # Remove special characters, allowing alphanumeric, space, dot, hyphen, underscore
    clean = re.sub(r'[^a-zA-Z0-9\s._-]', ' ', ascii_text)

    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean).strip(' ._-')
    clean = re.sub(r'-{2,}', '-', clean)

    # Capitalize the first letter of each word (after start of string, space, hyphen, underscore, dot)
    clean = re.sub(r'(^|[\s._-])([a-z])', lambda m: m.group(1) + m.group(2).upper(), clean)

    if len(clean) > max_length:
        clean = clean[:max_length].rstrip(' ._-')

    # Guard against Windows reserved device names: CON, PRN, AUX, NUL, COM1-9, LPT1-9
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10))
    }
    stem_check = clean.split('.')[0].upper()
    if stem_check in reserved_names:
        clean = f"Model_{clean}"

    final_name = clean or "unnamed_model"
    if has_known_ext:
        final_name += ext.lower()

    return final_name


def generate_model_filename(
    model_name: str,
    version_name: str,
    original_filename: str = "",
    current_ext: str = ".safetensors",
    format_choice: str = "Model_Version"
) -> str:
    """
    Generate sanitized model filename.
    Primary format: {sanitized model's name}_{model's verion}.safetensors
    """
    model_name = (model_name or "").strip()
    version_name = (version_name or "").strip()
    original_filename = (original_filename or "").strip()

    if not current_ext:
        current_ext = ".safetensors"
    elif not current_ext.startswith('.'):
        current_ext = '.' + current_ext

    fmt = (format_choice or "Model_Version").lower()

    if "original" in fmt and original_filename:
        stem, _ = os.path.splitext(original_filename)
        clean_stem = sanitize_filename(stem)
        return (clean_stem or "Model") + current_ext
    elif "model only" in fmt:
        clean_model = sanitize_filename(model_name or version_name or "Model")
        return clean_model + current_ext
    elif "model - version" in fmt:
        clean_model = sanitize_filename(model_name)
        clean_version = sanitize_filename(version_name)
        if clean_version and clean_version.lower() != clean_model.lower():
            if clean_model.lower().endswith(clean_version.lower()):
                clean_model = clean_model[:-len(clean_version)].rstrip(' _-')
            target_stem = f"{clean_model} - {clean_version}" if clean_model else clean_version
        else:
            target_stem = clean_model or clean_version or "Model"
        return target_stem + current_ext
    else:
        # Default and standard: {sanitized model's name}_{model's verion}
        clean_model = sanitize_filename(model_name)
        clean_version = sanitize_filename(version_name)
        if clean_version and clean_version.lower() != clean_model.lower():
            if clean_model.lower().endswith(clean_version.lower()):
                clean_model = clean_model[:-len(clean_version)].rstrip(' _-')
            target_stem = f"{clean_model}_{clean_version}" if clean_model else clean_version
        else:
            target_stem = clean_model or clean_version or "Model"
        return target_stem + current_ext


def generate_display_name(model_name: str, version_name: str = "") -> str:
    """
    Generate sanitized "Model Only" display name (e.g. 'Cyber Realistic').
    Strips trailing version if present in model_name.
    """
    clean_model = sanitize_filename(model_name or "")
    clean_version = sanitize_filename(version_name or "")
    if clean_version and clean_version.lower() != clean_model.lower():
        if clean_model.lower().endswith(clean_version.lower()):
            clean_model = clean_model[:-len(clean_version)].rstrip(' _-')
    return clean_model or clean_version or "Model"


def resolve_duplicate_filename(
    target_filepath: str,
    source_filepath: str,
    duplicate_action: str = "auto_number",
    source_hash: Optional[str] = None,
    hash_engine = None
) -> Tuple[str, bool]:
    """
    Check if target_filepath collides with an existing file.
    Returns (resolved_filepath, is_duplicate).
    On duplicates generates: {sanitized model's name}_{model's verion} - Duplicate {number of duplicate}.safetensors
    """
    norm_target = os.path.normcase(os.path.abspath(target_filepath))
    norm_source = os.path.normcase(os.path.abspath(source_filepath))

    # Exact same file (or Windows case-only rename)
    if norm_target == norm_source:
        return target_filepath, False

    # Target path is currently free
    if not os.path.exists(target_filepath):
        return target_filepath, False

    # Target file exists -> collision
    target_name = os.path.basename(target_filepath)
    source_name = os.path.basename(source_filepath)

    # Optional duplicate binary check via hash
    if source_hash and hash_engine:
        try:
            target_hash = hash_engine.get_hash(target_filepath, algo="sha256")
            if target_hash and target_hash.lower() == source_hash.lower():
                logger.warning(
                    f"[models-info] Binary duplicate detected: '{source_name}' has identical hash to '{target_name}'"
                )
        except Exception as e:
            logger.debug(f"Hash comparison on collision failed: {e}")

    # If configured to skip renaming on collision
    if "skip" in (duplicate_action or "").lower():
        logger.info(f"Target '{target_name}' already exists. Skipping rename for '{source_name}'.")
        return source_filepath, True

    # Otherwise auto-number: generate stem - Duplicate 1.ext, stem - Duplicate 2.ext...
    directory = os.path.dirname(target_filepath)
    stem, ext = os.path.splitext(target_name)

    # If stem already ends with " - Duplicate (\d+)", extract base stem
    m = re.match(r'^(.*?)\s*-\s*Duplicate\s*(\d+)$', stem, re.IGNORECASE)
    if m:
        base_stem = m.group(1).rstrip()
        start_counter = int(m.group(2)) + 1
    else:
        # Also support legacy (N) if present
        m_old = re.match(r'^(.*?)\s*\((\d+)\)$', stem)
        if m_old:
            base_stem = m_old.group(1).rstrip()
            start_counter = int(m_old.group(2)) + 1
        else:
            base_stem = stem
            start_counter = 1

    counter = start_counter
    while True:
        candidate_name = f"{base_stem} - Duplicate {counter}{ext}"
        candidate_path = os.path.join(directory, candidate_name)
        norm_candidate = os.path.normcase(os.path.abspath(candidate_path))

        if norm_candidate == norm_source:
            # Source file is already this candidate
            return candidate_path, False

        if not os.path.exists(candidate_path):
            logger.info(f"Resolved collision: '{target_name}' exists -> using '{candidate_name}'")
            return candidate_path, True

        counter += 1


class FileOperations:
    """Safe file operations for model rename, delete, and subfolder sorting."""

    # Sidecar extensions that should follow the model file
    SIDECAR_SUFFIXES = (
        '.metadata',
        '.json',          # Native SD WebUI / Civitai metadata
        '.civitai.info',  # Civitai Helper info
        '.preview.png',
        '.preview.jpg',
        '.preview.webp',
        '.png',           # Native SD WebUI preview
        '.jpg',           # Native SD WebUI preview
        '.jpeg',          # Native SD WebUI preview
        '.webp',          # Native SD WebUI preview
        '.txt',           # Native SD WebUI description/tags file
        '.sha256'         # Checksum file
    )

    def generate_display_name(self, model_name: str, version_name: str = "") -> str:
        return generate_display_name(model_name, version_name)

    def _get_associated_files(self, filepath: str) -> List[str]:
        """Collect all existing sidecar files associated with this model."""
        base_no_ext, _ = os.path.splitext(filepath)
        associated = []
        
        for suffix in self.SIDECAR_SUFFIXES:
            # 1. Stem sidecar (e.g. test_model.sha256)
            sidecar_path = base_no_ext + suffix
            if os.path.exists(sidecar_path) and sidecar_path != filepath:
                associated.append(sidecar_path)
                
            # 2. Full sidecar (e.g. test_model.safetensors.sha256)
            sidecar_path_full = filepath + suffix
            if os.path.exists(sidecar_path_full) and sidecar_path_full != filepath:
                if sidecar_path_full not in associated:
                    associated.append(sidecar_path_full)
            
        return associated

    def sanitize_filename(self, name: str, max_length: int = 200) -> str:
        return sanitize_filename(name, max_length)

    def generate_model_filename(self, model_name: str, version_name: str, original_filename: str, current_ext: str, format_choice: str = "Model - Version") -> str:
        return generate_model_filename(model_name, version_name, original_filename, current_ext, format_choice)

    def resolve_duplicate_filename(self, target_filepath: str, source_filepath: str, duplicate_action: str = "auto_number", source_hash: Optional[str] = None, hash_engine = None) -> Tuple[str, bool]:
        return resolve_duplicate_filename(target_filepath, source_filepath, duplicate_action, source_hash, hash_engine)

    def rename_model(
        self,
        filepath: str,
        new_name: str,
        resolve_duplicates: bool = False,
        duplicate_action: str = "auto_number",
        source_hash: Optional[str] = None,
        hash_engine = None
    ) -> str:
        """
        Rename model file and all associated sidecars.
        new_name can be a complete filename or just the stem (without extension).
        Returns new model filepath.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Model file not found: {filepath}")

        # Sanitize new_name to only allow renaming in the same folder (prevent traversal)
        clean_new_name = os.path.basename(new_name)
        if not clean_new_name or clean_new_name in ('.', '..'):
            raise ValueError("Invalid new filename")

        directory = os.path.dirname(filepath)
        _, ext = os.path.splitext(filepath)

        # Determine new filename and extension
        if not clean_new_name.lower().endswith(ext.lower()):
            new_filename = clean_new_name + ext
        else:
            new_filename = clean_new_name

        new_filepath = os.path.join(directory, new_filename)

        if resolve_duplicates:
            new_filepath, was_dup = resolve_duplicate_filename(
                new_filepath, filepath, duplicate_action=duplicate_action,
                source_hash=source_hash, hash_engine=hash_engine
            )
            if was_dup and "skip" in (duplicate_action or "").lower():
                return filepath
            if os.path.abspath(new_filepath) == os.path.abspath(filepath) and os.path.basename(new_filepath) == os.path.basename(filepath):
                # No change needed
                return filepath
        else:
            if os.path.exists(new_filepath) and os.path.normcase(os.path.abspath(filepath)) != os.path.normcase(os.path.abspath(new_filepath)):
                raise FileExistsError(f"Target file already exists: {new_filepath}")

        # Case-only rename check on Windows
        old_norm = os.path.normcase(os.path.abspath(filepath))
        new_norm = os.path.normcase(os.path.abspath(new_filepath))
        is_case_only = (old_norm == new_norm and os.path.basename(filepath) != os.path.basename(new_filepath))

        # Get sidecars before renaming main file
        sidecars = self._get_associated_files(filepath)

        # Rename main file with lock handling
        try:
            if is_case_only:
                temp_filepath = filepath + ".tmp_case_rename"
                os.rename(filepath, temp_filepath)
                os.rename(temp_filepath, new_filepath)
            else:
                os.rename(filepath, new_filepath)
            old_name = os.path.basename(filepath)
            new_name_base = os.path.basename(new_filepath)
            logger.info(f"Renamed '{old_name}' -> '{new_name_base}'")
        except PermissionError:
            raise RuntimeError(f"File is locked: {os.path.basename(filepath)} is currently loaded or in use by WebUI/Forge.")

        # Rename all sidecars
        base_no_ext, _ = os.path.splitext(filepath)
        new_base_no_ext, _ = os.path.splitext(new_filepath)

        for old_sidecar in sidecars:
            # Determine if this was a stem sidecar or a full sidecar
            if old_sidecar.startswith(filepath):
                # Full format (e.g. test_model.safetensors.sha256)
                suffix = old_sidecar[len(filepath):]
                new_sidecar = new_filepath + suffix
            else:
                # Stem format (e.g. test_model.sha256)
                suffix = old_sidecar[len(base_no_ext):]
                new_sidecar = new_base_no_ext + suffix

            try:
                if is_case_only and os.path.normcase(os.path.abspath(old_sidecar)) == os.path.normcase(os.path.abspath(new_sidecar)):
                    temp_sidecar = old_sidecar + ".tmp_case_rename"
                    os.rename(old_sidecar, temp_sidecar)
                    os.rename(temp_sidecar, new_sidecar)
                else:
                    if os.path.exists(new_sidecar):
                        os.remove(new_sidecar)
                    os.rename(old_sidecar, new_sidecar)
                logger.debug(f"Renamed sidecar: {os.path.basename(old_sidecar)} -> {os.path.basename(new_sidecar)}")
            except Exception as e:
                logger.error(f"Failed to rename sidecar {os.path.basename(old_sidecar)}: {e}")

        # Update previewLocalPath in renamed metadata sidecar if it exists
        new_metadata_path = new_base_no_ext + ".metadata"
        if os.path.exists(new_metadata_path):
            try:
                import json
                with open(new_metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    new_preview_name = ""
                    for ext_val in (".preview.jpg", ".preview.png", ".preview.webp", ".png", ".jpg", ".jpeg", ".webp"):
                        if os.path.exists(new_base_no_ext + ext_val):
                            new_preview_name = os.path.basename(new_base_no_ext + ext_val)
                            break

                    if new_preview_name:
                        data["previewLocalPath"] = new_preview_name
                        with open(new_metadata_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=4, ensure_ascii=False)
                        logger.debug(f"Updated previewLocalPath in metadata to: {new_preview_name}")
            except Exception as e:
                logger.error(f"Failed to update previewLocalPath inside renamed metadata: {e}")

        return new_filepath

    def delete_model(self, filepath: str) -> None:
        """
        Safely delete model file and all associated sidecars (sends to OS Recycle Bin).
        """
        if not os.path.exists(filepath):
            logger.warning(f"File does not exist to delete: {os.path.basename(filepath)}")
            return

        # Get sidecars before deleting main file
        sidecars = self._get_associated_files(filepath)

        def _safe_remove(target_path: str):
            try:
                import send2trash
                send2trash.send2trash(target_path)
            except Exception:
                os.remove(target_path)

        # Delete main file
        filename = os.path.basename(filepath)
        try:
            _safe_remove(filepath)
            logger.info(f"Deleted model '{filename}'")
        except PermissionError:
            raise RuntimeError(f"File is locked: {filename} is currently loaded or in use by WebUI/Forge.")

        # Delete all sidecars
        for sidecar in sidecars:
            try:
                _safe_remove(sidecar)
                logger.debug(f"Deleted sidecar: {os.path.basename(sidecar)}")
            except Exception as e:
                logger.error(f"Failed to delete sidecar {os.path.basename(sidecar)}: {e}")

    def sort_into_subfolder(self, filepath: str, category: str, base_directory: str = None, sidecar_data: dict = None) -> str:
        """
        Move model + sidecars into a subfolder named by category relative to base_directory.
        Returns new model filepath.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Model file not found: {filepath}")

        # Sanitize category name (no slashes, backslashes, dots, etc.)
        safe_category = "".join([c for c in category if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()
        if not safe_category:
            safe_category = "unsorted"

        # Resolve correct target directory relative to base_directory if provided
        if base_directory:
            target_dir = os.path.join(base_directory, safe_category)
        else:
            target_dir = os.path.join(os.path.dirname(filepath), safe_category)
            
        os.makedirs(target_dir, exist_ok=True)

        filename = os.path.basename(filepath)
        new_filepath = os.path.join(target_dir, filename)

        if os.path.exists(new_filepath):
            logger.warning(f"Target model already exists in subfolder: {filename} in '{safe_category}', skipping move")
            return filepath

        # Get sidecars before moving main file
        sidecars = self._get_associated_files(filepath)

        # Move main file
        try:
            shutil.move(filepath, new_filepath)
            logger.info(f"Moved model '{filename}' to '{safe_category}/'")
        except PermissionError:
            logger.warning(f"Model '{filename}' is locked or in use, skipping move to subfolder")
            return filepath

        # Move all sidecars
        for old_sidecar in sidecars:
            sidecar_filename = os.path.basename(old_sidecar)
            new_sidecar = os.path.join(target_dir, sidecar_filename)
            try:
                # If target sidecar exists, overwrite or remove first
                if os.path.exists(new_sidecar):
                    os.remove(new_sidecar)
                shutil.move(old_sidecar, new_sidecar)
                logger.debug(f"Moved sidecar: {sidecar_filename} -> {safe_category}/")
            except Exception as e:
                logger.error(f"Failed to move sidecar {sidecar_filename}: {e}")

        # Update metadata sidecar with new relative preview path
        new_base_no_ext, _ = os.path.splitext(new_filepath)
        sidecar_path = new_base_no_ext + ".metadata"
        if os.path.exists(sidecar_path):
            try:
                import json
                with open(sidecar_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Ensure previewLocalPath is relative basename
                if isinstance(data, dict) and "previewLocalPath" in data:
                    data["previewLocalPath"] = os.path.basename(data["previewLocalPath"])
                    
                with open(sidecar_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to update moved sidecar metadata for {filename}: {e}")

        return new_filepath
