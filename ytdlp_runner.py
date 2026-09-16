# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import re
import sys
import asyncio
import logging
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from config import Config
from cookie_manager import cookie_manager
from queue_manager import queue_manager

logger = logging.getLogger("YtDlpRunner")

# Invoke yt-dlp through the running interpreter so Windows background launches
# do not depend on the virtual environment's Scripts directory being in PATH.
_YTDLP_COMMAND = [sys.executable, "-m", "yt_dlp"]

import shutil

def get_js_runtime_args() -> list[str]:
    """Detects available JS runtime (Node.js or Deno) and returns yt-dlp arguments."""
    deno = shutil.which("deno")
    node = shutil.which("node")
    if deno:
        return ["--js-runtimes", "deno"]
    if node:
        return ["--js-runtimes", "node"]

    explicit_node = str(os.getenv("YTDLP_NODE_PATH", "") or "").strip()
    candidates = [Path(explicit_node)] if explicit_node else []
    # Codex's bundled Python keeps Node in a sibling dependency directory but
    # may sanitize PATH for child processes. Derive that exact location safely.
    base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if base_executable.parent.name.lower() == "python":
        candidates.append(base_executable.parent.parent / "node" / "bin" / "node.exe")
    for candidate in candidates:
        if candidate.is_file():
            return ["--js-runtimes", f"node:{candidate}"]
    return []

_JS_RUNTIME_ARGS = get_js_runtime_args()
_EXTRACTOR_ARGS = []

if _JS_RUNTIME_ARGS:
    logger.info(f"JavaScript runtime for yt-dlp: {_JS_RUNTIME_ARGS[1]}")
else:
    logger.warning("No JavaScript runtime (Node.js / Deno) detected for yt-dlp!")


def get_cookie_args() -> list[str]:
    active = cookie_manager.get_active_cookie()
    if active and os.path.exists(active):
        return ["--cookies", active]
    return []


def get_resolver_cookie_args() -> list[str]:
    """Returns cookie args for the dedicated resolver cookie (metadata/format extraction only)."""
    resolver = cookie_manager.get_resolver_cookie_path()
    if resolver and os.path.exists(resolver):
        return ["--cookies", resolver]
    return []


_SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}


def get_authenticated_proxy_url() -> str:
    """Builds a proxy URL from separate credentials without logging them."""
    proxy_url = str(getattr(Config, "YTDLP_PROXY_URL", "") or "").strip()
    if not proxy_url:
        return ""

    parsed = urlsplit(proxy_url)
    if parsed.scheme.lower() not in _SUPPORTED_PROXY_SCHEMES or not parsed.hostname:
        return ""
    if parsed.username is not None:
        return proxy_url

    username = str(getattr(Config, "YTDLP_PROXY_USERNAME", "") or "").strip()
    password = str(getattr(Config, "YTDLP_PROXY_PASSWORD", "") or "")
    if not username:
        return proxy_url

    credentials = quote(username, safe="")
    if password:
        credentials += ":" + quote(password, safe="")
    return urlunsplit(parsed._replace(netloc=f"{credentials}@{parsed.netloc}"))


def get_proxy_args(has_cookie: bool) -> list[str]:
    """Returns safe yt-dlp proxy args according to the configured routing mode."""
    proxy_url = get_authenticated_proxy_url()
    mode = str(getattr(Config, "YTDLP_PROXY_MODE", "anonymous") or "anonymous").lower()
    if not proxy_url or mode == "off":
        return []
    if mode not in {"anonymous", "all"}:
        return []
    if mode == "anonymous" and has_cookie:
        return []

    parsed = urlsplit(proxy_url)
    if parsed.scheme.lower() not in _SUPPORTED_PROXY_SCHEMES or not parsed.hostname:
        return []
    return ["--proxy", proxy_url]


def sanitize_network_error(message: str) -> str:
    """Removes configured proxy URLs and URL credentials from log messages."""
    text = str(message or "")
    raw_proxy_url = str(getattr(Config, "YTDLP_PROXY_URL", "") or "").strip()
    authenticated_proxy_url = get_authenticated_proxy_url()
    for proxy_url in (authenticated_proxy_url, raw_proxy_url):
        if proxy_url:
            text = text.replace(proxy_url, "<proxy-url>")
    return re.sub(
        r"(?i)\b(https?|socks4a?|socks5h?)://[^\s/@]+(?::[^\s/@]*)?@",
        r"\1://<credentials>@",
        text,
    )


def get_fragment_download_args(extra_args: list[str] | None = None) -> list[str]:
    """Adds conservative parallel fragment downloading unless already set."""
    supplied = list(extra_args or [])
    if any(arg in {"-N", "--concurrent-fragments"} for arg in supplied):
        return []
    count = int(getattr(Config, "YTDLP_CONCURRENT_FRAGMENTS", 4) or 1)
    return ["--concurrent-fragments", str(max(1, min(8, count)))]


def build_quality_selector(
    quality_label: str,
    preferred_codec: str | None = None,
    preferred_container: str | None = None,
    preferred_audio_codec: str | None = None,
) -> tuple[str, list[str]]:
    """
    Builds an exact-height format selector conceptually matching:
    bestvideo[height=Q][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height=Q]+bestaudio/best[height=Q]
    """
    container = (preferred_container or "MKV").lower()
    codec_map = {"AVC": "avc", "AV1": "av01", "VP9": "vp", "HEVC": "hev", "VP9 HDR": "vp09.02", "AV1 HDR": "av01"}
    vcodec_prefix = codec_map.get(str(preferred_codec or "").upper())

    if quality_label == "audio":
        # YouTube normally exposes AAC/M4A directly. Keeping the source codec
        # avoids the expensive FFmpeg transcode required for a real MP3.
        return "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]", []

    if quality_label == "mp3":
        return "bestaudio/best", ["-x", "--audio-format", "mp3", "--audio-quality", "320K", "--embed-metadata"]

    # Audio bitrate selection: a128, a256, a320, etc.
    if quality_label.startswith("a") and quality_label[1:].isdigit():
        bitrate = int(quality_label[1:])
        audio_codec = str(preferred_audio_codec or "M4A").upper()
        if audio_codec == "OPUS":
            # Try native YouTube Opus (WebM) first, then fall back to FFmpeg conversion
            return (
                f"bestaudio[ext=webm][acodec=opus][abr<={bitrate}]"
                f"/bestaudio[ext=webm][acodec=opus]"
                f"/bestaudio",
                ["-x", "--audio-format", "opus", "--audio-quality", f"{bitrate}K", "--embed-metadata"],
            )
        # M4A max on YouTube is ~256kbps; 320K+ always needs MP3 transcode
        use_mp3 = audio_codec == "MP3" or bitrate > 256
        if use_mp3:
            return "bestaudio/best", [
                "-x", "--audio-format", "mp3", "--audio-quality", f"{bitrate}K", "--embed-metadata"
            ]
        # M4A path: try to find M4A with matching bitrate, else fallback to M4A best
        return f"bestaudio[ext=m4a][abr<={bitrate}]/bestaudio[ext=m4a]/bestaudio", [
            "-x", "--audio-format", "m4a", "--audio-quality", f"{bitrate}K", "--embed-metadata"
        ]

    if quality_label == "best":
        if container == "mp4":
            selectors = []
            if vcodec_prefix:
                selectors.append(f"bestvideo[vcodec^={vcodec_prefix}][ext=mp4]+bestaudio[ext=m4a]")
            selectors.extend((
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
                "best[ext=mp4][vcodec!=none][acodec!=none]",
            ))
            sel = "/".join(selectors)
        elif vcodec_prefix:
            sel = f"bestvideo[vcodec^={vcodec_prefix}]+bestaudio/bestvideo+bestaudio/best"
        else:
            sel = "bestvideo+bestaudio/best"
        return sel, ["--merge-output-format", container]

    h_match = re.search(r'\d+', quality_label)
    if h_match:
        height = int(h_match.group())
        selectors = []
        if container == "mp4":
            # MP4 must not fall back to unrestricted WebM/Opus audio. Such a
            # pairing may download successfully but fail during merge or play
            # inconsistently in Telegram clients.
            if vcodec_prefix:
                selectors.append(f"bestvideo[height={height}][vcodec^={vcodec_prefix}]+bestaudio[ext=m4a]")
            selectors.append(f"bestvideo[height={height}][ext=mp4]+bestaudio[ext=m4a]")
            selectors.append(f"bestvideo[height={height}][ext=mp4]")
        else:
            if vcodec_prefix:
                selectors.append(f"bestvideo[height={height}][vcodec^={vcodec_prefix}]+bestaudio[ext=m4a]")
                selectors.append(f"bestvideo[height={height}][vcodec^={vcodec_prefix}]+bestaudio")
            selectors.append(f"bestvideo[height={height}]+bestaudio")
            selectors.append(f"bestvideo[height={height}]")
        # Never fall back to best[height=...] muxed — it picks wrong/smaller formats
        return "/".join(selectors), ["--merge-output-format", container]

    return "bestvideo+bestaudio/best", ["--merge-output-format", container]


def parse_ytdlp_info(info: dict) -> tuple[dict, dict]:
    """
    Parses yt-dlp extracted dictionary into standardized metadata and qualities dicts.
    Accurately sums DASH video and audio stream sizes for real file sizes.
    """
    vid = info.get("id", "")
    title = info.get("title", "Video")
    channel = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "YouTube"
    
    follower_count = info.get("channel_follower_count")
    if follower_count:
        subs = f"{int(follower_count):,} subs"
    else:
        subs = "Official"

    # Upload date formatting: YYYYMMDD -> DD.MM.YYYY
    raw_date = str(info.get("upload_date") or "")
    if len(raw_date) == 8:
        upload_date = f"{raw_date[6:8]}.{raw_date[4:6]}.{raw_date[:4]}"
    else:
        upload_date = raw_date or "N/A"

    duration_sec = int(info.get("duration") or 0)
    mins, secs = divmod(duration_sec, 60)
    hours, mins = divmod(mins, 60)
    dur_str = f"{hours}h, {mins}m, {secs}s" if hours else f"{mins}m, {secs}s"

    views = f"{int(info.get('view_count') or 0):,}"
    likes = f"{int(info.get('like_count') or 0):,}" if info.get("like_count") else "N/A"
    description = info.get("description", "") or ""

    thumb = info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"

    muxed_formats = [
        fmt for fmt in info.get("formats", [])
        if fmt.get("vcodec") not in (None, "none")
        and fmt.get("acodec") not in (None, "none")
        and fmt.get("url")
        and str(fmt.get("protocol") or "").startswith("http")
    ]
    best_muxed = max(
        muxed_formats,
        key=lambda fmt: (
            int(fmt.get("height") or 0),
            float(fmt.get("tbr") or 0),
            int(fmt.get("filesize") or fmt.get("filesize_approx") or 0),
        ),
        default=None,
    )

    metadata = {
        "video_id": vid,
        "title": title,
        "channel_name": channel,
        "subscribers": subs,
        "upload_date": upload_date,
        "duration_sec": duration_sec,
        "duration_str": dur_str,
        "views": views,
        "likes": likes,
        "description": description,
        "thumbnail": thumb,
        "direct_url": best_muxed.get("url") if best_muxed else None,
        "direct_quality": f"{best_muxed.get('height')}p" if best_muxed and best_muxed.get("height") else None,
        "audio_formats": [],
    }

    # Best audio stream size for combining with separate video DASH streams
    audio_formats = [f for f in info.get("formats", []) if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    best_audio_size = 0
    audio_sizes = {"MP4": 0, "MKV": 0}

    # Build audio formats list for UI display
    seen_audio = {}
    for af in audio_formats:
        abr = int(af.get("abr") or af.get("tbr") or 0)
        if abr <= 0:
            continue
        ext = af.get("ext", "")
        acodec = af.get("acodec", "")
        raw_sz = int(af.get("filesize") or af.get("filesize_approx") or 0)
        if raw_sz == 0 and duration_sec > 0:
            raw_sz = int(abr * 1000 / 8 * duration_sec)
        is_m4a = ext == "m4a" or "mp4a" in str(acodec)
        label_key = f"{abr}"
        if label_key not in seen_audio or raw_sz > seen_audio[label_key].get("size", 0):
            seen_audio[label_key] = {
                "bitrate": abr,
                "ext": ext,
                "codec": acodec,
                "codec_string": str(acodec),
                "sample_rate": af.get("sample_rate") or "",
                "channels": af.get("channels") or 0,
                "channel_layout": af.get("channel_layout") or "",
                "audio_bitrate": int(af.get("abr") or 0),
                "size": raw_sz,
                "is_m4a": is_m4a,
                "itag": af.get("format_id") or "",
            }
    metadata["audio_formats"] = sorted(seen_audio.values(), key=lambda x: x["bitrate"])

    # Add synthetic 320kbps MP3 if not already present and audio exists
    if audio_formats and "320" not in seen_audio:
        best_audio = max(audio_formats, key=lambda f: f.get("tbr") or f.get("abr") or 0)
        best_abr = int(best_audio.get("abr") or best_audio.get("tbr") or 0)
        if best_abr >= 128:
            # Estimate 320kbps MP3 size from best audio stream
            est_size = int(320 * 1000 / 8 * duration_sec) if duration_sec > 0 else 0
            if est_size == 0:
                raw_sz = int(best_audio.get("filesize") or best_audio.get("filesize_approx") or 0)
                est_size = int(raw_sz * 320 / best_abr) if best_abr > 0 and raw_sz > 0 else 0
            metadata["audio_formats"].append({
                "bitrate": 320,
                "ext": "mp3",
                "codec": "mp3",
                "size": est_size,
                "is_m4a": False,
            })
            metadata["audio_formats"] = sorted(metadata["audio_formats"], key=lambda x: x["bitrate"])

    if audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get("tbr") or f.get("abr") or 0)
        best_audio_size = int(best_audio.get("filesize") or best_audio.get("filesize_approx") or 0)
        m4a_audios = [f for f in audio_formats if f.get("ext") == "m4a" or "mp4a" in str(f.get("acodec") or "")]
        if m4a_audios:
            best_m4a = max(m4a_audios, key=lambda f: f.get("tbr") or f.get("abr") or 0)
            audio_sizes["MP4"] = int(best_m4a.get("filesize") or best_m4a.get("filesize_approx") or best_audio_size)
        else:
            audio_sizes["MP4"] = best_audio_size
        audio_sizes["MKV"] = best_audio_size

    qualities = {}
    duration_sec = float(info.get("duration") or 0)
    for f in info.get("formats", []):
        h = f.get("height")
        vcodec = f.get("vcodec")
        if h and vcodec and vcodec != "none":
            raw_sz = int(f.get("filesize") or f.get("filesize_approx") or 0)
            # DASH streams often have filesize=0; estimate from bitrate × duration
            if raw_sz == 0:
                tbr = float(f.get("tbr") or 0)
                if tbr > 0 and duration_sec > 0:
                    raw_sz = int(tbr * 1000 / 8 * duration_sec)
            is_muxed = (f.get("acodec") != "none")
            total_sz = raw_sz if is_muxed else (raw_sz + best_audio_size)

            codec_name = "AVC"
            v_lower = str(vcodec).lower()
            if "vp9" in v_lower or "vp09" in v_lower:
                codec_name = "VP9"
            elif "av01" in v_lower:
                codec_name = "AV1"
            elif "hvc1" in v_lower or "hev1" in v_lower:
                codec_name = "HEVC"
            elif "avc" in v_lower or "h264" in v_lower:
                codec_name = "AVC"

            q_label = f"{h}p"

            entry = {
                "height": h,
                "width": f.get("width") or 0,
                "fps": f.get("fps") or 30,
                "codec": codec_name,
                "codec_string": str(vcodec),
                "codec_profile": f.get("vprofile") or f.get("codec_profile") or "",
                "codec_level": f.get("vlevel") or f.get("codec_level") or "",
                "pix_fmt": f.get("pix_fmt") or "",
                "color_space": f.get("color_space") or "",
                "color_range": f.get("color_range") or "",
                "color_transfer": f.get("color_transfer") or "",
                "color_primaries": f.get("color_primaries") or "",
                "video_bitrate": int(f.get("vbr") or 0),
                "video_size": raw_sz,
                "total_bitrate": int(f.get("tbr") or 0),
                "total_size": total_sz,
                "audio_sizes": audio_sizes,
                "url": f.get("url"),
                "itag": f.get("format_id"),
                "is_muxed": is_muxed,
                "ext": f.get("ext") or "",
                "protocol": f.get("protocol") or "",
            }

            if q_label not in qualities:
                qualities[q_label] = {
                    **entry,
                    "formats_by_codec": {codec_name: entry},
                }
            else:
                qualities[q_label]["formats_by_codec"][codec_name] = entry
                if total_sz > qualities[q_label]["total_size"]:
                    f_map = qualities[q_label]["formats_by_codec"]
                    qualities[q_label] = {**entry, "formats_by_codec": f_map}

    return metadata, qualities


async def extract_info_via_ytdlp(url: str, cookies: str | None = None, playlist_end: int | None = None, is_playlist: bool = False) -> dict | None:
    """Primary metadata and formats extractor using yt-dlp with resolver cookie.

    For normal video links: uses dedicated resolver cookie for metadata extraction.
    For playlists: uses regular cookie rotation (resolver cookie is skipped).
    Falls back to regular cookie rotation if no resolver cookie is configured."""
    try:
        import orjson as json_lib
    except ImportError:
        import json as json_lib

    # Priority: explicit cookie > resolver cookie (normal links only) > regular rotation
    use_resolver = False
    if cookies:
        cookie_args = ["--cookies", cookies]
    elif not is_playlist:
        resolver_args = get_resolver_cookie_args()
        if resolver_args:
            cookie_args = resolver_args
            use_resolver = True
        else:
            cookie_args = get_cookie_args()
    else:
        cookie_args = get_cookie_args()
    attempts = [(cookie_args, get_proxy_args(bool(cookie_args)))]
    anonymous_proxy_args = get_proxy_args(False)
    if cookie_args and anonymous_proxy_args and not attempts[0][1]:
        attempts.append(([], anonymous_proxy_args))

    for attempt_cookie_args, proxy_args in attempts:
        cmd = [
            *_YTDLP_COMMAND,
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            "--sleep-requests", "1",
            *_JS_RUNTIME_ARGS,
            *_EXTRACTOR_ARGS,
            *proxy_args,
            *attempt_cookie_args,
        ]
        # Add anti-ban sleep for resolver cookie
        if use_resolver:
            cmd.extend(["--sleep-interval", "1", "--max-sleep-interval", "3"])
        if playlist_end:
            cmd.extend(["--flat-playlist", "--playlist-end", str(playlist_end)])
        else:
            cmd.append("--no-playlist")
        cmd.append(url)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            timeout = 90 if await queue_manager.has_active_tasks() else 45
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0 and stdout:
                return json_lib.loads(stdout.decode(errors="ignore"))

            err_text = stderr.decode(errors="ignore") if stderr else ""
            if ("429" in err_text or "Too Many Requests" in err_text) and attempt_cookie_args:
                logger.warning("HTTP 429 encountered in yt-dlp extraction — rotating cookie")
                cookie_manager.rotate_cookie()
            safe_error = sanitize_network_error(err_text[-300:])
            route = "anonymous proxy" if proxy_args else "direct"
            logger.error(f"yt-dlp extraction failed via {route}: {safe_error}")
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:
                    pass
            raise
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:
                    pass
            logger.warning(f"yt-dlp metadata extraction timed out after {timeout}s")
        except Exception as e:
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:
                    pass
            safe_error = sanitize_network_error(str(e))
            logger.error(f"yt-dlp extraction error: {safe_error}")
    return None


async def download_media(
    url: str,
    format_selector: str,
    output_template: str,
    task_id: str,
    progress_hook=None,
    progress_reset_hook=None,
    stage_hook=None,
    stage_end_hook=None,
    extra_args: list[str] | None = None,
    is_playlist: bool = False,
) -> tuple[bool, str]:
    """
    Downloads media using yt-dlp subprocess with progress monitoring,
    cancellation support, and automatic fallback on HTTP 403 / unavailable formats.
    """
    pattern = re.compile(r"\[download\]\s+(\d+\.\d+)%\s+of\s+([~0-9\.]+[KMG]iB|\S+)\s+at\s+([0-9\.]+[KMG]iB/s|\S+)\s+ETA\s+(\S+)")
    progress_prefix = "__YTDLP_PROGRESS__"
    progress_template = (
        "download:" + progress_prefix
        + "%(progress.status)s\t%(info.format_id)s\t%(progress.downloaded_bytes)s"
        + "\t%(progress.total_bytes)s\t%(progress.total_bytes_estimate)s"
        + "\t%(progress.speed)s\t%(progress.eta)s"
    )

    def numeric(value: str) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def eta_text(value: float) -> str:
        seconds = max(0, int(value))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    claimed_cookie = cookie_manager.claim_cookie()
    cookie_candidates = [claimed_cookie] if claimed_cookie else []
    remaining = cookie_manager.get_cookie_candidates(include_anonymous=True)
    for c in remaining:
        if c != claimed_cookie:
            cookie_candidates.append(c)
    fragment_args = get_fragment_download_args(extra_args)

    last_err = ""
    for cookie_path in cookie_candidates:
        cargs = ["--cookies", cookie_path] if cookie_path else []
        proxy_args = get_proxy_args(bool(cookie_path))
        attempts = [(format_selector, list(extra_args or []), "")]
        attempt_index = 0
        local_recovery_started = False
        while attempt_index < len(attempts):
            sel, attempt_args, success_note = attempts[attempt_index]
            attempt_index += 1
            if queue_manager.is_cancelled(task_id):
                return False, "Task Cancelled"

            cmd = [
                *_YTDLP_COMMAND,
                "--newline",
                "--restrict-filenames",
                "--trim-filenames", "80",
                "--progress-template", progress_template,
                "--no-playlist",
                "--no-warnings",
                "--sleep-requests", "1",
                *_JS_RUNTIME_ARGS,
                *_EXTRACTOR_ARGS,
                *proxy_args,
                *fragment_args,
                "-f", sel,
                "-o", output_template,
                *cargs,
                *attempt_args,
            ]
            if is_playlist:
                cmd.extend(["--sleep-interval", "2", "--max-sleep-interval", "5"])
            cmd.append(url)

            cookie_label = os.path.basename(cookie_path) if cookie_path else "anonymous"
            route_label = f"{cookie_label} + proxy" if proxy_args else cookie_label
            logger.info(f"Downloading with selector '{sel}' via {route_label}...")

            try:
                if progress_reset_hook:
                    progress_reset_hook()
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                queue_manager.register_process(task_id, proc)
                # Drain stderr while reading progress. FFmpeg can otherwise fill
                # the pipe during a large merge and stall/fail after 100%.
                stderr_task = asyncio.create_task(proc.stderr.read())

                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="ignore").strip()

                    if queue_manager.is_cancelled(task_id):
                        proc.kill()
                        await proc.wait()
                        await stderr_task
                        if stage_end_hook:
                            await stage_end_hook()
                        return False, "Task Cancelled"

                    if line_str.startswith("[Merger]") or line_str.startswith("[VideoRemuxer]"):
                        if stage_hook:
                            await stage_hook("Merging", "FFmpeg")
                        continue
                    if line_str.startswith("[ExtractAudio]"):
                        if stage_hook:
                            await stage_hook("Converting Audio", "FFmpeg")
                        continue

                    if line_str.startswith(progress_prefix) and progress_hook:
                        fields = line_str[len(progress_prefix):].split("\t")
                        if len(fields) >= 7:
                            status, format_id, downloaded, exact_total, estimated_total, speed_value, eta_value = fields[:7]
                            downloaded_value = numeric(downloaded)
                            total_value = numeric(exact_total) or numeric(estimated_total)
                            speed_number = numeric(speed_value)
                            percent = (
                                min(100.0, downloaded_value * 100 / total_value)
                                if total_value > 0 else (100.0 if status == "finished" else 0.0)
                            )
                            await progress_hook(
                                percent,
                                f"{total_value}B" if total_value else "Unknown",
                                f"{speed_number}B/s" if speed_number else "0 B/s",
                                eta_text(numeric(eta_value)) if eta_value not in {"NA", "None"} else "N/A",
                                downloaded_bytes=downloaded_value,
                                total_bytes=total_value,
                                stream_key=format_id or "stream",
                            )
                            continue

                    m = pattern.search(line_str)
                    if m and progress_hook:
                        percent = float(m.group(1))
                        total_size = m.group(2)
                        speed = m.group(3)
                        eta = m.group(4)
                        await progress_hook(percent, total_size, speed, eta)

                await proc.wait()
                stderr = await stderr_task
                if stage_end_hook:
                    await stage_end_hook()

                if proc.returncode == 0:
                    return True, success_note or "Download Successful"
                else:
                    full_err = stderr.decode("utf-8", errors="ignore")
                    err_msg = sanitize_network_error(full_err[-1200:])
                    last_err = err_msg
                    if "429" in err_msg or "Sign in" in err_msg or "403" in err_msg:
                        if cookie_path:
                            cookie_manager.rotate_cookie()
                    logger.warning(f"Download with '{sel}' and {cookie_label} failed: {err_msg[-500:]}")

                    # If the selected streams downloaded successfully but MP4
                    # muxing failed, reuse those files and merge into MKV. MKV
                    # accepts the codec combinations emitted by YouTube. As a
                    # final recovery, fetch a single progressive stream, which
                    # requires no FFmpeg postprocessing at all.
                    postprocess_failed = any(
                        marker in full_err.lower()
                        for marker in ("postprocessing", "conversion failed", "ffmpegpostprocessor")
                    )
                    merge_index = next(
                        (i for i, arg in enumerate(attempt_args) if arg == "--merge-output-format"),
                        None,
                    )
                    if postprocess_failed and merge_index is not None:
                        local_recovery_started = True
                        requested_container = attempt_args[merge_index + 1].lower()
                        if requested_container == "mp4":
                            mkv_args = list(attempt_args)
                            mkv_args[merge_index + 1] = "mkv"
                            attempts.append((sel, mkv_args, "Download Successful (MKV merge fallback)"))
                        progressive = "best[vcodec!=none][acodec!=none]"
                        if not any(item[0] == progressive for item in attempts):
                            no_merge_args = attempt_args[:merge_index] + attempt_args[merge_index + 2:]
                            no_merge_args.append("--force-overwrites")
                            attempts.append((progressive, no_merge_args, "Download Successful (progressive fallback)"))
                        # This is a local muxing problem; changing cookies cannot
                        # fix it. Finish the recovery attempts for this cookie.
            except asyncio.CancelledError:
                if stage_end_hook:
                    await stage_end_hook()
                return False, "Task Cancelled"
            except Exception as e:
                if stage_end_hook:
                    await stage_end_hook()
                last_err = str(e)
                logger.warning(f"Exception downloading with '{sel}': {e}")

        if local_recovery_started:
            # Cookie rotation cannot repair a local FFmpeg/container failure and
            # would needlessly download a large video again.
            break

    return False, f"yt-dlp error: {last_err}"
