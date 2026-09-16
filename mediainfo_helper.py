# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import json
import re
import asyncio
import logging
from urllib.parse import quote, urlsplit, urlunsplit
from media_processor import get_media_duration, run_cmd


def _build_proxy_env() -> dict[str, str]:
    """Build http_proxy/https_proxy env vars from Config for FFmpeg."""
    from config import Config
    proxy_url = str(getattr(Config, "YTDLP_PROXY_URL", "") or "").strip()
    if not proxy_url:
        return {}
    parsed = urlsplit(proxy_url)
    if not parsed.hostname:
        return {}
    if parsed.username is None:
        username = str(getattr(Config, "YTDLP_PROXY_USERNAME", "") or "").strip()
        password = str(getattr(Config, "YTDLP_PROXY_PASSWORD", "") or "")
        if username:
            creds = quote(username, safe="")
            if password:
                creds += ":" + quote(password, safe="")
            proxy_url = urlunsplit(parsed._replace(netloc=f"{creds}@{parsed.netloc}"))
    return {"http_proxy": proxy_url, "https_proxy": proxy_url, "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url}

logger = logging.getLogger("MediaInfoHelper")


def select_screenshot_source(metadata: dict, qualities: dict) -> tuple[str | None, str]:
    """Pick a stream URL suitable for fast screenshot extraction.

    Prefer the highest cached stream so screenshots retain the best available
    detail without requiring another format lookup.

    Returns:
        (url, quality_label) — e.g. ("https://...", "720p")
    """
    direct = str((metadata or {}).get("direct_url") or "").strip()
    raw_quality = str((metadata or {}).get("direct_quality") or "").replace("p", "").strip()
    direct_height = int(raw_quality) if raw_quality.isdigit() else 0
    if direct and ".m3u8" not in direct:
        label = f"{direct_height}p" if direct_height else "Best"
        return direct, label

    candidates = []
    for q_name, quality in (qualities or {}).items():
        entries = [quality, *((quality.get("formats_by_codec") or {}).values())]
        for entry in entries:
            url = str((entry or {}).get("url") or "").strip()
            h = int((entry or {}).get("height") or 0)
            if url and h > 0 and ".m3u8" not in url:
                candidates.append((h, url, q_name))
    if not candidates:
        return None, ""

    # Pick the highest resolution available for best screenshot quality
    best = max(candidates, key=lambda item: item[0])
    return best[1], best[2]


def format_bytes(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


async def upload_to_paste(text: str) -> str | None:
    """Uploads text report to Nekobin or Dpaste and returns a shareable Web URL."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Try Nekobin
            try:
                resp = await client.post("https://nekobin.com/api/documents", json={"content": text})
                if resp.status_code == 201:
                    data = resp.json()
                    key = data.get("result", {}).get("key")
                    if key:
                        return f"https://nekobin.com/{key}"
            except Exception:
                pass

            # 2. Fallback to Dpaste
            resp2 = await client.post("https://dpaste.com/api/", data={"content": text, "expiry_days": 14})
            if resp2.status_code == 200 and resp2.text:
                return resp2.text.strip()
    except Exception as e:
        logger.warning(f"Failed to create paste link: {e}")
    return None


async def upload_to_pastyx(content: str, title: str = "", author: str = "@NexTubeBot") -> str | None:
    """Uploads markdown content to PastyX and returns the shareable URL."""
    import httpx

    payload = {"content": content}
    if title:
        payload["title"] = title
    if author:
        payload["author"] = author

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://pastyx.pages.dev/api/paste",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                url = data.get("url")
                if url:
                    return url
    except Exception as e:
        logger.warning(f"Failed to upload to PastyX: {e}")
    return None


async def generate_screenshots(
    file_path: str,
    output_dir: str,
    count: int = 5,
    duration_hint: float | int | None = None,
) -> list[str]:
    """Generates evenly spaced high-resolution screenshots across video duration.

    If FFmpeg fails on a URL (e.g. IP-locked googlevideo), downloads the video
    via httpx+proxy first, then extracts frames from the local file.
    """
    os.makedirs(output_dir, exist_ok=True)
    is_url = str(file_path).lower().startswith(("http://", "https://"))

    try:
        duration = float(duration_hint or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        duration = await get_media_duration(file_path)

    if duration > 0:
        end = max(0.05, float(duration) - min(0.5, float(duration) * 0.05))
        timestamps = [end * (i + 0.5) / count for i in range(count)] if count > 0 else []
    else:
        timestamps = [0.0, 0.5, 1.0, 2.0, 3.0][:count]

    limiter = asyncio.Semaphore(max(1, min(3, count)))
    proxy_env = _build_proxy_env() if is_url else {}

    async def extract_one(idx: int, ts: float, src: str | None = None) -> tuple[str | None, str]:
        src = src or file_path
        src_is_url = str(src).lower().startswith(("http://", "https://"))
        out_file = os.path.join(output_dir, f"screenshot_{idx+1}.jpg")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-threads", "1",
            "-ss", f"{ts:.3f}",
        ]
        if src_is_url:
            cmd.extend([
                "-rw_timeout", "15000000",
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "-headers", "Referer: https://www.youtube.com/\r\n",
            ])
        cmd.extend([
            "-i", src,
            "-vframes", "1",
            "-vf", "scale='min(2160,iw)':-2",
            "-q:v", "1",
            "-y", out_file,
        ])
        async with limiter:
            ret, _, stderr = await run_cmd(cmd, env=proxy_env if src_is_url else None)
        if ret == 0 and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
            return out_file, ""
        return None, stderr.decode("utf-8", errors="ignore")[-500:] if stderr else ""

    results = await asyncio.gather(*(
        extract_one(idx, ts) for idx, ts in enumerate(timestamps)
    ))
    screenshot_paths = [path for path, _ in results if path]
    last_error = next((error for _, error in reversed(results) if error), "")

    if not screenshot_paths and last_error:
        safe_error = re.sub(r"https?://\S+", "<media-url>", last_error)
        logger.warning(f"FFmpeg direct extraction failed: {safe_error}")

    return screenshot_paths


async def extract_mediainfo(file_path: str, display_name: str = "") -> tuple[str, str, str]:
    """
    Extracts stream metadata via ffprobe and returns:
    (rich_text_summary: str, full_report_text: str, premium_markdown: str)
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
    ]
    cmd.append(file_path)
    ret, stdout, _ = await run_cmd(cmd)
    if ret != 0 or not stdout:
        return "❌ Failed to read MediaInfo.", "", ""

    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore"))
    except Exception:
        return "❌ Error parsing MediaInfo JSON.", "", ""

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    fname = display_name if display_name else os.path.basename(file_path)
    # Clean up URLs and temp filenames
    if fname.startswith(("http://", "https://")):
        fname = "Stream Sample"
    elif fname.startswith("sample."):
        ext = fname.split(".")[-1] if "." in fname else ""
        fname = f"Sample ({ext.upper()})" if ext else "Sample"
    # Truncate very long filenames
    if len(fname) > 60:
        fname = fname[:57] + "..."
    size_str = format_bytes(int(fmt.get("size") or (os.path.getsize(file_path) if os.path.exists(file_path) else 0)))
    duration_sec = int(float(fmt.get("duration") or 0))
    mins, secs = divmod(duration_sec, 60)
    hours, mins = divmod(mins, 60)
    dur_str = f"{hours}h {mins}m {secs}s" if hours else f"{mins}m {secs}s"
    container = fmt.get("format_long_name", fmt.get("format_name", "Unknown"))

    video_info = []
    audio_info = []
    sub_info = []

    # Detailed per-stream data for premium markdown
    video_streams_detail = []
    audio_streams_detail = []
    sub_streams_detail = []

    for s in streams:
        c_type = s.get("codec_type")
        c_name = (s.get("codec_name") or "unknown").upper()

        if c_type == "video":
            w = s.get("width", "N/A")
            h = s.get("height", "N/A")
            fps = s.get("r_frame_rate", "N/A")
            try:
                if "/" in fps:
                    num, den = fps.split("/")
                    fps = f"{round(float(num)/float(den), 2)}fps"
            except Exception:
                pass
            bitrate = int(s.get("bit_rate") or fmt.get("bit_rate") or 0)
            br_str = f"{bitrate // 1000}kb/s" if bitrate else "N/A"
            # Resolution label
            res_label = ""
            try:
                h_int = int(h)
                if h_int >= 2160:
                    res_label = "4K"
                elif h_int >= 1440:
                    res_label = "1440p"
                elif h_int >= 1080:
                    res_label = "1080p"
                elif h_int >= 720:
                    res_label = "720p"
                elif h_int >= 480:
                    res_label = "480p"
                elif h_int >= 360:
                    res_label = "360p"
                else:
                    res_label = f"{h}p"
            except (ValueError, TypeError):
                res_label = ""
            res_part = f" [{res_label}]" if res_label else ""
            video_info.append(f"{c_name} {w}x{h}{res_part} {fps} {br_str}")

            # Detailed data for markdown
            profile = s.get("profile", "")
            pix_fmt = s.get("pix_fmt", "")
            color_space = s.get("color_space", "")
            color_range = s.get("color_range", "")
            scan_type = s.get("field_order", "progressive")
            video_streams_detail.append({
                "codec": c_name, "width": w, "height": h, "fps": fps,
                "bitrate": br_str, "profile": profile, "pix_fmt": pix_fmt,
                "color_space": color_space, "color_range": color_range,
                "scan_type": scan_type, "res_label": res_label,
            })

        elif c_type == "audio":
            channels = s.get("channels", 2)
            ch_str = "Stereo" if channels == 2 else f"{channels}ch"
            sr = s.get("sample_rate", "48000")
            sr_str = f"{int(sr)/1000:.1f}kHz" if sr else "N/A"
            bitrate = int(s.get("bit_rate") or 0)
            br_str = f"{bitrate // 1000}kb/s" if bitrate else "N/A"
            audio_info.append(f"{c_name} {ch_str} {sr_str} {br_str}")

            lang = (s.get("tags") or {}).get("language", "und")
            title = (s.get("tags") or {}).get("title", "")
            audio_streams_detail.append({
                "codec": c_name, "channels": ch_str, "sample_rate": sr_str,
                "bitrate": br_str, "language": lang, "title": title,
            })

        elif c_type == "subtitle":
            lang = (s.get("tags") or {}).get("language", "und")
            sub_info.append(f"{c_name}[{lang}]")
            sub_streams_detail.append({"codec": c_name, "language": lang})

    video_count = len(video_info)
    audio_count = len(audio_info)
    sub_count = len(sub_info)

    summary = (
        "<b>ℹ️ MEDIA INFO</b>\n\n"
        f"📁 <code>{fname[:50]}</code>\n"
        f"📦 <code>{size_str}</code> | ⏱ <code>{dur_str}</code>\n"
        f"🎞 <code>{container[:30]}</code>\n"
    )

    if video_info:
        summary += f"\n<b>🎬 Video ({video_count}):</b>\n" + "\n".join(f"<code>{v}</code>" for v in video_info)
    if audio_info:
        summary += f"\n\n<b>🎧 Audio ({audio_count}):</b>\n" + "\n".join(f"<code>{a}</code>" for a in audio_info)
    if sub_info:
        summary += f"\n\n<b>💬 Subtitles ({sub_count}):</b>\n<code>" + ", ".join(sub_info) + "</code>"

    full_report = json.dumps(data, indent=2)

    # ── Styled Markdown ──
    md = []
    md.append(f"# {fname}")
    md.append("")
    md.append("## 🗒 General")
    md.append("")
    md.append(f"| Parameter | Value |")
    md.append(f"| :--- | :--- |")
    md.append(f"| **Complete name** | `{fname}` |")
    md.append(f"| **Format** | `{container[:60]}` |")
    md.append(f"| **File size** | `{size_str}` |")
    md.append(f"| **Duration** | `{dur_str}` |")
    overall_bitrate = int(fmt.get("bit_rate") or 0)
    if overall_bitrate:
        md.append(f"| **Overall bit rate** | `{overall_bitrate // 1000} kb/s` |")
    frame_rate_global = fmt.get("frame_rate", "")
    if frame_rate_global:
        md.append(f"| **Frame rate** | `{frame_rate_global} FPS` |")
    md.append(f"| **Writing application** | `{fmt.get('format_long_name', 'N/A')}` |")
    md.append("")

    if video_streams_detail:
        md.append("## 📊 Video")
        md.append("")
        for idx, vs in enumerate(video_streams_detail, 1):
            md.append(f"### Stream #{idx}")
            md.append("")
            md.append(f"| Parameter | Value |")
            md.append(f"| :--- | :--- |")
            md.append(f"| **ID** | `{idx}` |")
            md.append(f"| **Format** | `{vs['codec']}` |")
            if vs["profile"]:
                md.append(f"| **Format profile** | `{vs['profile']}` |")
            md.append(f"| **Width** | `{vs['width']} pixels` |")
            md.append(f"| **Height** | `{vs['height']} pixels` |")
            md.append(f"| **Frame rate** | `{vs['fps']}` |")
            md.append(f"| **Bit rate** | `{vs['bitrate']}` |")
            if vs["pix_fmt"]:
                md.append(f"| **Pixel format** | `{vs['pix_fmt']}` |")
            if vs["color_space"]:
                md.append(f"| **Color space** | `{vs['color_space']}` |")
            if vs["color_range"]:
                md.append(f"| **Color range** | `{vs['color_range']}` |")
            md.append(f"| **Scan type** | `{vs['scan_type']}` |")
            md.append("")

    if audio_streams_detail:
        md.append("## 🔊 Audio")
        md.append("")
        for idx, au in enumerate(audio_streams_detail, 1):
            md.append(f"### Stream #{idx}")
            md.append("")
            md.append(f"| Parameter | Value |")
            md.append(f"| :--- | :--- |")
            md.append(f"| **ID** | `{idx}` |")
            md.append(f"| **Format** | `{au['codec']}` |")
            md.append(f"| **Channels** | `{au['channels']}` |")
            md.append(f"| **Sample rate** | `{au['sample_rate']}` |")
            md.append(f"| **Bit rate** | `{au['bitrate']}` |")
            if au["language"] and au["language"] != "und":
                md.append(f"| **Language** | `{au['language']}` |")
            if au["title"]:
                md.append(f"| **Title** | `{au['title']}` |")
            md.append("")

    if sub_streams_detail:
        md.append("## 📝 Subtitle")
        md.append("")
        for idx, sb in enumerate(sub_streams_detail, 1):
            md.append(f"| Parameter | Value |")
            md.append(f"| :--- | :--- |")
            md.append(f"| **ID** | `{idx}` |")
            md.append(f"| **Format** | `{sb['codec']}` |")
            if sb["language"] and sb["language"] != "und":
                md.append(f"| **Language** | `{sb['language']}` |")
            md.append("")

    md.append("---")
    md.append(f"*Generated by NexTube Bot • [View Online](https://pastyx.pages.dev)*")

    premium_md = "\n".join(md)
    return summary, full_report, premium_md


def build_all_qualities_mediainfo(metadata: dict, qualities: dict) -> tuple[str, str, str]:
    """
    Builds a comprehensive MediaInfo report from cached yt-dlp data.
    Shows real MediaInfo-style technical details per stream.

    Returns:
        (telegram_summary: str, full_json_report: str, premium_markdown: str)
    """
    title = metadata.get("title", "Unknown")
    channel = metadata.get("channel_name", "YouTube")
    duration = metadata.get("duration_str", "N/A")
    duration_sec = int(metadata.get("duration_sec") or 0)
    views = metadata.get("views", "N/A")
    likes = metadata.get("likes", "N/A")
    upload_date = metadata.get("upload_date", "N/A")
    vid = metadata.get("video_id", "")

    # ── Telegram summary (HTML) ──
    from config import Emojis
    love_tag = Emojis.tag(Emojis.LOVE, "❤️")
    summary_lines = [
        "<b>📊 MEDIA INFO</b>\n",
        f"🎬 <code>{title[:60]}</code>",
        f"📺 <code>{channel}</code>",
        f"⏱ <code>{duration}</code> | 👁 <code>{views}</code> | {love_tag} <code>{likes}</code>",
    ]

    # Group by resolution
    sorted_qualities = sorted(
        qualities.items(),
        key=lambda x: int(x[1].get("height") or x[0].replace("p", "")),
        reverse=True,
    )

    video_count = sum(len(qd.get("formats_by_codec", {})) for _, qd in sorted_qualities)
    audio_formats = metadata.get("audio_formats", [])

    summary_lines.append(f"\n<b>🎬 Video Streams:</b> <code>{video_count}</code>")
    summary_lines.append(f"<b>🎧 Audio Streams:</b> <code>{len(audio_formats)}</code>")
    summary = "\n".join(summary_lines)

    # ── Full JSON report ──
    import json as _json
    full_report = _json.dumps({"metadata": {k: v for k, v in metadata.items() if k != "description"}, "qualities": {
        q: {k: v for k, v in qd.items() if k != "url"} for q, qd in qualities.items()
    }}, indent=2, default=str)

    # ── Real MediaInfo Markdown ──
    md = []
    md.append(f"# MediaInfo — {title}")
    md.append("")

    # ── General Section ──
    md.append("## General")
    md.append("")
    md.append("```")
    md.append(f"Complete name                            : {title}")
    md.append(f"Format                                   : {channel}")
    md.append(f"Duration                                 : {duration}")
    md.append(f"Overall bit rate                         : N/A")
    md.append(f"Views                                    : {views}")
    md.append(f"Likes                                    : {likes}")
    md.append(f"Upload date                              : {upload_date}")
    md.append("```")
    md.append("")

    # ── Video Streams ──
    if sorted_qualities:
        md.append("## Video")
        md.append("")
        stream_idx = 0
        for q_label, q_data in sorted_qualities:
            h = q_data.get("height", 0)
            w = q_data.get("width", 0)
            fps = q_data.get("fps", 30)
            codecs = q_data.get("formats_by_codec", {})
            for codec_name, cd in codecs.items():
                stream_idx += 1
                codec_str = cd.get("codec_string", "")
                profile = cd.get("codec_profile", "")
                level = cd.get("codec_level", "")
                pix_fmt = cd.get("pix_fmt", "")
                color_space = cd.get("color_space", "")
                color_range = cd.get("color_range", "")
                color_transfer = cd.get("color_transfer", "")
                color_primaries = cd.get("color_primaries", "")
                vbr = cd.get("video_bitrate", 0)
                tbr = cd.get("total_bitrate", 0)
                total_sz = cd.get("total_size", 0)
                is_muxed = cd.get("is_muxed", False)
                ext = cd.get("ext", "")
                proto = cd.get("protocol", "")

                # Build codec info line
                codec_info = codec_name
                if "vp9" in codec_str.lower():
                    codec_info = "Google VP9"
                elif "av01" in codec_str.lower():
                    codec_info = "AOMedia AV1"
                elif "hvc1" in codec_str.lower() or "hev1" in codec_str.lower():
                    codec_info = "High Efficiency Video Coding"
                elif "avc" in codec_str.lower() or "h264" in codec_str.lower():
                    codec_info = "Advanced Video Coding"

                br_str = f"{tbr} kb/s" if tbr else "N/A"
                vbr_str = f"{vbr} kb/s" if vbr else "N/A"
                size_str = format_bytes(total_sz) if total_sz else "N/A"

                md.append(f"### Video #{stream_idx}")
                md.append("")
                md.append("```")
                md.append(f"Format                                   : {codec_str}")
                md.append(f"Format/Info                              : {codec_info}")
                if profile:
                    md.append(f"Format profile                           : {profile}")
                if level:
                    md.append(f"Format level                             : {level}")
                md.append(f"Codec ID                                 : {codec_str}")
                md.append(f"Width                                    : {w} pixels")
                md.append(f"Height                                   : {h} pixels")
                md.append(f"Display aspect ratio                     : {w}:{h}" if w and h else "")
                md.append(f"Frame rate                               : {fps} FPS")
                md.append(f"Bit rate                                 : {br_str}")
                md.append(f"Video bit rate                           : {vbr_str}")
                if pix_fmt:
                    md.append(f"Pixel format                            : {pix_fmt}")
                if color_space:
                    md.append(f"Color space                             : {color_space}")
                if color_transfer:
                    md.append(f"Color transfer                           : {color_transfer}")
                if color_primaries:
                    md.append(f"Color primaries                          : {color_primaries}")
                if color_range:
                    md.append(f"Color range                              : {color_range}")
                md.append(f"Scan type                                : Progressive")
                md.append(f"Stream size                              : {size_str}")
                mux_str = "Yes" if is_muxed else "No (DASH video only)"
                md.append(f"Muxed with audio                         : {mux_str}")
                md.append(f"Container                                : {ext.upper()}")
                md.append(f"Protocol                                 : {proto}")
                md.append("```")
                md.append("")

    # ── Audio Streams ──
    if audio_formats:
        md.append("## Audio")
        md.append("")
        for idx, af in enumerate(audio_formats, 1):
            codec_str = af.get("codec_string", af.get("codec", ""))
            abr = af.get("bitrate", 0)
            sample_rate = af.get("sample_rate", "")
            channels = af.get("channels", 0)
            ch_layout = af.get("channel_layout", "")
            aext = af.get("ext", "")
            asize = af.get("size", 0)
            itag = af.get("itag", "")

            # Codec info
            codec_info = "Unknown"
            cl = codec_str.lower()
            if "mp4a" in cl or "aac" in cl:
                codec_info = "Advanced Audio Coding"
            elif "opus" in cl:
                codec_info = "Opus"
            elif "mp3" in cl or "mpa1" in cl:
                codec_info = "MPEG Audio Layer 3"
            elif "vorbis" in cl:
                codec_info = "Vorbis"

            sr_str = f"{int(sample_rate)//1000:.1f} kHz" if sample_rate else "N/A"
            ch_str = f"{channels} channels" if channels else "N/A"
            if ch_layout:
                ch_str = ch_layout
            br_str = f"{abr} kb/s" if abr else "N/A"
            size_str = format_bytes(asize) if asize else "N/A"

            md.append(f"### Audio #{idx}")
            md.append("")
            md.append("```")
            md.append(f"Format                                   : {codec_str}")
            md.append(f"Format/Info                              : {codec_info}")
            md.append(f"Codec ID                                 : {codec_str}")
            md.append(f"Duration                                 : {duration}")
            md.append(f"Bit rate mode                            : CBR")
            md.append(f"Bit rate                                 : {br_str}")
            md.append(f"Channel(s)                               : {ch_str}")
            md.append(f"Sampling rate                            : {sr_str}")
            md.append(f"Stream size                              : {size_str}")
            md.append(f"Container                                : {aext.upper()}")
            md.append("```")
            md.append("")

    md.append("---")
    md.append(f"*Generated by NexTube Bot • [View Online](https://pastyx.pages.dev)*")

    premium_md = "\n".join(md)
    return summary, full_report, premium_md
