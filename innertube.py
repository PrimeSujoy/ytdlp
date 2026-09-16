# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import re
import logging
import httpx
try:
    import orjson as json_lib
except ImportError:
    import json as json_lib

logger = logging.getLogger("Innertube")

_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_URL = f"https://youtubei.googleapis.com/youtubei/v1/player?key={_KEY}"
_ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"

_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/|/v/|/live/)([A-Za-z0-9_-]{11})")
_BARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")

_client = httpx.AsyncClient(http2=True, timeout=10.0)


def extract_video_id(url_or_id: str) -> str | None:
    """Extracts 11-char YouTube Video ID from any URL format or returns bare ID."""
    if not url_or_id:
        return None
    match = _ID_RE.search(url_or_id)
    if match:
        return match.group(1)
    val = url_or_id.strip()
    return val if _BARE_ID_RE.fullmatch(val) else None


def get_thumbnail_url(video_id: str) -> str:
    """Returns the primary high-resolution maxresdefault thumbnail URL."""
    return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"


def _ladder(vid: str, visitor: str | None) -> tuple:
    """Client escalation ladder for videos refused by ANDROID client."""
    return (
        ("android_vr", {
            "clientName": "ANDROID_VR", "clientVersion": "1.65.10",
            "deviceMake": "Oculus", "deviceModel": "Quest 3",
            "androidSdkVersion": 32, "osName": "Android", "osVersion": "12L",
            "hl": "en", "gl": "US", "visitorData": visitor,
        }, {"X-Youtube-Client-Name": "28"}, None),
        ("web_embedded", {
            "clientName": "WEB_EMBEDDED_PLAYER", "clientVersion": "1.20260115.01.00",
            "hl": "en", "gl": "US", "visitorData": visitor,
        }, {"X-Youtube-Client-Name": "56"},
            {"thirdParty": {"embedUrl": f"https://www.youtube.com/watch?v={vid}"}}),
    )


async def _post_player(vid: str, client: dict, headers: dict, ctx_extra: dict | None) -> dict:
    payload = {"videoId": vid, "context": {"client": client, **(ctx_extra or {})}}
    raw = json_lib.dumps(payload)
    body = raw.encode("utf-8") if isinstance(raw, str) else raw
    resp = await _client.post(
        _URL,
        content=body,
        headers={"Content-Type": "application/json", "User-Agent": _ANDROID_UA, **headers}
    )
    resp.raise_for_status()
    return json_lib.loads(resp.content)


async def fetch_player_data(url_or_id: str) -> dict | None:
    """Queries Innertube API using ANDROID client with fallback ladder (~0.15s)."""
    vid = extract_video_id(url_or_id)
    if not vid:
        return None

    android = {
        "clientName": "ANDROID", "clientVersion": "20.10.38",
        "androidSdkVersion": 30, "hl": "en", "gl": "US"
    }
    try:
        data = await _post_player(vid, android, {"X-Youtube-Client-Name": "3"}, None)
        if (data.get("playabilityStatus") or {}).get("status") == "OK":
            return data
    except Exception as e:
        logger.warning(f"[Innertube] Android client error for {vid}: {e}")

    # Ladder fallback
    visitor = None
    if 'data' in locals() and isinstance(data, dict):
        visitor = (data.get("responseContext") or {}).get("visitorData")

    for label, client_cfg, headers, ctx_extra in _ladder(vid, visitor):
        try:
            alt = await _post_player(vid, client_cfg, headers, ctx_extra)
            if (alt.get("playabilityStatus") or {}).get("status") == "OK":
                logger.info(f"[Innertube] {vid} recovered via {label}")
                return alt
        except Exception:
            continue

    logger.info(f"[Innertube] {vid} not playable anonymously — falling back to yt-dlp")
    return None


def parse_metadata(player_data: dict, vid: str) -> dict:
    """Parses videoDetails and microformats into a clean metadata structure."""
    vd = player_data.get("videoDetails", {})
    mf = (player_data.get("microformat", {})).get("playerMicroformatRenderer", {})

    duration_sec = int(vd.get("lengthSeconds") or 0)
    mins, secs = divmod(duration_sec, 60)
    hours, mins = divmod(mins, 60)
    dur_str = f"{hours}h, {mins}m, {secs}s" if hours else f"{mins}m, {secs}s"

    upload_date = mf.get("uploadDate", "")
    if upload_date and len(upload_date) >= 10:
        parts = upload_date[:10].split("-")
        upload_date = f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else upload_date

    return {
        "video_id": vid,
        "title": vd.get("title", ""),
        "channel_name": vd.get("author", ""),
        "subscribers": "Official",
        "upload_date": upload_date or "N/A",
        "duration_sec": duration_sec,
        "duration_str": dur_str,
        "views": f"{int(vd.get('viewCount') or 0):,}",
        "likes": "N/A",
        "description": vd.get("shortDescription", ""),
        "thumbnail": get_thumbnail_url(vid),
    }


_PLAYLIST_URL = f"https://youtubei.googleapis.com/youtubei/v1/browse?key={_KEY}"


async def get_playlist_info(playlist_id: str) -> dict | None:
    """Lightweight Innertube playlist extraction — returns title, count, views, channel, subs, thumbnail, entries.
    Does NOT extract per-video formats or metadata."""
    try:
        payload = {
            "context": {"client": {"clientName": "WEB", "clientVersion": "2.20260115.01.00", "hl": "en", "gl": "US"}},
            "browseId": "VL" + playlist_id if not playlist_id.startswith("VL") else playlist_id,
        }
        raw = json_lib.dumps(payload)
        body = raw.encode("utf-8") if isinstance(raw, str) else raw
        resp = await _client.post(
            _PLAYLIST_URL,
            content=body,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = json_lib.loads(resp.content)

        import re as _re

        title = ""
        total_count = 0
        views = 0
        channel_name = ""
        subs = ""
        thumbnail = ""
        entries = []

        # ── NEW API: pageHeaderRenderer + contentMetadataViewModel ──
        phr = (data.get("header") or {}).get("pageHeaderRenderer") or {}
        page_vm = (phr.get("content") or {}).get("pageHeaderViewModel") or {}
        if page_vm:
            title = page_vm.get("title", {}).get("dynamicTextViewModel", {}).get("text", {}).get("content", "")
            meta_vm = page_vm.get("metadata", {}).get("contentMetadataViewModel", {})
            for row in meta_vm.get("metadataRows", []):
                for part in row.get("metadataParts", []):
                    txt = part.get("text", {}).get("content", "")
                    if not txt:
                        continue
                    if "video" in txt.lower():
                        nums = _re.sub(r"[^\d]", "", txt)
                        if nums:
                            total_count = int(nums)
                    if "view" in txt.lower():
                        nums = _re.sub(r"[^\d]", "", txt)
                        if nums:
                            views = int(nums)

        # ── OLD API: playlistHeaderRenderer (fallback) ──
        header = (data.get("header") or {}).get("playlistHeaderRenderer") or {}
        if header:
            if not title:
                title = header.get("title", "")
            if not total_count:
                total_count = int(header.get("numVideosText", {}).get("runs", [{}])[-1].get("text", "0") or 0)
            if not total_count:
                total_count = int(header.get("videoCount", "0") or 0)
            if not views:
                views_text = header.get("viewCountText", {}).get("simpleText", "")
                if not views_text:
                    vr = header.get("viewCountText", {}).get("runs", [])
                    if vr:
                        views_text = "".join(r.get("text", "") for r in vr)
                if views_text:
                    nums = _re.sub(r"[^\d]", "", views_text)
                    if nums:
                        views = int(nums)
            if not channel_name:
                channel_name = header.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
            if not subs:
                subs = header.get("subscriberCountText", {}).get("simpleText", "")
            if not thumbnail:
                thumbs = header.get("thumbnail", {}).get("thumbnails", [])
                if thumbs:
                    thumbnail = thumbs[-1].get("url", "")

        # ── SIDEBAR (top-level): channel info + views ──
        sidebar = (data.get("sidebar") or {}).get("playlistSidebarRenderer") or {}
        for si in sidebar.get("items", []):
            primary = si.get("playlistSidebarPrimaryInfoRenderer") or {}
            if primary and not views:
                for stat in primary.get("stats", []):
                    stxt = stat.get("simpleText", "")
                    if not stxt:
                        stxt = "".join(r.get("text", "") for r in stat.get("runs", []))
                    if "view" in stxt.lower():
                        nums = _re.sub(r"[^\d]", "", stxt)
                        if nums:
                            views = int(nums)
                    if "video" in stxt.lower() and not total_count:
                        nums = _re.sub(r"[^\d]", "", stxt)
                        if nums:
                            total_count = int(nums)
            secondary = si.get("playlistSidebarSecondaryInfoRenderer") or {}
            if secondary:
                owner = secondary.get("videoOwner", {}).get("videoOwnerRenderer", {})
                if not channel_name:
                    channel_name = owner.get("title", {}).get("runs", [{}])[0].get("text", "")
                if not subs:
                    subs = owner.get("subscriberCountText", {}).get("simpleText", "")

        # ── VIDEO ENTRIES: lockupViewModel (new) or playlistVideoRenderer (old) ──
        contents = (data.get("contents") or {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
        for tab in contents:
            tab_content = (tab.get("tabRenderer") or {}).get("content", {})
            section = tab_content.get("sectionListRenderer", {}).get("contents", [])
            for sec in section:
                items = (sec.get("itemSectionRenderer") or {}).get("contents", [])
                for item in items:
                    lockup = item.get("lockupViewModel")
                    if lockup and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                        vid_id = lockup.get("contentId", "")
                        meta_vm = (lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                        vid_title = meta_vm.get("title", {}).get("content", "")
                        if vid_id:
                            entries.append({
                                "id": vid_id,
                                "url": f"https://youtu.be/{vid_id}",
                                "title": vid_title,
                                "duration": "",
                            })
                    pl_contents = (item.get("playlistVideoListRenderer") or {}).get("contents", [])
                    for pv in pl_contents:
                        vid_renderer = pv.get("playlistVideoRenderer")
                        if vid_renderer:
                            vid_id = vid_renderer.get("videoId", "")
                            vid_title = vid_renderer.get("title", {}).get("runs", [{}])[0].get("text", "")
                            vid_duration = vid_renderer.get("lengthSeconds", "")
                            index_data = vid_renderer.get("index") or {}
                            index_text = index_data.get("simpleText") or "".join(
                                run.get("text", "") for run in index_data.get("runs", [])
                            )
                            if vid_id:
                                entries.append({
                                    "id": vid_id,
                                    "url": f"https://youtu.be/{vid_id}",
                                    "title": vid_title,
                                    "duration": vid_duration,
                                    "playlist_index": int(index_text) if index_text.isdigit() else len(entries) + 1,
                                })

        if not total_count:
            total_count = len(entries)

        if not thumbnail:
            first_id = entries[0]["id"] if entries else ""
            if first_id:
                thumbnail = f"https://i.ytimg.com/vi/{first_id}/hqdefault.jpg"

        return {
            "title": title,
            "total_count": total_count,
            "views": views,
            "channel_name": channel_name,
            "subscribers": subs,
            "thumbnail": thumbnail,
            "entries": entries,
        }
    except Exception as e:
        logger.error(f"Innertube playlist extraction error: {e}")
        return None


def parse_available_formats(streaming_data: dict, duration_sec: int) -> dict:
    """
    Extracts all available video resolutions (144p to 4K/8K) and audio streams,
    pre-calculating size and codec mapping.
    """
    adaptive = streaming_data.get("adaptiveFormats", [])
    muxed = streaming_data.get("formats", [])

    qualities = {}
    audio_formats = []

    # Best audio size estimation
    best_audio_size = 0
    best_audio_url = None
    for f in adaptive:
        mime = f.get("mimeType", "")
        if mime.startswith("audio/"):
            audio_formats.append(f)
            size = int(f.get("contentLength") or 0)
            if not size and f.get("bitrate") and duration_sec:
                size = int((f["bitrate"] * duration_sec) / 8)
            if size > best_audio_size:
                best_audio_size = size
                best_audio_url = f.get("url")

    # If no separate adaptive audio, estimate ~128kbps audio
    if not best_audio_size and duration_sec:
        best_audio_size = int((128000 * duration_sec) / 8)

    # Process all video formats
    all_video_formats = adaptive + muxed
    for f in all_video_formats:
        height = f.get("height")
        if not height:
            continue

        q_key = f"{height}p"
        size = int(f.get("contentLength") or 0)
        if not size and f.get("bitrate") and duration_sec:
            size = int((f["bitrate"] * duration_sec) / 8)

        # If it's a video-only stream, add audio size
        mime = f.get("mimeType", "")
        is_video_only = "audio/" not in mime
        total_size = (size + best_audio_size) if is_video_only else size

        width = f.get("width", 0)
        fps = f.get("fps", 30)

        # Codec detection
        codec_name = "AVC"
        if "av01" in mime or "av1" in mime:
            codec_name = "AV1"
        entry = {
            "height": height,
            "width": width,
            "fps": fps,
            "codec": codec_name,
            "video_size": size,
            "total_size": total_size,
            "audio_sizes": {"MP4": best_audio_size, "MKV": best_audio_size},
            "url": f.get("url"),
            "itag": f.get("itag"),
            "is_muxed": not is_video_only,
        }

        if q_key not in qualities:
            qualities[q_key] = {
                **entry,
                "formats_by_codec": {codec_name: entry},
            }
        else:
            qualities[q_key]["formats_by_codec"][codec_name] = entry
            if total_size > qualities[q_key]["total_size"]:
                f_map = qualities[q_key]["formats_by_codec"]
                qualities[q_key] = {**entry, "formats_by_codec": f_map}

    return {
        "qualities": qualities,
        "best_audio_size": best_audio_size,
        "best_audio_url": best_audio_url,
    }
