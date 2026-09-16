# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import html
import re
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Emojis, Config

try:
    from pyrogram.enums import ButtonStyle
    _HAS_STYLE = True
except ImportError:
    _HAS_STYLE = False
    class ButtonStyle:
        DEFAULT = None
        PRIMARY = None
        SUCCESS = None
        DANGER = None


def _style_kwargs(kind: str = "default") -> dict:
    """Return button style kwargs if ButtonStyle is available."""
    if not _HAS_STYLE:
        return {}
    style_map = {
        "primary": ButtonStyle.PRIMARY,
        "success": ButtonStyle.SUCCESS,
        "danger": ButtonStyle.DANGER,
        "default": ButtonStyle.DEFAULT,
    }
    s = style_map.get(kind, ButtonStyle.DEFAULT)
    if s is not None:
        return {"style": s}
    return {}


def format_bytes_human(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


def make_hashtag(text: str) -> str:
    clean = re.sub(r"[^\w\s]", "", text)
    tag = re.sub(r"\s+", "_", clean.strip())
    return f"#{tag}" if tag else "#youtube"


def split_description(description: str, chunk_size: int = 3900) -> list[str]:
    """Split a description into Telegram-safe plain-text chunks."""
    text = str(description or "No description available.").strip()
    chunks: list[str] = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, chunk_size + 1)
        if split_at < chunk_size // 2:
            split_at = text.rfind(" ", 0, chunk_size + 1)
        if split_at <= 0:
            split_at = chunk_size
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    return chunks or ["No description available."]


def quality_for_preferences(q_data: dict, preferred_codec: str | None = None, preferred_container: str | None = None) -> dict:
    """Returns the stream variant and combined size used by both UI and downloads."""
    variants = q_data.get("formats_by_codec") or {}
    preferred_key = str(preferred_codec or "").upper()
    selected = variants.get(preferred_key) or q_data

    # If preferred codec exists in variants, use its size; otherwise use the
    # smallest available variant (which is what yt-dlp will actually download
    # as fallback), so the displayed size matches the real download.
    if preferred_key and preferred_key in variants:
        selected_size = variants[preferred_key]
    elif variants:
        selected_size = min(variants.values(), key=lambda v: int(v.get("total_size") or 0))
    else:
        selected_size = selected

    if selected.get("is_muxed"):
        return selected_size

    container = str(preferred_container or "MKV").upper()
    video_size = int(selected_size.get("video_size") or 0)
    audio_sizes = selected_size.get("audio_sizes") or {}
    calc_size = video_size + int(audio_sizes.get(container) or audio_sizes.get("MKV") or 0)
    total_size = calc_size if calc_size > 0 else int(selected_size.get("total_size") or 0)
    return {**selected_size, "total_size": total_size}


def build_metadata_caption(
    metadata: dict,
    qualities: dict,
    preferred_codec: str | None = None,
    preferred_container: str | None = None,
) -> str:
    """Renders the rich metadata text card matching the user's screenshots."""
    channel = str(metadata.get("channel_name", "YouTube"))
    subs = str(metadata.get("subscribers", "Official"))
    title = str(metadata.get("title", "Video"))
    date = str(metadata.get("upload_date", "N/A"))
    duration = str(metadata.get("duration_str", "N/A"))
    views = str(metadata.get("views", "N/A"))
    likes = str(metadata.get("likes", "N/A"))

    channel_tag = make_hashtag(channel)
    title_tag = make_hashtag(title[:25])

    sorted_q = sorted(
        qualities.items(),
        key=lambda item: int(item[0].replace("p", "")) if item[0].replace("p", "").isdigit() else 0
    )

    format_lines = []

    # Video formats section
    format_lines.append("📸 Video Formats:")
    for q_name, q_data in sorted_q:
        display_data = quality_for_preferences(q_data, preferred_codec, preferred_container)
        sz_str = format_bytes_human(display_data.get("total_size", 0))
        w = display_data.get("width", 0)
        h = display_data.get("height", 0)
        dim_str = f" ({w}x{h})" if w and h else ""
        format_lines.append(f"📹 {q_name}: {sz_str}{dim_str}")

    # Audio formats section
    audio_formats = metadata.get("audio_formats", [])
    if audio_formats:
        format_lines.append("")
        format_lines.append("🎵 Audio Formats:")
        for af in audio_formats:
            abr = af.get("bitrate", 0)
            sz = af.get("size", 0)
            sz_str = format_bytes_human(sz) if sz else "N/A"
            ext = af.get("ext", "")
            codec_label = "AAC" if af.get("is_m4a") else ext.upper()
            format_lines.append(f"🎧 {abr}kbps ({codec_label}): {sz_str}")

    safe_channel = html.escape(channel)
    safe_subs = html.escape(subs)
    safe_title = html.escape(title)
    safe_date = html.escape(date)
    safe_duration = html.escape(duration)
    safe_views = html.escape(views)
    safe_likes = html.escape(likes)

    def render(lines: list[str], include_tags: bool = True) -> str:
        format_block = "\n".join(lines) if lines else "📹 Standard Quality Available"
        tags = f"\n\n#youtube {channel_tag} {title_tag}" if include_tags else ""
        return (
            f"📺 <b>{safe_channel}</b>\n"
            f"👥 <b>{safe_subs}</b>\n\n"
            f"<b>{safe_title}</b>\n"
            f"📅 {safe_date}  ⏱️ {safe_duration}\n"
            f"👁️ {safe_views}  ❤️ {safe_likes}\n\n"
            f"<pre>{html.escape(format_block)}</pre>{tags}"
        )

    caption = render(format_lines)
    while len(caption) > 1020 and len(format_lines) > 1:
        format_lines.pop()
        caption = render(format_lines)
    if len(caption) > 1020:
        caption = render(format_lines, include_tags=False)
    return caption


def build_playlist_caption(playlist_data: dict) -> str:
    """Build a clean, lightweight playlist caption matching the user's screenshot."""
    title = str(playlist_data.get("title", "YouTube Playlist"))
    total = int(playlist_data.get("total_count", 0))
    views = playlist_data.get("views", 0)
    channel = str(playlist_data.get("channel_name", ""))
    subs = str(playlist_data.get("subscribers", ""))

    views_str = f"{views:,}" if views else "🔒 Unavailable (Mix/Radio playlist)"

    lines = [
        "📑 <b>YouTube Playlist Detected</b>",
        "",
        f"<b>{html.escape(title)}</b>",
        "",
        f"⭐️ <b>Total Videos:</b> <code>{total}</code>",
        f"👁 <b>Total Views:</b> <code>{views_str}</code>",
    ]
    if channel:
        lines.append(f"📺 <b>{html.escape(channel)}</b>")
    if subs:
        lines.append(f"👥 <b>{html.escape(subs)}</b>")

    return "\n".join(lines)


def build_main_keyboard(video_id: str, qualities: dict, preferred_codec: str | None = None, audio_formats: list | None = None, current_audio_codec: str = "M4A") -> InlineKeyboardMarkup:
    """Builds the main resolution selection keyboard matching Screenshot 3."""
    rows = []

    rows.append([
        InlineKeyboardButton("💽 CODEC", callback_data=f"menu_codec:{video_id}"),
        InlineKeyboardButton("📜 Description", callback_data=f"desc:{video_id}"),
    ])

    # Audio format buttons from metadata (GREEN / SUCCESS)
    af_list = audio_formats or []
    if af_list:
        audio_row = []
        for af in af_list:
            abr = af.get("bitrate", 0)
            if abr <= 0:
                continue
            label = current_audio_codec.upper() if current_audio_codec else "M4A"
            audio_row.append(InlineKeyboardButton(f"🎵 {abr}K ({label})", callback_data=f"dl:{video_id}:a{abr}", **_style_kwargs("success")))
            if len(audio_row) == 3:
                rows.append(audio_row)
                audio_row = []
        if audio_row:
            rows.append(audio_row)
    else:
        # Fallback: generic audio buttons (GREEN / SUCCESS)
        rows.append([
            InlineKeyboardButton("🎵 128K", callback_data=f"dl:{video_id}:a128", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 256K", callback_data=f"dl:{video_id}:a256", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 320K", callback_data=f"dl:{video_id}:a320", **_style_kwargs("success")),
        ])

    sorted_res = sorted(
        [k for k in qualities.keys() if k.replace("p", "").isdigit()],
        key=lambda x: int(x.replace("p", ""))
    )

    # Video quality buttons (BLUE / PRIMARY)
    curr_row = []
    for res in sorted_res:
        curr_row.append(InlineKeyboardButton(f"📹 {res}", callback_data=f"dl:{video_id}:{res}", **_style_kwargs("primary")))
        if len(curr_row) == 3:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)

    # Best video (BLUE / PRIMARY)
    rows.append([
        InlineKeyboardButton("📹 Best", callback_data=f"dl:{video_id}:best", **_style_kwargs("primary")),
        InlineKeyboardButton("🎛️ Other", callback_data=f"other:{video_id}"),
    ])

    rows.append([
        InlineKeyboardButton("🔗 Link", callback_data=f"link:{video_id}"),
        InlineKeyboardButton("👁️ Watch", url=f"https://poketube.fun/watch?v={video_id}"),
    ])

    rows.append([
        InlineKeyboardButton("📸 Screenshots", callback_data=f"shots:{video_id}"),
        InlineKeyboardButton("ℹ️ MediaInfo", callback_data=f"minfo:{video_id}"),
    ])

    rows.append([
        InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))
    ])

    return InlineKeyboardMarkup(rows)


def build_codec_keyboard(video_id: str, current_codec: str, current_ext: str, qualities: dict, current_audio_codec: str = "M4A", audio_formats: list | None = None) -> InlineKeyboardMarkup:
    """Builds the interactive Codec and Container selector keyboard matching Screenshot 1."""
    rows = []

    avc_mark = "✅" if current_codec == "AVC" else "☑️"
    av1_mark = "✅" if current_codec == "AV1" else "☑️"
    vp9_mark = "✅" if current_codec == "VP9" else "☑️"
    hevc_mark = "✅" if current_codec == "HEVC" else "☑️"
    # Video codec buttons (BLUE / PRIMARY)
    rows.append([
        InlineKeyboardButton(f"{avc_mark} AVC", callback_data=f"set_codec:{video_id}:AVC", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{av1_mark} AV1", callback_data=f"set_codec:{video_id}:AV1", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{vp9_mark} VP9", callback_data=f"set_codec:{video_id}:VP9", **_style_kwargs("primary")),
    ])
    all_codecs = set()
    for q in qualities.values():
        all_codecs.update((q.get("formats_by_codec") or {}).keys())
    extra_row = []
    if "HEVC" in all_codecs:
        extra_row.append(InlineKeyboardButton(f"{hevc_mark} HEVC", callback_data=f"set_codec:{video_id}:HEVC", **_style_kwargs("primary")))
    if extra_row:
        rows.append(extra_row)

    mp4_mark = "✅" if current_ext == "MP4" else "☑️"
    mkv_mark = "✅" if current_ext == "MKV" else "☑️"
    rows.append([
        InlineKeyboardButton(f"{mp4_mark} MP4", callback_data=f"set_ext:{video_id}:MP4", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{mkv_mark} MKV", callback_data=f"set_ext:{video_id}:MKV", **_style_kwargs("primary")),
    ])

    # Audio codec buttons (GREEN / SUCCESS)
    m4a_mark = "✅" if current_audio_codec == "M4A" else "☑️"
    mp3_mark = "✅" if current_audio_codec == "MP3" else "☑️"
    opus_mark = "✅" if current_audio_codec == "OPUS" else "☑️"
    rows.append([
        InlineKeyboardButton(f"{m4a_mark} M4A", callback_data=f"set_audio_codec:{video_id}:M4A", **_style_kwargs("success")),
        InlineKeyboardButton(f"{mp3_mark} MP3", callback_data=f"set_audio_codec:{video_id}:MP3", **_style_kwargs("success")),
        InlineKeyboardButton(f"{opus_mark} OPUS", callback_data=f"set_audio_codec:{video_id}:OPUS", **_style_kwargs("success")),
    ])

    # Audio format buttons from metadata (GREEN / SUCCESS)
    af_list = audio_formats or []
    if af_list:
        audio_row = []
        for af in af_list:
            abr = af.get("bitrate", 0)
            if abr <= 0:
                continue
            label = current_audio_codec.upper() if current_audio_codec else "M4A"
            audio_row.append(InlineKeyboardButton(f"🎵 {abr}K ({label})", callback_data=f"dl:{video_id}:a{abr}", **_style_kwargs("success")))
            if len(audio_row) == 3:
                rows.append(audio_row)
                audio_row = []
        if audio_row:
            rows.append(audio_row)
    else:
        rows.append([
            InlineKeyboardButton("🎵 128K", callback_data=f"dl:{video_id}:a128", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 256K", callback_data=f"dl:{video_id}:a256", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 320K", callback_data=f"dl:{video_id}:a320", **_style_kwargs("success")),
        ])

    sorted_res = sorted(
        [k for k in qualities.keys() if k.replace("p", "").isdigit()],
        key=lambda x: int(x.replace("p", ""))
    )
    curr_row = []
    for res in sorted_res:
        curr_row.append(InlineKeyboardButton(f"📹 {res}", callback_data=f"dl:{video_id}:{res}", **_style_kwargs("primary")))
        if len(curr_row) == 3:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)

    rows.append([
        InlineKeyboardButton("📹 Best", callback_data=f"dl:{video_id}:best", **_style_kwargs("primary")),
        InlineKeyboardButton("🎛️ Other", callback_data=f"other:{video_id}"),
    ])

    rows.append([
        InlineKeyboardButton("🔗 Link", callback_data=f"link:{video_id}"),
        InlineKeyboardButton("👁️ Watch", url=f"https://poketube.fun/watch?v={video_id}"),
    ])

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data=f"menu_main:{video_id}"),
        InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger")),
    ])

    return InlineKeyboardMarkup(rows)


def build_settings_keyboard(prefs: dict) -> InlineKeyboardMarkup:
    """Builds the interactive /settings panel."""
    media_pref = prefs.get("media_preference", "video")
    split_mode = prefs.get("split_mode", "streamable")
    dest_id = prefs.get("destination_chat_id")
    codec = prefs.get("codec", "AVC")
    container = prefs.get("container", "MP4")

    media_btn = "🎬 Video Media (Default)" if media_pref == "video" else "📁 Document File"
    split_btn = "🎬 Streamable Parts" if split_mode == "streamable" else "📦 Binary (.mkv.001)"
    dest_btn = f"🎯 Dest: {dest_id}" if dest_id else "🎯 Set Destination Chat"

    rows = [
        [InlineKeyboardButton(f"Delivery: {media_btn}", callback_data="toggle_setting:media_preference")],
        [InlineKeyboardButton(f"Split Mode: {split_btn}", callback_data="toggle_setting:split_mode")],
        [
            InlineKeyboardButton(f"Codec: {codec}", callback_data="toggle_setting:codec"),
            InlineKeyboardButton(f"Ext: {container}", callback_data="toggle_setting:container"),
        ],
        [InlineKeyboardButton(dest_btn, callback_data="prompt_destination")],
    ]

    if dest_id:
        rows.append([InlineKeyboardButton("🔄 Reset Destination to PM", callback_data="reset_destination")])

    rows.append([InlineKeyboardButton("✖️ Close Settings", callback_data="close_menu", **_style_kwargs("danger"))])
    return InlineKeyboardMarkup(rows)


def build_playlist_keyboard(playlist_id: str, total_count: int) -> InlineKeyboardMarkup:
    """Builds interactive options for downloading YouTube playlists."""
    rows = []
    if total_count >= 5:
        rows.append([
            InlineKeyboardButton("📥 1-5", callback_data=f"pldl:{playlist_id}:1-5"),
            InlineKeyboardButton("📥 1-10", callback_data=f"pldl:{playlist_id}:1-10"),
        ])
    if total_count > 10:
        rows.append([
            InlineKeyboardButton("📥 11-20", callback_data=f"pldl:{playlist_id}:11-20"),
        ])
    rows.append([
        InlineKeyboardButton("📥 Download All", callback_data=f"pldl:{playlist_id}:all", **_style_kwargs("primary")),
    ])
    # Custom range and selection
    rows.append([
        InlineKeyboardButton("🔢 Custom Range", callback_data=f"pl_custom:{playlist_id}"),
        InlineKeyboardButton("🎯 Pick Videos", callback_data=f"pl_pick:{playlist_id}"),
    ])
    rows.append([
        InlineKeyboardButton("✖️ Cancel", callback_data="close_menu", **_style_kwargs("danger"))
    ])
    return InlineKeyboardMarkup(rows)


def build_cookie_delete_keyboard(cookie_files: list[dict]) -> InlineKeyboardMarkup:
    """Builds a keyboard for Admins to pick which cookie file to delete."""
    rows = []
    for c in cookie_files:
        rows.append([
            InlineKeyboardButton(f"🗑️ Delete {c['filename']}", callback_data=f"del_cookie_req:{c['filename']}", **_style_kwargs("danger"))
        ])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="open_cookies")])
    return InlineKeyboardMarkup(rows)


def build_playlist_quality_keyboard(playlist_id: str, first_vid: str, qualities: dict | None = None, preferred_codec: str | None = None, total_count: int = 0, audio_formats: list | None = None, channel_name: str = "", subscribers: str = "", current_audio_codec: str = "M4A") -> InlineKeyboardMarkup:
    """Builds the quality selector keyboard for a playlist, same style as normal video.
    When qualities is None (lightweight mode), shows action buttons without pre-resolved quality buttons."""
    rows = []

    pl_tag = f"📑 {total_count} videos" if total_count else "📑 Playlist"

    rows.append([
        InlineKeyboardButton("💽 CODEC", callback_data=f"pl_codec:{playlist_id}"),
        InlineKeyboardButton("📜 Descriptions", callback_data=f"pl_desc:{playlist_id}"),
    ])

    if qualities:
        # Full mode: show quality buttons
        # Audio format buttons from metadata (GREEN / SUCCESS)
        af_list = audio_formats or []
        if af_list:
            audio_row = []
            for af in af_list:
                abr = af.get("bitrate", 0)
                if abr <= 0:
                    continue
                label = current_audio_codec.upper() if current_audio_codec else "M4A"
                audio_row.append(InlineKeyboardButton(f"🎵 {abr}K ({label})", callback_data=f"pldl_start:{playlist_id}:a{abr}", **_style_kwargs("success")))
                if len(audio_row) == 3:
                    rows.append(audio_row)
                    audio_row = []
            if audio_row:
                rows.append(audio_row)
        else:
            rows.append([
                InlineKeyboardButton("🎵 128K", callback_data=f"pldl_start:{playlist_id}:a128", **_style_kwargs("success")),
                InlineKeyboardButton("🎵 256K", callback_data=f"pldl_start:{playlist_id}:a256", **_style_kwargs("success")),
                InlineKeyboardButton("🎵 320K", callback_data=f"pldl_start:{playlist_id}:a320", **_style_kwargs("success")),
            ])

        sorted_res = sorted(
            [k for k in qualities.keys() if k.replace("p", "").isdigit()],
            key=lambda x: int(x.replace("p", ""))
        )

        # Video quality buttons (BLUE / PRIMARY)
        curr_row = []
        for res in sorted_res:
            curr_row.append(InlineKeyboardButton(f"📹 {res}", callback_data=f"pldl_start:{playlist_id}:{res}", **_style_kwargs("primary")))
            if len(curr_row) == 3:
                rows.append(curr_row)
                curr_row = []
        if curr_row:
            rows.append(curr_row)

        # Best video (BLUE / PRIMARY)
        rows.append([
            InlineKeyboardButton("📹 Best", callback_data=f"pldl_start:{playlist_id}:best", **_style_kwargs("primary")),
        ])
    else:
        # Lightweight mode: show generic action buttons without pre-resolved qualities
        # Audio generic buttons (GREEN / SUCCESS)
        rows.append([
            InlineKeyboardButton("🎵 128K", callback_data=f"pldl_start:{playlist_id}:a128", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 256K", callback_data=f"pldl_start:{playlist_id}:a256", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 320K", callback_data=f"pldl_start:{playlist_id}:a320", **_style_kwargs("success")),
        ])
        # Video quality buttons (BLUE / PRIMARY)
        rows.append([
            InlineKeyboardButton("📹 144p", callback_data=f"pldl_start:{playlist_id}:144p", **_style_kwargs("primary")),
            InlineKeyboardButton("📹 240p", callback_data=f"pldl_start:{playlist_id}:240p", **_style_kwargs("primary")),
            InlineKeyboardButton("📹 360p", callback_data=f"pldl_start:{playlist_id}:360p", **_style_kwargs("primary")),
        ])
        rows.append([
            InlineKeyboardButton("📹 480p", callback_data=f"pldl_start:{playlist_id}:480p", **_style_kwargs("primary")),
            InlineKeyboardButton("📹 720p", callback_data=f"pldl_start:{playlist_id}:720p", **_style_kwargs("primary")),
            InlineKeyboardButton("📹 1080p", callback_data=f"pldl_start:{playlist_id}:1080p", **_style_kwargs("primary")),
        ])
        rows.append([
            InlineKeyboardButton("📹 1440p", callback_data=f"pldl_start:{playlist_id}:1440p", **_style_kwargs("primary")),
            InlineKeyboardButton("📹 2160p", callback_data=f"pldl_start:{playlist_id}:2160p", **_style_kwargs("primary")),
        ])
        rows.append([
            InlineKeyboardButton("📹 Best", callback_data=f"pldl_start:{playlist_id}:best", **_style_kwargs("primary")),
        ])

    rows.append([
        InlineKeyboardButton("🔗 Link", callback_data=f"link:{first_vid}"),
        InlineKeyboardButton("👁️ Watch", url=f"https://poketube.fun/watch?v={first_vid}"),
    ])

    rows.append([
        InlineKeyboardButton("ℹ️ MediaInfo", callback_data=f"minfo:{first_vid}"),
    ])

    rows.append([
        InlineKeyboardButton(f"✖️ Cancel ({pl_tag})", callback_data="close_menu", **_style_kwargs("danger"))
    ])

    return InlineKeyboardMarkup(rows)


def build_playlist_codec_keyboard(playlist_id: str, first_vid: str, current_codec: str, current_ext: str, qualities: dict, total_count: int = 0, current_audio_codec: str = "M4A", audio_formats: list | None = None) -> InlineKeyboardMarkup:
    """Builds the codec selector keyboard for a playlist."""
    rows = []

    avc_mark = "✅" if current_codec == "AVC" else "☑️"
    av1_mark = "✅" if current_codec == "AV1" else "☑️"
    vp9_mark = "✅" if current_codec == "VP9" else "☑️"
    hevc_mark = "✅" if current_codec == "HEVC" else "☑️"
    # Video codec buttons (BLUE / PRIMARY)
    rows.append([
        InlineKeyboardButton(f"{avc_mark} AVC", callback_data=f"pl_set_codec:{playlist_id}:AVC", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{av1_mark} AV1", callback_data=f"pl_set_codec:{playlist_id}:AV1", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{vp9_mark} VP9", callback_data=f"pl_set_codec:{playlist_id}:VP9", **_style_kwargs("primary")),
    ])
    all_codecs = set()
    for q in qualities.values():
        all_codecs.update((q.get("formats_by_codec") or {}).keys())
    extra_row = []
    if "HEVC" in all_codecs:
        extra_row.append(InlineKeyboardButton(f"{hevc_mark} HEVC", callback_data=f"pl_set_codec:{playlist_id}:HEVC", **_style_kwargs("primary")))
    if extra_row:
        rows.append(extra_row)

    mp4_mark = "✅" if current_ext == "MP4" else "☑️"
    mkv_mark = "✅" if current_ext == "MKV" else "☑️"
    rows.append([
        InlineKeyboardButton(f"{mp4_mark} MP4", callback_data=f"pl_set_ext:{playlist_id}:MP4", **_style_kwargs("primary")),
        InlineKeyboardButton(f"{mkv_mark} MKV", callback_data=f"pl_set_ext:{playlist_id}:MKV", **_style_kwargs("primary")),
    ])

    # Audio codec buttons (GREEN / SUCCESS)
    m4a_mark = "✅" if current_audio_codec == "M4A" else "☑️"
    mp3_mark = "✅" if current_audio_codec == "MP3" else "☑️"
    opus_mark = "✅" if current_audio_codec == "OPUS" else "☑️"
    rows.append([
        InlineKeyboardButton(f"{m4a_mark} M4A", callback_data=f"pl_set_audio_codec:{playlist_id}:M4A", **_style_kwargs("success")),
        InlineKeyboardButton(f"{mp3_mark} MP3", callback_data=f"pl_set_audio_codec:{playlist_id}:MP3", **_style_kwargs("success")),
        InlineKeyboardButton(f"{opus_mark} OPUS", callback_data=f"pl_set_audio_codec:{playlist_id}:OPUS", **_style_kwargs("success")),
    ])

    # Audio format buttons from metadata (GREEN / SUCCESS)
    af_list = audio_formats or []
    if af_list:
        audio_row = []
        for af in af_list:
            abr = af.get("bitrate", 0)
            if abr <= 0:
                continue
            label = current_audio_codec.upper() if current_audio_codec else "M4A"
            audio_row.append(InlineKeyboardButton(f"🎵 {abr}K ({label})", callback_data=f"pldl_start:{playlist_id}:a{abr}", **_style_kwargs("success")))
            if len(audio_row) == 3:
                rows.append(audio_row)
                audio_row = []
        if audio_row:
            rows.append(audio_row)
    else:
        rows.append([
            InlineKeyboardButton("🎵 128K", callback_data=f"pldl_start:{playlist_id}:a128", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 256K", callback_data=f"pldl_start:{playlist_id}:a256", **_style_kwargs("success")),
            InlineKeyboardButton("🎵 320K", callback_data=f"pldl_start:{playlist_id}:a320", **_style_kwargs("success")),
        ])

    sorted_res = sorted(
        [k for k in qualities.keys() if k.replace("p", "").isdigit()],
        key=lambda x: int(x.replace("p", ""))
    )
    curr_row = []
    for res in sorted_res:
        curr_row.append(InlineKeyboardButton(f"📹 {res}", callback_data=f"pldl_start:{playlist_id}:{res}", **_style_kwargs("primary")))
        if len(curr_row) == 3:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)

    rows.append([
        InlineKeyboardButton("📹 Best", callback_data=f"pldl_start:{playlist_id}:best", **_style_kwargs("primary")),
    ])

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data=f"pl_back:{playlist_id}:{first_vid}"),
        InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger")),
    ])

    return InlineKeyboardMarkup(rows)


def build_playlist_download_keyboard(playlist_id: str, quality: str, total_count: int, pl_title: str = "") -> InlineKeyboardMarkup:
    """Builds the video selection keyboard shown after user picks quality for a playlist."""
    rows = []

    rows.append([
        InlineKeyboardButton(f"📹 Quality: {quality}", callback_data="noop"),
    ])

    if total_count <= 5:
        rows.append([
            InlineKeyboardButton(f"📥 Download All ({total_count})", callback_data=f"pldl_go:{playlist_id}:{quality}:all", **_style_kwargs("primary")),
        ])
    else:
        rows.append([
            InlineKeyboardButton(f"📥 1-5", callback_data=f"pldl_go:{playlist_id}:{quality}:1-5", **_style_kwargs("primary")),
            InlineKeyboardButton(f"📥 1-10", callback_data=f"pldl_go:{playlist_id}:{quality}:1-10", **_style_kwargs("primary")),
        ])
        if total_count > 10:
            rows.append([
                InlineKeyboardButton(f"📥 11-20", callback_data=f"pldl_go:{playlist_id}:{quality}:11-20", **_style_kwargs("primary")),
            ])
        rows.append([
            InlineKeyboardButton(f"📥 Download All ({total_count})", callback_data=f"pldl_go:{playlist_id}:{quality}:all", **_style_kwargs("primary")),
        ])

    rows.append([
        InlineKeyboardButton("🔢 Custom Range", callback_data=f"pl_custom_q:{playlist_id}:{quality}"),
        InlineKeyboardButton("🎯 Pick Videos", callback_data=f"pl_pick_q:{playlist_id}:{quality}"),
    ])
    rows.append([
        InlineKeyboardButton("✖️ Cancel", callback_data="close_menu", **_style_kwargs("danger"))
    ])
    return InlineKeyboardMarkup(rows)
