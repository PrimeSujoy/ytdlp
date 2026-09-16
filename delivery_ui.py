# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import html
import os


_VIDEO_CODEC_LABELS = {
    "h264": "AVC",
    "avc1": "AVC",
    "av1": "AV1",
    "av01": "AV1",
    "vp9": "VP9",
    "vp09": "VP9",
    "hevc": "HEVC",
    "h265": "HEVC",
}

_AUDIO_CODEC_LABELS = {
    "mp3": "MP3",
    "aac": "AAC",
    "opus": "OPUS",
    "vorbis": "VORBIS",
    "flac": "FLAC",
}


def _safe_text(value, limit: int) -> str:
    text = str(value or "N/A").strip() or "N/A"
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return html.escape(text)


def _format_size(size_bytes: int) -> str:
    size = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return "0 B"


def _format_duration(seconds: int, fallback: str | None = None) -> str:
    if seconds <= 0:
        return str(fallback or "N/A")
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h, {minutes}m, {secs}s"
    return f"{minutes}m, {secs}s"


def _codec_label(raw_codec: str, preferred: str, is_audio: bool) -> str:
    codec = str(raw_codec or "").lower()
    labels = _AUDIO_CODEC_LABELS if is_audio else _VIDEO_CODEC_LABELS
    for prefix, label in labels.items():
        if codec.startswith(prefix):
            return label
    return str(preferred or ("MP3" if is_audio else "Unknown")).upper()


def _quality_label(requested: str, height: int) -> str:
    requested = str(requested or "").lower()
    if requested.startswith("a") and requested[1:].isdigit():
        return requested.upper()
    if requested == "best" and height:
        return f"{height}P"
    if requested.endswith("p") and requested[:-1].isdigit():
        return requested.upper()
    if requested == "audio":
        return f"{height}P" if height else "AUDIO"
    return f"{height}P" if height else requested.upper() or "BEST"


def build_delivery_caption(
    *,
    title: str,
    channel: str,
    views: str,
    requested_quality: str,
    file_path: str,
    file_size: int,
    profile: dict,
    preferred_codec: str,
    as_document: bool,
    part_index: int,
    total_parts: int,
    fallback_duration: str,
    bot_username: str,
    video_url: str = "",
) -> str:
    """Build a compact Telegram HTML caption for video, audio and document media."""
    is_audio = False
    width = int(profile.get("width") or 0)
    height = int(profile.get("height") or 0)
    duration = int(profile.get("duration") or 0)
    extension = os.path.splitext(file_path)[1].lstrip(".").upper()
    if extension.isdigit() and len(extension) == 3:
        base_extension = os.path.splitext(os.path.splitext(file_path)[0])[1].lstrip(".").upper()
        extension = f"{base_extension}.{extension}" if base_extension else extension
    if not extension:
        extension = "MP3" if is_audio else "MEDIA"

    title_icon = "🎧" if is_audio else "🎬"
    header = f"{title_icon} <b>{_safe_text(title, 110)}</b>"
    lines = ["╭── <b>MEDIA DETAILS</b>"]

    if is_audio:
        audio_fallback = "M4A" if requested_quality.lower() == "audio" else "MP3"
        codec = _codec_label(profile.get("audio_codec", ""), audio_fallback, True)
        bitrate = int(profile.get("audio_bitrate") or 0)
        bitrate_kbps = max(1, round(bitrate / 1000)) if bitrate else 320
        lines.append(f"│ 🎵 <b>Audio:</b> <code>{codec} · {bitrate_kbps} kbps</code>")
        lines.append(f"│ 💿 <b>Ext:</b> <code>{extension}</code>")
    else:
        quality = _quality_label(requested_quality, height)
        is_audio_quality = quality.startswith("A")
        dimensions = f" ({width}×{height})" if width and height and not is_audio_quality else ""
        codec = _codec_label(profile.get("video_codec" if not is_audio_quality else "audio_codec", ""), preferred_codec, is_audio_quality)
        lines.append(f"│ 📺 <b>Quality:</b> <code>{quality}{dimensions}</code>")
        lines.append(f"│ 💿 <b>Codec:</b> <code>{codec}</code>  <b>Ext:</b> <code>{extension}</code>")

    lines.append(f"│ 📦 <b>File Size:</b> <code>{_format_size(file_size)}</code>")
    lines.append(f"│ ⏱ <b>Duration:</b> <code>{_format_duration(duration, fallback_duration)}</code>")
    if total_parts > 1:
        lines.append(f"│ 🧩 <b>Part:</b> <code>{part_index}/{total_parts}</code>")

    if as_document:
        delivery = "📁 Document"
    elif is_audio:
        delivery = "🎧 Telegram Audio"
    else:
        delivery = "🎬 Streaming Video"
    lines.append(f"│ 📤 <b>Delivery:</b> <code>{delivery}</code>")
    lines.append(f"│ 👤 <b>Channel:</b> <code>{_safe_text(channel, 42)}</code>")
    lines.append(f"│ 👁 <b>Views:</b> <code>{_safe_text(views, 24)}</code>")
    if video_url:
        lines.append(f"│ 🔗 <b>Url:</b> <a href=\"{html.escape(video_url)}\"><b>Watch On Youtube</b></a>")
    lines.append("╰────────────────────")

    username = str(bot_username or "NexTubeRobot").lstrip("@")
    footer = (
        f"⚡ <i>Downloaded via</i> "
        f"<a href=\"https://t.me/{html.escape(username)}\"><b>@{html.escape(username)}</b></a>"
    )
    caption = header + "\n\n" + "\n".join(lines) + "\n\n" + footer
    # Inputs are individually bounded, keeping the complete HTML safely below
    # Telegram's caption limit without ever cutting an entity in half.
    return caption
