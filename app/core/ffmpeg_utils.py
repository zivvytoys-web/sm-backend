import os
import shutil
import logging

logger = logging.getLogger("social_ai_platform")


def get_ffmpeg_executable() -> str:
    """
    Finds a working FFmpeg executable across Linux (Render/Docker), Windows, and macOS.
    Priority:
    1. System ffmpeg from PATH (e.g. /usr/bin/ffmpeg installed via apt-get)
    2. imageio-ffmpeg bundled binary with guaranteed execute permissions (chmod +x)
    3. Plain 'ffmpeg' fallback
    """
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and os.path.exists(sys_ffmpeg):
        return sys_ffmpeg

    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            try:
                os.chmod(exe, 0o755)
            except Exception:
                pass
            return exe
    except Exception as e:
        logger.debug(f"imageio_ffmpeg resolution notice: {e}")

    return "ffmpeg"

