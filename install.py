import launch

if not launch.is_installed("aiohttp"):
    launch.run_pip("install aiohttp", "requirements for models-info")

if not launch.is_installed("Pillow"):
    launch.run_pip("install Pillow", "requirements for models-info")

if not launch.is_installed("send2trash"):
    launch.run_pip("install send2trash", "requirements for models-info")
