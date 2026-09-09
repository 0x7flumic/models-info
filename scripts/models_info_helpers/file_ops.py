import os
import shutil
import logging
from typing import List

logger = logging.getLogger("models-info")

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

    def rename_model(self, filepath: str, new_name: str) -> str:
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
        if os.path.exists(new_filepath) and os.path.normcase(os.path.abspath(filepath)) != os.path.normcase(os.path.abspath(new_filepath)):
            raise FileExistsError(f"Target file already exists: {new_filepath}")

        # Get sidecars before renaming main file
        sidecars = self._get_associated_files(filepath)

        # Rename main file with lock handling
        try:
            os.rename(filepath, new_filepath)
            old_name = os.path.basename(filepath)
            new_name = os.path.basename(new_filepath)
            logger.info(f"Renamed '{old_name}' -> '{new_name}'")
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
