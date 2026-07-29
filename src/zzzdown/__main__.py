import sys

if "--yt-dlp-cli" in sys.argv:
    sys.argv.remove("--yt-dlp-cli")
    from yt_dlp import main as ytdlp_main

    raise SystemExit(ytdlp_main())

from .app import main

raise SystemExit(main())
