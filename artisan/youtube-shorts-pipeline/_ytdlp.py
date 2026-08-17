"""yt-dlp wrapper that never writes back the shared cookies file.

yt-dlp's YoutubeDL.save_cookies() rewrites the configured cookiefile on every
close(). On this machine that re-export drops the 1P auth cookies (the known
"broken 3P-only export"), which then bot-blocks every subsequent download.
Downloads only need to READ cookies, so we subclass and no-op the save.
"""
import yt_dlp


class NoWritebackYDL(yt_dlp.YoutubeDL):
    def save_cookies(self):
        return None