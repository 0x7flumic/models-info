# Models Info Extension for SD WebUI

A high-performance metadata downloader, preview image manager, and model organizer for Stable Diffusion Checkpoints and LoRAs. Seamlessly integrated into the SD WebUI Forge (Neo) Extra Networks cards interface.

---

## ✨ Features

### 🔍 Metadata & Preview Downloads
- Queries online databases by file hash (SHA-256) to retrieve base model architecture, version names, trigger words, descriptions, and source links.
- Downloads official preview images and saves them locally alongside your models.
- Automatically optimizes preview images at your chosen resolution to conserve disk space.

### 🏷️ Dynamic Architecture & ⭐ Favorites Filtering
- **Base Model Filter**: Instant client-side dropdown filtering by architecture (e.g. *SD 1.5, SDXL, Flux, Pony, Illustrious, NoobAI, SD 3.5, Hunyuan*). Unmatched assets are neatly grouped under *Unknown*.
- **⭐ Favorites Only Toggle**: Filter your library down to your starred models with a single click.
- **Composable Filtering**: Combine filters together effortlessly (e.g. view only *SDXL + Favorites*).

### 🗂️ Automatic LoRA Subfolder Organization
- Analyzes model tags using exact word-boundary matching and automatically organizes LoRAs into thematic subfolders:
  - **Character**: Fictional characters, celebrities, or figures.
  - **Style**: Art styles, painterly aesthetics, or visual filters.
  - **Clothing**: Outfits, costumes, or fashion accessories.
  - **Concept**: Abstract concepts, objects, or specific themes.
  - **Pose**: Action stances, dynamic angles, or body positions.
  - **Background**: Landscapes, scenery, or environment art.
  - **Utility**: Helpers, enhancers, and noise offsets.

### ⚡ Blazing-Fast & Drive-Aware
- **In-Memory Caching**: Sidecar metadata is cached in RAM with file modification time validation, eliminating disk lag and keeping Extra Networks fast.
- **Multi-Directory & External Drive Support**: Fully supports custom and external directories (`--ckpt-dir`, `--lora-dir`).
- **Safe Operations**: Renaming handles Windows case-only changes safely, and deletions send files to the OS Recycle Bin.

---

## 🌐 Supported APIs

| Platform | Authentication | Capabilities |
| :--- | :--- | :--- |
| **Civitai** | Optional (API key optional for public models) | Hash lookup, tags, trigger words, versions, previews |
| **Arcenciel** | Required (API key required) | Hash lookup, version preview media, tags, trigger words |

Both services are queried with automatic fallback if a model is only hosted on one platform.

---

## ⚙️ Configuration & Settings

Go to **Settings ➔ Models Info** in the WebUI to customize settings:

- **Civitai API Key**: Optional authentication key for Civitai early-access or private models.
- **Arcenciel API Key**: Authentication key for Arcenciel lookups.
- **Primary Metadata Source**: Choose which platform is queried first before falling back to the other.
- **Preview Image Width**: Select downscale resolution (256px, 512px, 1024px, 2048px, or original) to keep page loading snappy.
- **Auto-sort LoRA into subfolders**: Automatically routes newly downloaded LoRAs into category folders.
- **Maximum NSFW level**: Controls preview thumbnail safety filter:
  - `None`: Allows only G-rated content.
  - `Soft`: Hides explicit or adult previews.
  - `Mature`: Hides extreme content only.
  - `X`: Allows all preview content.
- **Max concurrent API requests**: Controls parallel request concurrency to respect platform rate limits.
- **Enforce uniform card dimensions**: Keeps cards at a clean, consistent aspect ratio.

---
