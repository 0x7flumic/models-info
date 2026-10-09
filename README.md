# Models Info Extension for SD WebUI

A metadata downloader, preview image manager, and model organizer for Stable Diffusion Checkpoints and LoRAs. Seamlessly integrated into the SD WebUI and WebUI Forge Extra Networks cards interface.

---

## Features

### 🔍 Metadata & Preview Downloads
- **Hash-Based Lookup**: Identifies models via SHA-256 or Quick Hash (AutoV1) against Civitai and Arcenciel databases.
- **Preview Retrieval**: Downloads and optimizes preview images locally alongside models (configurable to 256px, 512px, 1024px, 2048px, or original resolution).
- **Architecture Refinement**: Inspects file headers to identify architectures (e.g., SD 1.5, SDXL, Flux, Pony, Illustrious, NoobAI, SD 3.5, Anima, Hunyuan).
- **Trigger Word Insertion**: Injects trigger words into model prompts with a single click.

### 🏷️ Filtering & Organization
- **Base Model Filter**: Dropdown filtering by architecture inside Extra Networks.
- **Favorites**: Star models to filter by favorite status.
- **LoRA Categorization**: Automatically organizes LoRAs into subfolders (`Character`, `Style`, `Clothing`, `Concept`, `Pose`, `Background`, `Utility`) based on tags.
- **Auto-Renaming**: Sanitizes and standardizes model filenames (e.g., `Model_Version`) with duplicate collision handling (`- Duplicate N`).

### 🎛️ Card Actions
Hover over any model card in Extra Networks to access the action bar:
- **⭐ Favorite**: Toggle favorite status.
- **🌐 Open Source Page**: Open the model's external page (Civitai / Arcenciel) in a new tab.
- **🏷️ Inject Trigger Words**: Append trained trigger words directly into the active prompt textarea.
- **⚙️ Edit Metadata**: Open the edit modal to modify display name (card title), base model architecture, version, trigger words, external URL, or upload/select a local preview image.
- **✏️ Rename Model**: Rename the model file along with all associated sidecar files (`.metadata`, previews).
- **🗑️ Delete Model**: Permanently send the model file and associated sidecars to the OS Recycle Bin.

---

## Supported APIs

| Platform | Authentication | Capabilities |
| :--- | :--- | :--- |
| **Civitai** | Optional (API key optional for public models) | Hash lookup, tags, trigger words, versions, previews |
| **Arcenciel** | Required (API key required) | Hash lookup, version preview media, tags, trigger words |

Both platforms support automatic fallback when a model is missing from one service.

---

## Configuration & Settings

Configure via **Settings ➔ Models Info** in the WebUI:

- **Civitai API Key**: Optional authentication key for Civitai early-access or private models.
- **Arcenciel API Key**: Authentication key for Arcenciel lookups.
- **Primary Metadata Source**: Select initial platform to query before fallback.
- **Preview Image Width**: Select thumbnail resolution (256, 512, 1024, 2048, or original).
- **Auto-sort LoRA into category subfolders**: Move newly scanned LoRAs into category folders.
- **Maximum NSFW level for preview images**: Filter previews (`None`, `Soft`, `Mature`, `X`).
- **Max concurrent API requests**: Throttle parallel downloads (1–10).
- **Enforce Custom Card Dimensions**: Maintain standard 280x420 aspect ratio cards.
- **Hash Algorithm**: Choose between SHA256 (full file) or AutoV1 / Quick Hash (fast Civitai lookup).
- **Auto-rename model files**: Standardize filenames according to selected format (`Model_Version`, `Model - Version`, `Original Civitai Filename`, `Model Only`).
- **Duplicate Handling Policy**: Choose between auto-numbering (`- Duplicate N`) or skipping renaming on conflict.
- **Also rename already-cached models on rescan**: Apply auto-renaming rules across previously cached models.
