# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import json
import logging
import asyncio
from config import Config
from queue_manager import queue_manager

logger = logging.getLogger("MediaProcessor")


async def run_cmd(cmd: list[str], task_id: str | None = None, env: dict | None = None) -> tuple[int, bytes, bytes]:
    merged_env = os.environ.copy() if env else None
    if env and merged_env is not None:
        merged_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )
    if task_id:
        queue_manager.register_process(task_id, proc)
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def get_media_dimensions(file_path: str) -> tuple[int, int]:
    """Extracts width and height of video via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        file_path
    ]
    try:
        _, stdout, _ = await run_cmd(cmd)
        dims = stdout.decode().strip().split("x")
        if len(dims) == 2:
            return int(dims[0]), int(dims[1])
    except Exception as e:
        logger.debug(f"Could not extract dimensions for {file_path}: {e}")
    return 1280, 720


async def get_media_duration(file_path: str) -> int:
    """Extracts duration in seconds of media via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        file_path
    ]
    try:
        _, stdout, _ = await run_cmd(cmd)
        val = stdout.decode().strip()
        if val:
            return int(float(val))
    except Exception as e:
        logger.debug(f"Could not extract duration for {file_path}: {e}")
    return 0


async def _get_precise_media_duration(file_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", file_path,
    ]
    try:
        returncode, stdout, _ = await run_cmd(cmd)
        if returncode == 0 and stdout:
            return max(0.0, float(stdout.decode().strip()))
    except (TypeError, ValueError, OSError):
        pass
    return 0.0


async def get_media_profile(file_path: str) -> dict:
    """Returns the actual codecs, dimensions, bitrate and duration of a media file."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,bit_rate:format=duration,bit_rate",
        "-of", "json",
        file_path,
    ]
    profile = {
        "width": 0,
        "height": 0,
        "duration": 0,
        "video_codec": "",
        "audio_codec": "",
        "audio_bitrate": 0,
    }
    try:
        returncode, stdout, stderr = await run_cmd(cmd)
        if returncode != 0 or not stdout:
            logger.debug(
                "Could not probe media profile for %s: %s",
                file_path,
                stderr.decode("utf-8", errors="ignore")[-200:],
            )
            return profile

        data = json.loads(stdout.decode("utf-8", errors="ignore"))
        for stream in data.get("streams", []):
            stream_type = stream.get("codec_type")
            if stream_type == "video" and not profile["video_codec"]:
                profile["video_codec"] = str(stream.get("codec_name") or "").lower()
                profile["width"] = int(stream.get("width") or 0)
                profile["height"] = int(stream.get("height") or 0)
            elif stream_type == "audio" and not profile["audio_codec"]:
                profile["audio_codec"] = str(stream.get("codec_name") or "").lower()
                profile["audio_bitrate"] = int(stream.get("bit_rate") or 0)

        media_format = data.get("format") or {}
        profile["duration"] = int(float(media_format.get("duration") or 0))
        if not profile["audio_bitrate"] and not profile["video_codec"]:
            profile["audio_bitrate"] = int(media_format.get("bit_rate") or 0)
    except Exception as e:
        logger.debug(f"Could not parse media profile for {file_path}: {e}")
    return profile


async def merge_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    task_id: str | None = None
) -> tuple[bool, str]:
    """Losslessly merges video and audio streams into container using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-y",
        output_path
    ]
    returncode, _, stderr = await run_cmd(cmd, task_id)
    if returncode == 0 and os.path.exists(output_path):
        return True, output_path
    return False, stderr.decode("utf-8", errors="ignore")[-200:]


async def split_streamable_video(
    file_path: str,
    max_chunk_size_bytes: int,
    task_id: str | None = None
) -> list[str]:
    """Losslessly split media near the Telegram limit while preserving streams."""
    total_size = os.path.getsize(file_path)
    if total_size <= max_chunk_size_bytes:
        return [file_path]

    duration = await _get_precise_media_duration(file_path)
    if duration <= 0:
        return await split_binary_file(file_path, max_chunk_size_bytes, task_id)

    dir_name = os.path.dirname(file_path)
    base_name = os.path.basename(file_path)
    name, ext = os.path.splitext(base_name)
    margin = max(1_000_000, int(getattr(Config, "STREAM_SPLIT_MARGIN", 3_000_000)))
    safe_target = max(1, min(max_chunk_size_bytes - margin, int(max_chunk_size_bytes * 0.95)))
    bytes_per_second = total_size / duration
    segment_time = max(1.0, safe_target / max(bytes_per_second, 1.0))
    part_prefix = f"{name}.part"

    def generated_parts() -> list[str]:
        return sorted(
            os.path.join(dir_name, entry)
            for entry in os.listdir(dir_name)
            if entry.startswith(part_prefix) and entry.endswith(ext)
        )

    def cleanup_parts(paths: list[str] | None = None) -> None:
        for path in paths if paths is not None else generated_parts():
            try:
                os.remove(path)
            except OSError:
                pass

    async def fallback_to_binary(reason: str) -> list[str]:
        logger.warning("%s; falling back to binary split", reason)
        cleanup_parts()
        return await split_binary_file(file_path, max_chunk_size_bytes, task_id)

    output_pattern = os.path.join(dir_name, f"{part_prefix}%03d{ext}")
    for attempt in range(4):
        if task_id and queue_manager.is_cancelled(task_id):
            cleanup_parts()
            return []
        cleanup_parts()
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", file_path,
            "-map", "0", "-map_chapters", "-1",
            "-c", "copy", "-avoid_negative_ts", "make_zero",
            "-f", "segment",
            "-segment_time", f"{segment_time:.3f}",
            "-segment_start_number", "1",
            "-reset_timestamps", "1",
            "-y", output_pattern,
        ]
        ret, _, stderr = await run_cmd(cmd, task_id)
        if task_id and queue_manager.is_cancelled(task_id):
            cleanup_parts()
            return []
        parts = generated_parts()
        if ret != 0 or len(parts) < 2:
            return await fallback_to_binary(
                "FFmpeg segment split failed: "
                + stderr.decode("utf-8", errors="ignore")[-200:]
            )

        sizes = [os.path.getsize(path) for path in parts]
        oversize = max(sizes)
        if all(0 < size <= max_chunk_size_bytes for size in sizes):
            durations = [await _get_precise_media_duration(path) for path in parts]
            duration_ok = all(part_duration > 0 for part_duration in durations)
            coverage_ok = sum(durations) >= duration * 0.98
            streams_ok = sum(sizes) >= total_size * 0.90
            if duration_ok and coverage_ok and streams_ok:
                return parts
            return await fallback_to_binary("Segment validation detected missing/corrupt media")

        # Keyframe placement can overshoot the requested segment time. Scale
        # the next attempt from the observed largest part while retaining
        # extra headroom. Four attempts keep processing bounded on small hosts.
        ratio = safe_target / max(oversize, 1)
        segment_time *= max(0.50, min(0.90, ratio * 0.95))
        logger.warning(
            "Split attempt %s produced an oversized part (%s bytes); retrying",
            attempt + 1,
            oversize,
        )

    return await fallback_to_binary("Could not keep streamable parts below Telegram limit")


def _split_binary_file_sync(
    file_path: str,
    max_chunk_size_bytes: int,
    task_id: str | None = None,
) -> list[str]:
    """Stream exact chunks to disk without holding a multi-GB part in RAM."""
    total_size = os.path.getsize(file_path)
    if total_size <= max_chunk_size_bytes:
        return [file_path]

    parts: list[str] = []
    chunk_num = 1
    buffer_size = 4 * 1024 * 1024
    with open(file_path, "rb") as source:
        while source.tell() < total_size:
            if task_id and queue_manager.is_cancelled(task_id):
                break
            part_path = f"{file_path}.{chunk_num:03d}"
            temp_path = f"{part_path}.tmp"
            remaining = min(max_chunk_size_bytes, total_size - source.tell())
            try:
                with open(temp_path, "wb") as target:
                    while remaining > 0:
                        if task_id and queue_manager.is_cancelled(task_id):
                            raise asyncio.CancelledError
                        data = source.read(min(buffer_size, remaining))
                        if not data:
                            raise OSError("Unexpected end of source file")
                        target.write(data)
                        remaining -= len(data)
                os.replace(temp_path, part_path)
            except BaseException:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                for path in parts:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return []
            parts.append(part_path)
            chunk_num += 1

    return parts


async def split_binary_file(
    file_path: str,
    max_chunk_size_bytes: int,
    task_id: str | None = None,
) -> list[str]:
    return await asyncio.to_thread(
        _split_binary_file_sync, file_path, max_chunk_size_bytes, task_id
    )
