from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

import imageio_ffmpeg


root = Path(__file__).resolve().parents[1]
target = root / "src" / "zzzdown" / "resources" / "bin"
target.mkdir(parents=True, exist_ok=True)

if sys.platform == "win32":
    ffmpeg_name = "ffmpeg.exe"
elif sys.platform == "darwin":
    ffmpeg_name = "ffmpeg"
else:
    ffmpeg_name = "ffmpeg"

shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), target / ffmpeg_name)
for path in (target / ffmpeg_name,):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print(target)
