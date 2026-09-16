# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import asyncio
import io
import logging
import os

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from media_processor import run_cmd

logger = logging.getLogger("ThumbnailHelper")

_MAX_DIMENSION = 640
_MAX_FILE_SIZE = 200 * 1024


def normalize_thumbnail(source: str | bytes, destination: str) -> bool:
    """Re-encode thumbnail to valid JPEG for Telegram upload.

    Opens with PIL, converts to RGB, resizes if needed, and saves as JPEG.
    Telegram silently drops thumbnails that aren't valid JPEG.
    """
    try:
        if isinstance(source, bytes):
            img = Image.open(io.BytesIO(source))
        else:
            img = Image.open(source)

        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        w, h = img.size
        if w > _MAX_DIMENSION or h > _MAX_DIMENSION:
            img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

        img.save(destination, format="JPEG", quality=95, optimize=True)
        if os.path.getsize(destination) > _MAX_FILE_SIZE:
            img.save(destination, format="JPEG", quality=88, optimize=True)
        return True
    except Exception as e:
        logger.debug(f"Thumbnail normalize failed for {source}: {e}")
        return False


def _create_fallback_thumbnail(destination: str, media_label: str) -> bool:
    try:
        image = Image.new("RGB", (320, 180), "#171717")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 320, 14), fill="#ff0033")
        text = f"YOUTUBE {media_label.upper()}"
        font = ImageFont.load_default(size=22)
        bounds = draw.textbbox((0, 0), text, font=font)
        x = (320 - (bounds[2] - bounds[0])) // 2
        y = (180 - (bounds[3] - bounds[1])) // 2
        draw.text((x, y), text, fill="white", font=font)
        image.save(destination, format="JPEG", quality=88, optimize=True)
        return True
    except Exception as e:
        logger.warning(f"Could not create fallback thumbnail: {e}")
        return False


def _thumbnail_urls(video_id: str, metadata_url: str | None) -> list[str]:
    candidates = [metadata_url] if metadata_url else []
    candidates.extend(
        f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        for name in ("maxresdefault", "sddefault", "hqdefault", "mqdefault")
    )
    return list(dict.fromkeys(url for url in candidates if url))


async def prepare_thumbnail(
    work_dir: str,
    video_id: str,
    metadata_url: str | None,
    custom_path: str | None = None,
    video_path: str | None = None,
    media_label: str = "video",
) -> str | None:
    """Build a valid thumbnail using custom, YouTube, frame, then placeholder fallbacks."""
    destination = os.path.join(work_dir, "telegram_thumb.jpg")

    if custom_path and os.path.exists(custom_path):
        if await asyncio.to_thread(normalize_thumbnail, custom_path, destination):
            return destination

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            for url in _thumbnail_urls(video_id, metadata_url):
                try:
                    response = await client.get(url)
                    if response.status_code != 200 or not response.content:
                        continue
                    if len(response.content) > 5 * 1024 * 1024:
                        continue
                    if await asyncio.to_thread(normalize_thumbnail, response.content, destination):
                        return destination
                except Exception as e:
                    logger.debug(f"Thumbnail download failed for {url}: {e}")
    except Exception as e:
        logger.debug(f"Could not initialize thumbnail downloader: {e}")

    if video_path and os.path.exists(video_path):
        frame_path = os.path.join(work_dir, "thumbnail_frame.jpg")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-ss", "1",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            "-y", frame_path,
        ]
        returncode, _, _ = await run_cmd(command)
        if returncode == 0 and os.path.exists(frame_path):
            if await asyncio.to_thread(normalize_thumbnail, frame_path, destination):
                return destination

    if await asyncio.to_thread(_create_fallback_thumbnail, destination, media_label):
        return destination
    return None
