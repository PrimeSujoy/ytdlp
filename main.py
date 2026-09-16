# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import sys

# Ensure UTF-8 console output on Windows to prevent charmap UnicodeEncodeError on emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import re
import html
import uuid
import time
import shutil
import logging
import asyncio
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import FloodWait, MessageNotModified

from config import Config, Emojis
from database import cache_quality_candidates, db
from playlist_order import preserve_playlist_positions
from bot_lifecycle import check_update, git_command, stop_children, replace_bot
from force_sub import check_forcesub, get_forcesub_keyboard, handle_chat_member_updated
from queue_manager import queue_manager
from destination import verify_destination_chat, get_target_upload_chat
from innertube import (
    extract_video_id,
    fetch_player_data,
    parse_metadata,
    parse_available_formats,
    get_thumbnail_url,
    get_playlist_info,
)
from cookie_manager import cookie_manager
from ytdlp_runner import extract_info_via_ytdlp, download_media, parse_ytdlp_info, build_quality_selector
from media_processor import split_streamable_video, split_binary_file, get_media_profile
from mediainfo_helper import (
    generate_screenshots,
    extract_mediainfo,
    upload_to_pastyx,
    select_screenshot_source,
    build_all_qualities_mediainfo,
)
from thumbnail_helper import prepare_thumbnail
from delivery_ui import build_delivery_caption
from ui_builder import (
    build_metadata_caption,
    build_playlist_caption,
    build_main_keyboard,
    build_codec_keyboard,
    build_settings_keyboard,
    build_playlist_keyboard,
    build_cookie_delete_keyboard,
    build_playlist_quality_keyboard,
    build_playlist_codec_keyboard,
    build_playlist_download_keyboard,
    quality_for_preferences,
    split_description,
    format_bytes_human,
    _style_kwargs,
)
from progress import ProgressTracker, progress_dashboard
from speedtest_helper import (
    SpeedtestBusyError,
    SpeedtestCooldownError,
    SpeedtestError,
    download_speedtest_image,
    speedtest_service,
)
from uploader import uploader, flood_retry

# Logging Setup — write to both console AND files so /logs command works
_log_dir = os.path.dirname(os.path.abspath(__file__))
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)

# Console handler (default)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s"))
_root_logger.addHandler(_console_handler)

# File handler — rotates to prevent unlimited growth
try:
    from logging.handlers import RotatingFileHandler
    _error_file_handler = RotatingFileHandler(
        os.path.join(_log_dir, "bot_error.log"),
        maxBytes=5 * 1024 * 1024,  # 5MB max
        backupCount=2,
        encoding="utf-8",
    )
    _error_file_handler.setLevel(logging.WARNING)  # warnings + errors → bot_error.log
    _error_file_handler.setFormatter(logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s"))
    _root_logger.addHandler(_error_file_handler)

    _output_file_handler = RotatingFileHandler(
        os.path.join(_log_dir, "bot_output.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    _output_file_handler.setLevel(logging.INFO)  # all info → bot_output.log
    _output_file_handler.setFormatter(logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s"))
    _root_logger.addHandler(_output_file_handler)
except Exception as _log_err:
    print(f"Warning: Could not setup file logging: {_log_err}")

logger = logging.getLogger("MainBot")
BOT_STARTED_AT = time.time()


def _parse_playlist_range(text: str, total: int) -> list[int]:
    """Parse playlist video selection. Returns 0-based indices.
    Supports: 'all', '3', '1-5', '1,3,7', '1,3-5,7,10-12'
    Raises ValueError with user-friendly message on invalid input."""
    text = text.strip()
    if text.lower() == "all":
        return list(range(total))

    selected = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-", 1)
            try:
                start = int(pieces[0].strip())
                end = int(pieces[1].strip())
            except ValueError:
                raise ValueError(f"Invalid range: <code>{part}</code>")
            if start < 1 or end < 1:
                raise ValueError(f"Numbers must be ≥ 1: <code>{part}</code>")
            if start > total or end > total:
                raise ValueError(f"Number exceeds total ({total}): <code>{part}</code>")
            if start > end:
                raise ValueError(f"Start ({start}) > end ({end}): <code>{part}</code>")
            selected.update(range(start - 1, end))
        else:
            try:
                num = int(part)
            except ValueError:
                raise ValueError(f"Invalid number: <code>{part}</code>")
            if num < 1:
                raise ValueError(f"Number must be ≥ 1: <code>{part}</code>")
            if num > total:
                raise ValueError(f"Number {num} exceeds total ({total}) videos.")
            selected.add(num - 1)

    return sorted(selected)


os.makedirs(Config.DATA_DIR, exist_ok=True)

def _cleanup_session_files():
    """Delete ALL session files on startup for a fresh session every restart.
    Prevents session bloat across all hosting platforms (HF, VPS, Koyeb, Heroku, localhost).
    Bot re-authenticates on each restart (~2-3s overhead) but disk stays clean."""
    _SESSION_PREFIX = os.path.join(Config.DATA_DIR, "advanced_ytdlp_bot")
    _SESSION_EXTENSIONS = (
        "", "-journal", "-shm", "-wal",
        ".session", ".session-journal", ".session-shm", ".session-wal",
    )
    _cleaned = 0
    for _ext in _SESSION_EXTENSIONS:
        _p = _SESSION_PREFIX + _ext
        if os.path.isfile(_p):
            try:
                _size_kb = os.path.getsize(_p) / 1024
                os.remove(_p)
                _cleaned += 1
                if _size_kb > 100:
                    logger.info(f"🧹 Removed large session file: {os.path.basename(_p)} ({_size_kb:.0f} KB)")
            except OSError:
                pass
    if _cleaned:
        logger.info(f"🧹 Session cleanup: {_cleaned} files removed, fresh session will be generated")

_cleanup_session_files()

_TOKEN_FILE = os.path.join(Config.DATA_DIR, ".last_bot_token")
_current_token = Config.BOT_TOKEN.strip()
_session_valid = False
if os.path.isfile(_TOKEN_FILE):
    try:
        with open(_TOKEN_FILE, "r") as _f:
            _last_token = _f.read().strip()
        if _last_token == _current_token:
            _session_valid = True
    except OSError:
        pass

if not _session_valid:
    try:
        with open(_TOKEN_FILE, "w") as _f:
            _f.write(_current_token)
    except OSError:
        pass

app = Client(
    os.path.join(Config.DATA_DIR, "advanced_ytdlp_bot"),
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    no_updates=False,
    skip_updates=True,
    auto_no_updates=False,
)

_restart_event = asyncio.Event()
_restart_request = None
_restart_lock = asyncio.Lock()


@app.on_message(filters.command(["restart", "update"]) & filters.private)
async def restart_command(client, message):
    global _restart_request
    user = message.from_user
    if not user or (user.id != Config.OWNER_ID and user.id not in Config.ADMIN_ID):
        await message.reply_text("⛔ Owner/admin only.")
        return
    async with _restart_lock:
        if _restart_request:
            await message.reply_text("⏳ Restart/update already in progress.")
            return
        update = message.command[0].lower() == "update"
        if update:
            try:
                await check_update(os.path.dirname(os.path.abspath(__file__)))
            except Exception as exc:
                await message.reply_text(html.escape(str(exc)), parse_mode=enums.ParseMode.HTML)
                return
        await message.reply_text("🔄 Stopping all tasks; " + ("pulling updates and restarting..." if update else "restarting bot..."))
        _restart_request = (message.chat.id, update)
        _restart_event.set()


# =============================================================================
# Command: /c{task_id} — Cancel a task (admin/owner OR task owner only)
# =============================================================================
@app.on_message(filters.regex(r"^/c\s?[0-9a-fA-F]{8}\b") & (filters.private | filters.group))
async def cancel_task_command(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id

    # Extract task_id from /c153e8551 or /c 153e8551
    parts = (message.text or "").split()
    task_id = ""
    if len(parts) >= 2:
        task_id = parts[1].strip()
    elif len(parts) == 1 and len(parts[0]) > 2:
        # /c153e8551 format
        task_id = parts[0][2:].strip()

    if not task_id or len(task_id) != 8:
        await message.reply_text(
            "❌ <b>Invalid format.</b>\n\nUsage: <code>/c153e8551</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # Check if task exists
    tracker = _active_trackers.get(task_id)
    task_entry = queue_manager._cancel_registry.get(task_id)
    if not tracker and not task_entry:
        await message.reply_text(
            "❌ <b>Task not found.</b> It may have already completed or been cancelled.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # Permission check: admin/owner OR task owner
    is_admin = user_id in Config.ADMIN_ID or user_id == Config.OWNER_ID
    task_owner = (task_entry or {}).get("user_id", 0) if task_entry else (tracker.user_id if tracker else 0)
    is_task_owner = (user_id == task_owner)

    if not is_admin and not is_task_owner:
        await message.reply_text(
            "⛔ <b>Access Denied.</b>\n\nOnly the task owner or an admin can cancel this task.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # Cancel the task
    cancelled = await queue_manager.cancel_task(task_id)
    cancel_msg = (
        f"🛑 <b>Task Cancelled</b>\n"
        f"<code>Task ID:</code> <code>{task_id}</code>\n"
        f"<code>Cancelled by:</code> <a href=\"tg://user?id={user_id}\">{html.escape(user.first_name or 'User')}</a>"
    )
    if tracker and tracker.dashboard:
        try:
            await message.reply_text(cancel_msg, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
    elif tracker:
        await tracker.show_terminal(cancel_msg)

# Active user state for destination chat text prompt
_user_waiting_dest: dict[int, int] = {}
# Active user state for adding secondary DB URI
_user_waiting_db_uri: set[int] = set()
# Active format cache per video_id: {vid: {"metadata": dict, "formats": dict, "url": str}}
_video_cache: dict[str, dict] = {}
# Active progress trackers for Pause/Resume controls: {task_id: ProgressTracker}
_active_trackers: dict[str, ProgressTracker] = {}
# Prevent repeated screenshot taps from starting duplicate FFmpeg batches.
_active_screenshot_jobs: set[tuple[int, str]] = set()
# Retry tokens: {token: {"user_id", "url", "quality", "codec", "container", "mode", "created_at"}}
_retry_tokens: dict[str, dict] = {}
_RETRY_TOKEN_TTL = 900  # 15 minutes
# Playlist cache: {pl_id: {"title": str, "entries": list, "url": str}}
_playlist_cache: dict[str, dict] = {}
# Playlist quality cache: {pl_id: {"first_vid": str, "metadata": dict, "qualities": dict, "url": str}}
_playlist_quality_cache: dict[str, dict] = {}
# Playlist selected quality: {pl_id: str} — stores the quality user picked
_playlist_selected_quality: dict[str, str] = {}
# User state for custom playlist input: {user_id: {"pl_id", "total", "mode": "range"|"pick"}}
_user_playlist_input: dict[int, dict] = {}
# /queue anti-spam: {user_id: {"msg_id": int, "count": int, "last_ts": float}}
_queue_spam_tracker: dict[int, dict] = {}
_QUEUE_SPAM_WINDOW = 5.0  # seconds

_refresh_cooldown: dict[int, float] = {}
_REFRESH_COOLDOWN_SECONDS = 10


def _create_retry_token(user_id: int, url: str, quality: str, codec: str, container: str, mode: str) -> str:
    """Create a short-lived retry token for failed downloads."""
    import secrets
    token = secrets.token_hex(4)
    _retry_tokens[token] = {
        "user_id": user_id,
        "url": url,
        "quality": quality,
        "codec": codec,
        "container": container,
        "mode": mode,
        "created_at": time.time(),
    }
    return token


def _consume_retry_token(token: str, user_id: int) -> dict | None:
    """Consume a retry token if valid and not expired. Returns the stored params or None."""
    data = _retry_tokens.pop(token, None)
    if not data:
        return None
    if data["user_id"] != user_id:
        return None
    if time.time() - data["created_at"] > _RETRY_TOKEN_TTL:
        return None
    return data


# Cleanup expired retry tokens periodically
async def _cleanup_retry_tokens():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [t for t, d in _retry_tokens.items() if now - d["created_at"] > _RETRY_TOKEN_TTL]
        for t in expired:
            _retry_tokens.pop(t, None)


# =============================================================================
# Bot Lifecycle & Real-Time ForceSub Listener
# =============================================================================
@app.on_chat_member_updated()
async def chat_member_updated_handler(client: Client, update):
    await handle_chat_member_updated(client, update)


@app.on_message(group=-1)
async def global_message_logger(client: Client, message: Message):
    user = message.from_user
    uid = user.id if user else "Unknown"
    uname = f"@{user.username}" if user and user.username else "NoUser"
    txt = (message.text or message.caption or "<media>")[:60]
    logger.info(f"📩 [INCOMING] From {uid} ({uname}) in {message.chat.type}: {txt}")


# =============================================================================
# Command: /start
# =============================================================================
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if await _check_banned_user(message):
        return

    logger.info(f"Received /start from {user.id} (@{user.username or 'NoUser'})")
    try:
        await db.add_or_update_user(user.id, user.username or "", user.first_name or "")

        # ForceSub Check
        joined, missing = await check_forcesub(client, user.id)
        if not joined:
            await message.reply_text(
                "⚠️ <b>Access Restricted!</b>\n\nPlease join our required update channels to use the bot:",
                reply_markup=get_forcesub_keyboard(missing),
                parse_mode=enums.ParseMode.HTML,
            )
            return

        import html
        safe_name = html.escape(user.first_name or "User")
        crown = Emojis.tag(Emojis.CROWN, "👑")
        welcome_text = (
            f"👋 <b>Welcome {safe_name}!</b>\n\n"
            f"{crown} I am the <b>Ultimate High-Speed YouTube Downloader Bot</b>.\n\n"
            f"⚡ <b>Key Features:</b>\n"
            f"• <b>Innertube Fast-Path:</b> Direct ~0.15s stream resolution.\n"
            f"• <b>Dump Channel Cache:</b> Instant millisecond delivery for cached videos.\n"
            f"• <b>Full Resolutions:</b> 144p to 4K 60FPS + MP3 Audio.\n"
            f"• <b>Dual Splitting:</b> Streamable parts & Binary (.mkv.001).\n"
            f"• <b>Screenshots & MediaInfo:</b> Sample video frames & technical specs.\n"
            f"• <b>Custom Thumbnail:</b> Save your own thumbnail with /setthumb.\n"
            f"• <b>Multi-Cookie Pool:</b> Cookie rotation & anti-ban protection.\n"
            f"• <b>Custom Destination:</b> Upload straight to your channel/group.\n\n"
            f"👇 <b>Send any YouTube link or playlist to get started!</b>"
        )
        await message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Settings", callback_data="open_settings")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="open_leaderboard"),
                 InlineKeyboardButton("📜 History", callback_data="open_history")],
                *([[InlineKeyboardButton("🍪 Cookies", callback_data="open_cookies")]] if user.id in Config.ADMIN_ID or user.id == Config.OWNER_ID else []),
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error in start_handler for {user.id}: {e}", exc_info=True)


# =============================================================================
# Custom Thumbnail System (/setthumb, /viewthumb, /delthumb)
# =============================================================================
@app.on_message(filters.command("setthumb") & filters.private)
async def setthumb_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if await _check_banned_user(message):
        return
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("📸 Please reply to an image/photo with `/setthumb` to set your custom thumbnail.")
        return

    thumb_dir = os.path.join(Config.DOWNLOAD_DIR, "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    thumb_path = os.path.join(thumb_dir, f"{user_id}.jpg")

    await message.reply_to_message.download(file_name=thumb_path)
    await db.set_custom_thumbnail(user_id, thumb_path)
    check_tag = Emojis.tag(Emojis.CHECK, "✅")
    await message.reply_text(
        f"{check_tag} <b>Custom Thumbnail Saved!</b>\n\nAll your future video downloads will use this thumbnail.",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("viewthumb") & filters.private)
async def viewthumb_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if await _check_banned_user(message):
        return
    thumb_path = await db.get_custom_thumbnail(user_id)
    if thumb_path and os.path.exists(thumb_path):
        await message.reply_photo(photo=thumb_path, caption="🖼️ <b>Your Current Custom Thumbnail</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("ℹ️ You don't have a custom thumbnail set. Bot uses YouTube's original thumbnail.")


@app.on_message(filters.command("delthumb") & filters.private)
async def delthumb_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if await _check_banned_user(message):
        return
    thumb_path = await db.get_custom_thumbnail(user_id)
    if thumb_path and os.path.exists(thumb_path):
        try:
            os.remove(thumb_path)
        except Exception:
            pass
    await db.delete_custom_thumbnail(user_id)
    check_tag = Emojis.tag(Emojis.CHECK, "✅")
    await message.reply_text(f"{check_tag} Custom thumbnail removed. Bot will now use the video's original thumbnail.")


# =============================================================================
# Command: /settings
# =============================================================================
@app.on_message(filters.command("settings") & (filters.private | filters.group))
async def settings_command(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if await _check_banned_user(message):
        return
    prefs = await db.get_preferences(user.id)
    await message.reply_text(
        "<b>⚙️ User Preferences & Delivery Settings</b>\n\n"
        "Configure your default media delivery mode, video splitting format, preferred codecs, and custom destination chat:",
        reply_markup=build_settings_keyboard(prefs),
        parse_mode=enums.ParseMode.HTML,
    )


def get_cookies_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    is_admin = (user_id in Config.ADMIN_ID) or (user_id == Config.OWNER_ID)
    all_cookies = cookie_manager.get_all_cookies()
    resolver = cookie_manager.get_resolver_cookie()

    rows = []
    for c in all_cookies:
        st = c.get("status", "valid")
        if st == "valid":
            status_icon = "✅ Active"
        elif st == "degraded":
            status_icon = "⚠️ Degraded"
        elif st == "expired":
            status_icon = "❌ Expired"
        else:
            status_icon = "🟡 Ready"

        is_resolver = c['filename'] == resolver
        resolver_tag = " 📌" if is_resolver else ""
        btn_text = f"🍪 {c['filename']} | {status_icon}{resolver_tag}"
        show_text = f"👁️ Show {c['filename']}"
        buttons = [
            InlineKeyboardButton(btn_text, callback_data=f"chk_cookie:{c['filename']}"),
            InlineKeyboardButton(show_text, callback_data=f"show_cookie:{c['filename']}"),
        ]
        rows.append(buttons)

        if is_admin:
            if is_resolver:
                rows.append([
                    InlineKeyboardButton("❌ Remove Resolver", callback_data=f"set_resolver:none", **_style_kwargs("danger")),
                ])
            elif not resolver:
                rows.append([
                    InlineKeyboardButton("📌 Set as Resolver", callback_data=f"set_resolver:{c['filename']}"),
                ])

    if is_admin:
        rows.append([
            InlineKeyboardButton("➕ Add Cookie", callback_data="add_cookie_prompt"),
            InlineKeyboardButton("🗑️ Delete Cookie", callback_data="del_cookie_menu", **_style_kwargs("danger")),
        ])

    rows.append([InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))])

    resolver_text = f"<code>{resolver}</code>" if resolver else "<i>None (using regular rotation)</i>"
    text = (
        f"<b>🍪 Multi-Cookie Pool Management</b>\n\n"
        f"• <b>Total Loaded:</b> <code>{len(all_cookies)}</code>\n"
        f"• <b>Resolver Cookie:</b> {resolver_text}\n"
        f"• <b>Download Pool:</b> <code>{len(all_cookies) - (1 if resolver else 0)}</code> cookies\n"
        f"• <b>Access Level:</b> <code>{'👑 Admin / Owner' if is_admin else '👤 Member'}</code>\n\n"
        f"<i>📌 Resolver cookie handles metadata/format resolution for normal video links only.\n"
        f"Download cookies handle playlists and actual downloads.</i>"
    )
    return text, InlineKeyboardMarkup(rows)


# =============================================================================
# Command: /cookies
# =============================================================================
@app.on_message(filters.command("cookies"))
async def cookies_command(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if user.id not in Config.ADMIN_ID and user.id != Config.OWNER_ID:
        return

    text, markup = get_cookies_panel(user.id)
    await message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=enums.ParseMode.HTML,
    )


# =============================================================================
# Command: /leaderboard or /top
# =============================================================================
@app.on_message(filters.command(["leaderboard", "top"]) & (filters.private | filters.group))
async def leaderboard_command(client: Client, message: Message):
    user = message.from_user
    if await _check_banned_user(message):
        return
    leaders = await db.get_leaderboard(limit=5, period="all")
    user_stats = await db.get_user_rank(user.id) if user else {"rank": "N/A", "total_downloads": 0, "total_bytes": 0}

    crown = Emojis.tag(Emojis.CROWN, "👑")
    top_badge = Emojis.tag(Emojis.TOP, "🔝")
    badges = ["🥇", "🥈", "🥉", "🏅", "🏅"]

    text = f"{crown} <b>TOP DOWNLOADERS LEADERBOARD</b> {top_badge}\n\n"
    if leaders:
        for idx, l in enumerate(leaders):
            b = badges[idx] if idx < len(badges) else "•"
            name = l.get("username") or l.get("first_name") or f"User {l['user_id']}"
            gb = l.get("total_bytes", 0) / (1024 ** 3)
            text += f"{b} <b>{idx+1}. @{name}</b> — <code>{l['total_downloads']} Videos ({gb:.2f} GB)</code>\n"
    else:
        text += "<i>No downloads recorded yet!</i>\n"

    my_gb = user_stats.get("total_bytes", 0) / (1024 ** 3)
    text += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Your Rank:</b> #{user_stats.get('rank', 'N/A')} "
        f"(<code>{user_stats.get('total_downloads', 0)} Videos | {my_gb:.2f} GB</code>)"
    )

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Weekly", callback_data="lb:weekly"),
                InlineKeyboardButton("Monthly", callback_data="lb:monthly"),
                InlineKeyboardButton("All-Time", callback_data="lb:all"),
            ],
            [InlineKeyboardButton("✖️ Close", callback_data="close_menu")],
        ]),
        parse_mode=enums.ParseMode.HTML,
    )


# =============================================================================
# Command: /stats (Admin Only)
# =============================================================================
@app.on_message(filters.command("stats") & (filters.private | filters.group))
async def stats_command(client: Client, message: Message):
    if message.from_user.id not in Config.ADMIN_ID:
        return

    g_stats = await db.get_global_stats()
    total_gb = g_stats["total_bytes"] / (1024 ** 3)
    active_tasks = queue_manager._active_task_count

    await message.reply_text(
        f"<b>📊 Global Bot Statistics</b>\n\n"
        f"👥 <b>Total Users:</b> <code>{g_stats['total_users']}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{g_stats['total_downloads']}</code>\n"
        f"💾 <b>Data Bandwidth:</b> <code>{total_gb:.2f} GB</code>\n"
        f"⚡ <b>Active Tasks in Queue:</b> <code>{active_tasks}</code>\n"
        f"🍪 <b>Active Cookies in Pool:</b> <code>{len(cookie_manager.get_all_cookies())}</code>\n"
        f"📁 <b>Dump Channel:</b> <code>{Config.DUMP_CHANNEL or 'Disabled'}</code>\n"
        f"🚀 <b>4GB UserBot Status:</b> <code>{'Active (Premium)' if uploader.is_premium_userbot else 'Standard 2GB'}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


# =============================================================================
# Command: /status (Admin/Owner Only)
# =============================================================================
@app.on_message(filters.command("status") & filters.private)
async def status_command(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await message.reply_text("⛔ This command is admin-only.")
        return

    status_message = await message.reply_text("⏳ Collecting system statistics...")
    try:
        import importlib.metadata
        import platform
        import psutil

        telegram_started_at = time.perf_counter()
        await client.get_me()
        telegram_ping_ms = (time.perf_counter() - telegram_started_at) * 1000

        async def probe_database() -> tuple[bool, float]:
            started_at = time.perf_counter()
            try:
                await db._client.admin.command("ping")
                return True, (time.perf_counter() - started_at) * 1000
            except Exception:
                return False, 0.0

        cpu_percent, database_health = await asyncio.gather(
            asyncio.to_thread(psutil.cpu_percent, 0.5),
            probe_database(),
        )

        process = psutil.Process(os.getpid())
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        logical_cores = psutil.cpu_count(logical=True) or 0
        physical_cores = psutil.cpu_count(logical=False) or logical_cores
        process_memory = process.memory_info().rss
        bot_uptime = max(0, int(time.time() - BOT_STARTED_AT))
        server_uptime = max(0, int(time.time() - psutil.boot_time()))

        def format_duration(total_seconds: int) -> str:
            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"

        def format_size(size_bytes: int) -> str:
            size = float(size_bytes or 0)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if size < 1024 or unit == "TB":
                    return f"{size:.2f} {unit}"
                size /= 1024
            return "0 B"

        def progress_bar(percentage: float, length: int = 10) -> str:
            safe_percentage = max(0.0, min(100.0, float(percentage or 0)))
            filled = round(safe_percentage / 100 * length)
            return "█" * filled + "░" * (length - filled)

        connected, mongo_latency_ms = database_health
        database_text = (
            f"🟢 Connected ({mongo_latency_ms:.1f} ms)"
            if connected else "🔴 Disconnected"
        )
        try:
            telegram_version = importlib.metadata.version("wzgram")
            telegram_engine = f"WZGram {telegram_version}"
        except importlib.metadata.PackageNotFoundError:
            telegram_engine = "WZGram/Pyrogram"

        text = (
            "⏱️ <b>Advanced Bot Status</b>\n\n"
            "🟢 <b>Status:</b> Online\n"
            f"📡 <b>Telegram API:</b> {telegram_ping_ms:.1f} ms\n"
            f"🗄️ <b>MongoDB:</b> {database_text}\n\n"
            "⌛ <b>Uptime</b>\n"
            f"├ <b>Bot:</b> {format_duration(bot_uptime)}\n"
            f"└ <b>Server:</b> {format_duration(server_uptime)}\n\n"
            "🖥️ <b>CPU</b>\n"
            f"├ <code>{progress_bar(cpu_percent)}</code> {cpu_percent:.1f}%\n"
            f"└ <b>Cores:</b> {physical_cores} physical / {logical_cores} logical\n\n"
            "🧠 <b>RAM</b>\n"
            f"├ <code>{progress_bar(ram.percent)}</code> {ram.percent:.1f}%\n"
            f"├ <b>Used:</b> {format_size(ram.used)}\n"
            f"├ <b>Available:</b> {format_size(ram.available)}\n"
            f"└ <b>Total:</b> {format_size(ram.total)}\n\n"
            "💾 <b>Disk</b>\n"
            f"├ <code>{progress_bar(disk.percent)}</code> {disk.percent:.1f}%\n"
            f"├ <b>Used:</b> {format_size(disk.used)}\n"
            f"├ <b>Free:</b> {format_size(disk.free)}\n"
            f"└ <b>Total:</b> {format_size(disk.total)}\n\n"
            "⚙️ <b>Runtime</b>\n"
            f"├ <b>Bot memory:</b> {format_size(process_memory)}\n"
            f"├ <b>Python:</b> {platform.python_version()}\n"
            f"└ <b>Telegram:</b> {telegram_engine}"
        )
        await status_message.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except ImportError:
        await status_message.edit_text(
            "⏱️ <b>Bot Status</b>\n\n"
            "🟢 <b>Status:</b> Online\n\n"
            "⚠️ Install <code>psutil</code> to view system resource usage.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        logger.error(f"Status command failed: {exc}", exc_info=True)
        await status_message.edit_text("❌ Failed to collect system statistics.")


# =============================================================================
# Command: /history
# =============================================================================
@app.on_message(filters.command("history") & filters.private)
async def history_command(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if await _check_banned_user(message):
        return
    total = await db.get_user_history_count(user_id)
    if not total:
        await message.reply_text("📭 No download history found.")
        return

    _HISTORY_PER_PAGE = 10
    _HISTORY_MAX_ITEMS = 20
    page = 1
    display_total = min(total, _HISTORY_MAX_ITEMS)
    history = await db.get_user_history_paged(user_id, page=page, per_page=_HISTORY_PER_PAGE)
    total_pages = max(1, min(-(-display_total // _HISTORY_PER_PAGE), 2))

    lines = []
    buttons = []
    start_num = (page - 1) * _HISTORY_PER_PAGE + 1
    for i, rec in enumerate(history, start_num):
        title = rec.get("title", "Untitled")[:50]
        quality = rec.get("quality", "")
        mode = rec.get("mode", "video")
        size = rec.get("size_bytes", 0)
        ts = rec.get("timestamp", 0)
        size_str = f"{size / (1024*1024):.1f}MB" if size else "N/A"
        mode_icon = "🎵" if mode == "audio" else "📹"
        date_str = time.strftime("%d.%m.%y %H:%M", time.localtime(ts)) if ts else "N/A"
        lines.append(f"{i}. {mode_icon} <code>{html.escape(title)}</code>\n   {quality} | {size_str} | {date_str}")
        vid = rec.get("video_id", "")
        buttons.append([InlineKeyboardButton(f"🔄 Resend #{i}", callback_data=f"re dl:{vid}:{quality}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"hpage:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"hpage:{page + 1}"))
    buttons.append(nav_row)

    caption = f"<b>📜 Latest Downloads</b> (showing {display_total} of {total})\n\n" + "\n\n".join(lines)
    await message.reply_text(
        caption,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =============================================================================
# Command: /globalqueue (Admin/Owner Only, WZML-style pagination)
# =============================================================================
_GLOBALQUEUE_PER_PAGE = 10

@app.on_message(filters.command("globalqueue") & filters.private)
async def globalqueue_command(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await message.reply_text("⛔ This command is admin-only.")
        return
    await _render_globalqueue_page(client, message, page=1)


async def _render_globalqueue_page(
    client: Client, message: Message, page: int = 1, edit: bool = False, user_id: int = 0
):
    tasks = await queue_manager.get_active_tasks()
    phase_counts = await queue_manager.get_phase_counts()
    total_active = sum(phase_counts.values())
    dl_count = phase_counts.get("downloading", 0)
    ul_count = phase_counts.get("uploading", 0)
    q_count = phase_counts.get("queued", 0) + phase_counts.get("processing", 0)

    if not tasks:
        text = (
            "📭 <b>No active tasks in queue.</b>\n\n"
            f"╭ <b>Global Active »</b> <code>0</code>\n"
            f"├ <b>⬇️ Downloading »</b> <code>0</code>\n"
            f"├ <b>⬆️ Uploading »</b> <code>0</code>\n"
            f"╰ <b>⏳ Queued »</b> <code>0</code>"
        )
        if edit:
            try:
                await message.edit_text(text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        else:
            await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return

    total_pages = max(1, -(-len(tasks) // _GLOBALQUEUE_PER_PAGE))  # ceil division
    page = max(1, min(page, total_pages))
    start = (page - 1) * _GLOBALQUEUE_PER_PAGE
    end = start + _GLOBALQUEUE_PER_PAGE
    page_tasks = tasks[start:end]

    lines = []
    lines.append(f"<b>📋 Global Queue — Page {page}/{total_pages}</b>")
    lines.append("▬▬▬▬▬✘▬▬▬▬▬")
    lines.append(f"╭ <b>Total Tasks »</b> <code>{len(tasks)}</code>")
    lines.append(f"├ <b>⬇️ Downloading »</b> <code>{dl_count}</code>")
    lines.append(f"├ <b>⬆️ Uploading »</b> <code>{ul_count}</code>")
    lines.append(f"╰ <b>⏳ Queued/Processing »</b> <code>{q_count}</code>")
    lines.append("")

    for i, task in enumerate(page_tasks, start + 1):
        task_id = task["task_id"]
        uid = task["user_id"]
        is_pl = task["is_playlist"]
        phase = task.get("phase", "queued")
        mode = "📑 Playlist" if is_pl else "📹 Single"

        # Phase display
        phase_map = {
            "queued": "⏳ Queued",
            "downloading": "⬇️ Downloading",
            "processing": "⚙️ Processing",
            "uploading": "⬆️ Uploading",
        }
        phase_str = phase_map.get(phase, "⏳ Queued")

        # Get user info from DB
        user_doc = await db.get_user(uid)
        username = ""
        if user_doc:
            username = user_doc.get("username", "")
        user_str = f"@{username}" if username else str(uid)

        # Get tracker info if available
        tracker = _active_trackers.get(task_id)
        if tracker:
            title = getattr(tracker, "title", "Unknown")[:40]
            lines.append(
                f"{i}. <code>{task_id}</code> | {mode}\n"
                f"   ┊ <b>Status »</b> {phase_str}\n"
                f"   ┊ <b>User »</b> {user_str}\n"
                f"   ╰ <b>Title »</b> {html.escape(title)}"
            )
        else:
            lines.append(
                f"{i}. <code>{task_id}</code> | {mode}\n"
                f"   ┊ <b>Status »</b> {phase_str}\n"
                f"   ╰ <b>User »</b> {user_str}"
            )

    # Build pagination keyboard
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"gqpage:{page - 1}"))
    buttons.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"gqpage:{page + 1}"))

    nav_row = list(buttons)
    buttons = [nav_row, [InlineKeyboardButton("🔄 Refresh", callback_data="gq_refresh")]]

    markup = InlineKeyboardMarkup(buttons) if buttons else None

    text = "\n".join(lines)
    if edit:
        try:
            await message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass
    else:
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


# =============================================================================
# Command: /queue (User — shows own tasks + global stats, anti-spam, WZML-style pagination)
# =============================================================================
_QUEUE_PER_PAGE = 5

@app.on_message(filters.command("queue") & filters.private)
async def queue_command(client: Client, message: Message):
    if await _check_banned_user(message):
        return
    await _render_queue_page(client, message, page=1)


async def _render_queue_page(
    client: Client, message: Message, page: int = 1, edit: bool = False, user_id: int = 0
):
    if not user_id:
        user_id = message.from_user.id if message.from_user else 0
    now = time.time()

    # Anti-spam: track rapid /queue invocations
    spam = _queue_spam_tracker.get(user_id)
    if spam and (now - spam["last_ts"]) < _QUEUE_SPAM_WINDOW:
        spam["count"] += 1
        spam["last_ts"] = now
        if spam["count"] >= 2 and spam.get("msg_id"):
            try:
                await client.delete_messages(user_id, spam["msg_id"])
            except Exception:
                pass
    else:
        _queue_spam_tracker[user_id] = {"count": 1, "last_ts": now, "msg_id": 0}

    # Gather data
    user_tasks = await queue_manager.get_user_tasks(user_id)
    phase_counts = await queue_manager.get_phase_counts()
    total_active = sum(phase_counts.values())

    dl_count = phase_counts.get("downloading", 0)
    ul_count = phase_counts.get("uploading", 0)
    q_count = phase_counts.get("queued", 0) + phase_counts.get("processing", 0)

    if not user_tasks:
        text = (
            "<b>📋 Queue Status</b>\n"
            "▬▬▬▬▬✘▬▬▬▬▬\n"
            f"╭ <b>Global Active »</b> <code>{total_active}</code>\n"
            f"├ <b>⬇️ Downloading »</b> <code>{dl_count}</code>\n"
            f"├ <b>⬆️ Uploading »</b> <code>{ul_count}</code>\n"
            f"╰ <b>⏳ Queued »</b> <code>{q_count}</code>\n\n"
            "📭 <b>You have no active tasks in queue.</b>"
        )
        if edit:
            try:
                await message.edit_text(text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        else:
            msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
            if user_id in _queue_spam_tracker:
                _queue_spam_tracker[user_id]["msg_id"] = msg.id
        return

    total_pages = max(1, -(-len(user_tasks) // _QUEUE_PER_PAGE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PER_PAGE
    end = start + _QUEUE_PER_PAGE
    page_tasks = user_tasks[start:end]

    lines = []
    lines.append(f"<b>📋 Queue Status — Page {page}/{total_pages}</b>")
    lines.append("▬▬▬▬▬✘▬▬▬▬▬")
    lines.append(f"╭ <b>Global Active »</b> <code>{total_active}</code>")
    lines.append(f"├ <b>⬇️ Downloading »</b> <code>{dl_count}</code>")
    lines.append(f"├ <b>⬆️ Uploading »</b> <code>{ul_count}</code>")
    lines.append(f"╰ <b>⏳ Queued »</b> <code>{q_count}</code>")
    lines.append("")
    lines.append(f"<b>Your Active Tasks ({len(user_tasks)}):</b>")
    lines.append("")

    for i, task in enumerate(page_tasks, start + 1):
        task_id = task["task_id"]
        is_pl = task["is_playlist"]
        phase = task["phase"]
        mode = "📑 Playlist" if is_pl else "📹 Single"

        phase_map = {
            "queued": "⏳ Queued",
            "downloading": "⬇️ Downloading",
            "processing": "⚙️ Processing",
            "uploading": "⬆️ Uploading",
        }
        phase_str = phase_map.get(phase, "⏳ Queued")

        tracker = _active_trackers.get(task_id)
        title = "Unknown"
        if tracker:
            title = getattr(tracker, "title", "Unknown")[:40]

        lines.append(f"{i}. <code>{task_id}</code> | {mode}")
        lines.append(f"   ┊ <b>Status »</b> {phase_str}")
        lines.append(f"   ╰ <b>Title »</b> {html.escape(title)}")

    # Build pagination keyboard
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"qpage:{page - 1}"))
    buttons.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"qpage:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    text = "\n".join(lines)
    if edit:
        try:
            await message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass
    else:
        msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        if user_id in _queue_spam_tracker:
            _queue_spam_tracker[user_id]["msg_id"] = msg.id

        # ── Rebind active trackers to this new message ──
        if user_tasks:
            for task in user_tasks:
                tracker = _active_trackers.get(task["task_id"])
                if tracker and tracker.user_id == user_id:
                    tracker.message = msg
                    if tracker.dashboard:
                        async with tracker.dashboard._lock:
                            session = tracker.dashboard._sessions.get(user_id)
                            if session:
                                old_msg = session["message"]
                                session["message"] = msg
                                for existing in session["tasks"].values():
                                    existing.message = msg
                                if old_msg and old_msg.id != msg.id:
                                    try:
                                        await old_msg.delete()
                                    except Exception:
                                        pass
                        tracker.dashboard.request_refresh(user_id, force=True)


# =============================================================================
# Command: /speedtest (Admin Only, explicit confirmation)
# =============================================================================
@app.on_message(filters.command(["speedtest", "spt"]) & filters.private)
async def speedtest_command(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await message.reply_text("⛔ This command is admin-only.")
        return
    if await queue_manager.has_active_tasks():
        await message.reply_text(
            "⚠️ Active download/upload running. Run speedtest after transfer completes."
        )
        return
    await message.reply_text(
        "⚠️ <b>Speedtest will use full network and may consume significant data.</b>\nRun it?",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Run", callback_data=f"speedtest_confirm:{user_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"speedtest_cancel:{user_id}", **_style_kwargs("danger")),
        ]]),
    )


# =============================================================================
# Command: /broadcast (Admin Only)
# =============================================================================
@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client: Client, message: Message):
    if message.from_user.id not in Config.ADMIN_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Please reply to a message to broadcast.")
        return

    status_msg = await message.reply_text("📢 Starting broadcast...")
    users_cursor = await db.get_all_users()
    total = await db.users.count_documents({"is_banned": False})

    success = 0
    blocked = 0
    async for u in users_cursor:
        try:
            await client.copy_message(chat_id=u["user_id"], from_chat_id=message.chat.id, message_id=message.reply_to_message.id)
            success += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            blocked += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"✅ <b>Broadcast Completed:</b>\n\nSent: <code>{success}</code>\nFailed/Blocked: <code>{blocked}</code>\nTotal: <code>{total}</code>")


# --- Ban / Unban Commands (Admin + Owner only) ---

BANNED_USER_MESSAGE = (
    "🚫 <b>You are banned from using this bot.</b>\n\n"
    "You have been banned by an administrator.\n"
    "<b>Reason:</b> {reason}\n\n"
    "Please contact <a href=\"https://t.me/NexonContactBot\">@NexonContactBot</a> for help or to request an unban."
)


async def _check_banned_user(message: Message) -> bool:
    """Check if user is banned. Returns True if banned (and sends message with reason)."""
    user = message.from_user
    if user and await db.is_banned(user.id):
        try:
            reason = await db.get_ban_reason(user.id) or "No reason provided"
            await message.reply_text(
                BANNED_USER_MESSAGE.format(reason=html.escape(reason)),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return True
    return False


async def _check_banned_user_cb(query: CallbackQuery) -> bool:
    """Check if user is banned from a callback query. Returns True if banned."""
    user = query.from_user
    if user and await db.is_banned(user.id):
        try:
            reason = await db.get_ban_reason(user.id) or "No reason provided"
            await query.answer(
                f"You are banned from using this bot.\nReason: {reason}\nContact @NexonContactBot for help.",
                show_alert=True,
            )
        except Exception:
            pass
        return True
    return False


def _parse_ban_args(message: Message) -> tuple[str | None, str | None]:
    """Parse ban command arguments: /ban <target> [reason].
    Returns (target_arg, reason_or_None)."""
    text = (message.text or "").strip()
    parts = text.split(maxsplit=2)  # /ban target reason...
    if len(parts) >= 3:
        return parts[1], parts[2]
    elif len(parts) == 2:
        return parts[1], None
    return None, None


async def _resolve_ban_target(message: Message) -> tuple[int | None, str]:
    """Resolve a ban/unban target from reply, user ID, or @username.
    Returns (user_id, display_name) or (None, error_message)."""
    # Case 1: Reply to a user's message
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return target.id, f"@{target.username}" if target.username else target.first_name or str(target.id)

    target_arg, _ = _parse_ban_args(message)
    if target_arg is None:
        return None, "Usage: /ban <user_id> [reason]\n/ban @username [reason]\nOr reply to a user's message with /ban [reason]"

    # Numeric user ID
    if target_arg.isdigit():
        uid = int(target_arg)
        user_doc = await db.get_user(uid)
        if user_doc and user_doc.get("username"):
            display = f"@{user_doc['username']}"
        elif user_doc and user_doc.get("first_name"):
            display = user_doc["first_name"]
        else:
            display = str(uid)
        return uid, display

    # @username
    if target_arg.startswith("@"):
        username = target_arg[1:]
        user_doc = await db.users.find_one({"username": username})
        if user_doc:
            return user_doc["user_id"], f"@{username}"
        return None, f"User @{username} not found in database. They must /start the bot first."

    return None, f"Invalid target: {target_arg}\nUse a numeric user ID or @username."


@app.on_message(filters.command("ban") & filters.private)
async def ban_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        return

    target_id, display = await _resolve_ban_target(message)
    if target_id is None:
        await message.reply_text(f"❌ {display}", parse_mode=enums.ParseMode.HTML)
        return

    # Cannot ban owner or admins
    if target_id == Config.OWNER_ID:
        await message.reply_text("❌ Cannot ban the bot owner.")
        return
    if target_id in Config.ADMIN_ID:
        await message.reply_text("❌ Cannot ban an admin.")
        return

    # Check if already banned
    if await db.is_banned(target_id):
        await message.reply_text(f"⚠️ <code>{target_id}</code> ({display}) is <b>already banned</b>.", parse_mode=enums.ParseMode.HTML)
        return

    # Parse optional reason
    _, reason = _parse_ban_args(message)

    await db.set_ban_status(target_id, True, reason)
    reason_line = f"\n• <b>Reason:</b> {html.escape(reason)}" if reason else ""
    await message.reply_text(
        f"✅ <b>User Banned</b>\n\n"
        f"• <b>User ID:</b> <code>{target_id}</code>\n"
        f"• <b>Username:</b> {display}"
        f"{reason_line}",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("unban") & filters.private)
async def unban_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        return

    target_id, display = await _resolve_ban_target(message)
    if target_id is None:
        await message.reply_text(f"❌ {display}", parse_mode=enums.ParseMode.HTML)
        return

    if not await db.is_banned(target_id):
        await message.reply_text(f"⚠️ <code>{target_id}</code> ({display}) is <b>not banned</b>.", parse_mode=enums.ParseMode.HTML)
        return

    await db.set_ban_status(target_id, False)
    await message.reply_text(
        f"✅ <b>User Unbanned</b>\n\n"
        f"• <b>User ID:</b> <code>{target_id}</code>\n"
        f"• <b>Username:</b> {display}",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("unbanall") & filters.private)
async def unbanall_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id != Config.OWNER_ID:
        await message.reply_text("❌ This command is owner-only.")
        return

    count = await db.unban_all()
    if count == 0:
        await message.reply_text("✅ No banned users found.")
    else:
        await message.reply_text(
            f"✅ <b>All Users Unbanned</b>\n\n"
            f"• <b>Users unbanned:</b> <code>{count}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("banlist") & filters.private)
async def banlist_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        return
    await _render_banlist_page(client, message, page=1)


async def _render_banlist_page(client: Client, message: Message, page: int):
    """Render paginated ban list."""
    per_page = 10
    users, total = await db.get_banned_users_paged(page=page, per_page=per_page)

    if total == 0:
        await message.reply_text("✅ <b>No banned users.</b>", parse_mode=enums.ParseMode.HTML)
        return

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    lines = [f"🚫 <b>Banned Users</b> (Page {page}/{total_pages})\n"]
    for i, u in enumerate(users, start=(page - 1) * per_page + 1):
        uid = u.get("user_id", "?")
        uname = f"@{u['username']}" if u.get("username") else u.get("first_name", "N/A")
        reason = u.get("ban_reason", "No reason provided")
        lines.append(f"<b>{i}.</b> <code>{uid}</code> — {uname}\n    📝 {html.escape(reason)}")

    # Build pagination buttons
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"banlist_page:{page - 1}"))
    buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"banlist_page:{page + 1}"))

    await message.reply_text(
        "\n".join(lines),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None,
    )


@app.on_callback_query(filters.regex(r"^banlist_page:(\d+)$"))
async def banlist_page_handler(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await query.answer("Admin only!", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("Invalid page.", show_alert=True)
        return
    # Edit the existing message with new page
    per_page = 10
    users, total = await db.get_banned_users_paged(page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    lines = [f"🚫 <b>Banned Users</b> (Page {page}/{total_pages})\n"]
    for i, u in enumerate(users, start=(page - 1) * per_page + 1):
        uid = u.get("user_id", "?")
        uname = f"@{u['username']}" if u.get("username") else u.get("first_name", "N/A")
        reason = u.get("ban_reason", "No reason provided")
        lines.append(f"<b>{i}.</b> <code>{uid}</code> — {uname}\n    📝 {html.escape(reason)}")

    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"banlist_page:{page - 1}"))
    buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"banlist_page:{page + 1}"))

    try:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None,
        )
    except MessageNotModified:
        pass
    await query.answer()


@app.on_message(filters.command("clearcache") & filters.private)
async def clear_cache_command(client: Client, message: Message):
    user_id = message.from_user.id
    if (user_id not in Config.ADMIN_ID) and (user_id != Config.OWNER_ID):
        return
    count = await db.cached_media.count_documents({})
    await message.reply_text(
        f"⚠️ <b>Are you sure you want to clear the dump cache?</b>\n\n"
        f"• <code>{count}</code> cached video entries will be <b>permanently deleted</b>.\n"
        f"• All future requests will need fresh downloads.\n\n"
        f"<i>This action cannot be undone.</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Delete", callback_data="clearcache_confirm", **_style_kwargs("danger")),
                InlineKeyboardButton("❌ Cancel", callback_data="close_menu"),
            ],
        ]),
    )


@app.on_callback_query(filters.regex(r"^clearcache_confirm$"))
async def clearcache_confirm_handler(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if (user_id not in Config.ADMIN_ID) and (user_id != Config.OWNER_ID):
        await query.answer("Admin only!", show_alert=True)
        return
    res = await db.cached_media.delete_many({})
    await query.message.edit_text(
        f"🗑️ <b>Dump Cache Cleared Successfully!</b>\n\n"
        f"• Removed <code>{res.deleted_count}</code> cached video entries from MongoDB.\n"
        f"• All future requests will now perform fresh downloads!",
        parse_mode=enums.ParseMode.HTML,
    )
    await query.answer("Cache cleared!")


@app.on_message(filters.command("clearqueue") & filters.private)
async def clear_queue_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await _check_banned_user(message):
        return
    cancelled = await queue_manager.cancel_user_tasks(user_id)
    if cancelled:
        await message.reply_text(
            f"🗑️ <b>Your queue cleared!</b>\n\n"
            f"• <b>Tasks cancelled:</b> <code>{cancelled}</code>\n\n"
            f"Running tasks will stop at their next checkpoint.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "✅ <b>Your queue is empty.</b>\nNo active tasks to clear.",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("clearglobalqueue") & filters.private)
async def clear_global_queue_cmd(client: Client, message: Message):
    if message.from_user.id != Config.OWNER_ID:
        await message.reply_text("⛔ Only owner can use this command.")
        return
    cancelled = await queue_manager.cancel_all_tasks()
    if cancelled:
        await message.reply_text(
            f"🗑️ <b>Global queue cleared!</b>\n\n"
            f"• <b>All tasks cancelled:</b> <code>{cancelled}</code>\n\n"
            f"Running tasks will stop at their next checkpoint.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "✅ <b>Global queue is empty.</b>\nNo active tasks to clear.",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("db") & filters.private)
async def db_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id != Config.OWNER_ID:
        return
    sec_uris = db.get_secondary_uris()
    sec_count = len(sec_uris)
    sec_list = ""
    if sec_uris:
        for i, uri in enumerate(sec_uris, 1):
            sec_list += f"  {i}. <code>{uri}</code>\n"
    else:
        sec_list = "  <i>None configured</i>"
    await message.reply_text(
        f"<b>🗄️ Multi-Database Manager</b>\n\n"
        f"<b>Primary DB:</b>\n  <code>{db._primary_uri[:50]}...</code>\n\n"
        f"<b>Secondary DBs:</b> <code>{sec_count}</code>\n{sec_list}\n"
        f"<i>Secondary DBs provide backup + failover for cache data.</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Database", callback_data="db_add_prompt")],
            [InlineKeyboardButton("➖ Remove Database", callback_data="db_remove_menu")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="db_refresh")],
            [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
        ]),
    )


# =============================================================================
# Command: /logs — Live Bot Logs with Styled Output
# =============================================================================
# In-memory log buffer — always available even if file logging is off
_import_log_buffer: list[str] = []
_import_orig_log = logging.Handler.emit

class _BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            _import_log_buffer.append(msg)
            if len(_import_log_buffer) > 500:
                _import_log_buffer.pop(0)
        except Exception:
            pass

_buf_handler = _BufferHandler()
_buf_handler.setFormatter(logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_buf_handler)


@app.on_message(filters.command("logs") & filters.private)
async def logs_command(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await message.reply_text("⛔ This command is admin-only.")
        return

    status_msg = await message.reply_text(
        "🔄 <i>Fetching live bot logs from server...</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        # Read from multiple possible log file locations
        bot_dir = os.path.dirname(os.path.abspath(__file__))
        possible_locations = [
            bot_dir,
            os.getcwd(),
            "/tmp",
            ".",
        ]

        error_lines = []
        output_lines = []
        source_info = "file"

        # Try to read from log files
        for loc in possible_locations:
            error_log_path = os.path.join(loc, "bot_error.log")
            output_log_path = os.path.join(loc, "bot_output.log")

            if os.path.exists(error_log_path) and os.path.getsize(error_log_path) > 0:
                with open(error_log_path, "r", encoding="utf-8", errors="replace") as f:
                    error_lines = f.readlines()

            if os.path.exists(output_log_path) and os.path.getsize(output_log_path) > 0:
                with open(output_log_path, "r", encoding="utf-8", errors="replace") as f:
                    output_lines = f.readlines()

            if error_lines or output_lines:
                break

        # Fallback: use in-memory buffer if files are empty
        if not error_lines and not output_lines and _import_log_buffer:
            output_lines = list(_import_log_buffer)
            source_info = "memory"

        if not error_lines and not output_lines:
            # Even with no logs, show a status report
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            md_sections = []
            md_sections.append("# 📋 Bot Live Logs")
            md_sections.append(f"**Generated:** {now_str}")
            md_sections.append(f"**Server Status:** ✅ Online (No log entries yet)")
            md_sections.append("")
            md_sections.append("## 📊 Overview")
            md_sections.append("")
            md_sections.append("| Metric | Value |")
            md_sections.append("| :--- | :---: |")
            md_sections.append(f"| Error Logs | `0` |")
            md_sections.append(f"| Output Logs | `0` |")
            md_sections.append("")
            md_sections.append("::: yellow")
            md_sections.append("**Note:** Logs will appear here after bot processes some requests. The file logging handlers are now active.")
            md_sections.append(":::")
            md_sections.append("")
            md_sections.append("---")
            md_sections.append("*Powered by NexTube Bot*")

            md_content = "\n".join(md_sections)
            paste_url = await upload_to_pastyx(
                content=md_content,
                title=f"Bot Logs — {now_str}",
                author="@NexTubeBot",
            )

            buttons = []
            if paste_url:
                buttons.append([InlineKeyboardButton("📋 View Logs", url=paste_url)])
            buttons.append([
                InlineKeyboardButton("🔄 Refresh", callback_data="logs_refresh"),
                InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger")),
            ])

            await status_msg.edit_text(
                f"<b>📋 Bot Live Logs — Server Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 <b>Time:</b> <code>{now_str}</code>\n"
                f"📁 <b>Error Lines:</b> <code>0</code>\n"
                f"📁 <b>Output Lines:</b> <code>0</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📭 <b>No log entries yet.</b>\n"
                f"Logs will appear after bot processes requests.\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Source: {source_info} | File logging is now active</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # Take latest 100 lines from each log
        error_latest = error_lines[-100:] if error_lines else []
        output_latest = output_lines[-100:] if output_lines else []

        total_error = len(error_lines)
        total_output = len(output_lines)

        # Build premium styled markdown logs
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        md_sections = []
        md_sections.append(f"# 📋 Bot Live Logs")
        md_sections.append(f"**Generated:** {now_str}")
        md_sections.append(f"**Server Status:** ✅ Online")
        md_sections.append(f"**Log Source:** {source_info}")
        md_sections.append("")

        # Stats overview
        md_sections.append("## 📊 Overview")
        md_sections.append("")
        md_sections.append("| Metric | Value |")
        md_sections.append("| :--- | :---: |")
        md_sections.append(f"| Total Error Logs | `{total_error}` |")
        md_sections.append(f"| Total Output Logs | `{total_output}` |")
        md_sections.append(f"| Shown (Latest) | `{len(error_latest) + len(output_latest)}` |")
        md_sections.append("")

        # Error logs section
        if error_latest:
            md_sections.append("## 🔴 Error Logs (Latest)")
            md_sections.append("")
            md_sections.append("```")
            for line in error_latest:
                md_sections.append(line.rstrip())
            md_sections.append("```")
            md_sections.append("")

        # Output logs section
        if output_latest:
            md_sections.append("## 🟢 Output / Info Logs (Latest)")
            md_sections.append("")
            md_sections.append("```")
            for line in output_latest:
                md_sections.append(line.rstrip())
            md_sections.append("```")
            md_sections.append("")

        md_sections.append("---")
        md_sections.append(f"*Powered by NexTube Bot • [View Online](https://pastyx.pages.dev)*")

        md_content = "\n".join(md_sections)

        await status_msg.edit_text(
            "📤 <i>Uploading styled logs...</i>",
            parse_mode=enums.ParseMode.HTML,
        )

        paste_url = await upload_to_pastyx(
            content=md_content,
            title=f"Bot Logs — {now_str}",
            author="@NexTubeBot",
        )

        # Build summary
        latest_error_summary = ""
        if error_latest:
            last_err = error_latest[-1].strip()[:120]
            latest_error_summary = f"\n🔴 <b>Last Error:</b>\n<code>{html.escape(last_err)}</code>"

        latest_info_summary = ""
        if output_latest:
            last_info = output_latest[-1].strip()[:120]
            latest_info_summary = f"\n🟢 <b>Last Info:</b>\n<code>{html.escape(last_info)}</code>"

        buttons = []
        if paste_url:
            buttons.append([InlineKeyboardButton("📋 View Full Logs", url=paste_url)])
        buttons.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="logs_refresh"),
            InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger")),
        ])

        await status_msg.edit_text(
            f"<b>📋 Bot Live Logs — Server Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <b>Time:</b> <code>{now_str}</code>\n"
            f"📁 <b>Error Lines:</b> <code>{total_error}</code>\n"
            f"📁 <b>Output Lines:</b> <code>{total_output}</code>\n"
            f"📡 <b>Source:</b> <code>{source_info}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{latest_error_summary}"
            f"{latest_info_summary}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Full styled logs uploaded 👆</i>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception as e:
        logger.error(f"Error in /logs command: {e}")
        try:
            await status_msg.edit_text(
                f"❌ <b>Failed to fetch logs:</b>\n<code>{html.escape(str(e)[:300])}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^logs_refresh$"))
async def logs_refresh_handler(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        await query.answer("Admin only!", show_alert=True)
        return

    await query.answer("🔄 Refreshing logs...")

    try:
        possible_locations = [
            os.path.dirname(os.path.abspath(__file__)),
            os.getcwd(),
            "/tmp",
            ".",
        ]

        error_lines = []
        output_lines = []
        source_info = "file"

        for loc in possible_locations:
            error_log_path = os.path.join(loc, "bot_error.log")
            output_log_path = os.path.join(loc, "bot_output.log")

            if os.path.exists(error_log_path) and os.path.getsize(error_log_path) > 0:
                with open(error_log_path, "r", encoding="utf-8", errors="replace") as f:
                    error_lines = f.readlines()

            if os.path.exists(output_log_path) and os.path.getsize(output_log_path) > 0:
                with open(output_log_path, "r", encoding="utf-8", errors="replace") as f:
                    output_lines = f.readlines()

            if error_lines or output_lines:
                break

        if not error_lines and not output_lines and _import_log_buffer:
            output_lines = list(_import_log_buffer)
            source_info = "memory"

        error_latest = error_lines[-100:] if error_lines else []
        output_latest = output_lines[-100:] if output_lines else []
        total_error = len(error_lines)
        total_output = len(output_lines)

        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        md_sections = []
        md_sections.append(f"# 📋 Bot Live Logs")
        md_sections.append(f"**Generated:** {now_str}")
        md_sections.append(f"**Server Status:** ✅ Online")
        md_sections.append(f"**Log Source:** {source_info}")
        md_sections.append("")
        md_sections.append("## 📊 Overview")
        md_sections.append("")
        md_sections.append("| Metric | Value |")
        md_sections.append("| :--- | :---: |")
        md_sections.append(f"| Total Error Logs | `{total_error}` |")
        md_sections.append(f"| Total Output Logs | `{total_output}` |")
        md_sections.append(f"| Shown (Latest) | `{len(error_latest) + len(output_latest)}` |")
        md_sections.append("")

        if error_latest:
            md_sections.append("## 🔴 Error Logs (Latest)")
            md_sections.append("")
            md_sections.append("```")
            for line in error_latest:
                md_sections.append(line.rstrip())
            md_sections.append("```")
            md_sections.append("")

        if output_latest:
            md_sections.append("## 🟢 Output / Info Logs (Latest)")
            md_sections.append("")
            md_sections.append("```")
            for line in output_latest:
                md_sections.append(line.rstrip())
            md_sections.append("```")
            md_sections.append("")

        md_sections.append("---")
        md_sections.append(f"*Powered by NexTube Bot • [View Online](https://pastyx.pages.dev)*")

        md_content = "\n".join(md_sections)
        paste_url = await upload_to_pastyx(
            content=md_content,
            title=f"Bot Logs — {now_str}",
            author="@NexTubeBot",
        )

        latest_error_summary = ""
        if error_latest:
            last_err = error_latest[-1].strip()[:120]
            latest_error_summary = f"\n🔴 <b>Last Error:</b>\n<code>{html.escape(last_err)}</code>"

        latest_info_summary = ""
        if output_latest:
            last_info = output_latest[-1].strip()[:120]
            latest_info_summary = f"\n🟢 <b>Last Info:</b>\n<code>{html.escape(last_info)}</code>"

        buttons = []
        if paste_url:
            buttons.append([InlineKeyboardButton("📋 View Full Logs", url=paste_url)])
        buttons.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="logs_refresh"),
            InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger")),
        ])

        await query.message.edit_text(
            f"<b>📋 Bot Live Logs — Server Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <b>Time:</b> <code>{now_str}</code>\n"
            f"📁 <b>Error Lines:</b> <code>{total_error}</code>\n"
            f"📁 <b>Output Lines:</b> <code>{total_output}</code>\n"
            f"📡 <b>Source:</b> <code>{source_info}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{latest_error_summary}"
            f"{latest_info_summary}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Full styled logs uploaded 👆</i>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        await query.answer(f"Error: {str(e)[:100]}", show_alert=True)


# =============================================================================
# Document Handler: Admin Uploads Netscape cookies.txt
# =============================================================================
@app.on_message(filters.document & filters.private)
async def document_cookie_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if (user_id not in Config.ADMIN_ID) and (user_id != Config.OWNER_ID):
        return

    doc = message.document
    if doc.file_name and (doc.file_name.endswith(".txt") or doc.file_name.endswith(".cookies")):
        status_msg = await message.reply_text("🔄 Processing and auto-sanitizing cookie file...")
        downloaded = await message.download(in_memory=True)
        content_bytes = bytes(downloaded.getbuffer())

        ok, name_or_err = cookie_manager.add_cookie_file(content_bytes)
        if ok:
            check_tag = Emojis.tag(Emojis.CHECK, "✅")
            total_cookies = len(cookie_manager.get_all_cookies())
            await status_msg.edit_text(
                f"{check_tag} <b>Cookie Added Successfully!</b>\n\n"
                f"• Saved as: <code>{name_or_err}</code>\n"
                f"• Auto-sanitized: <code>#HttpOnly_</code> prefixes\n"
                f"• Total Pool Size: <code>{total_cookies}</code> cookies\n\n"
                f"<i>This cookie is now active in the rotation pool!</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await status_msg.edit_text(f"❌ Error adding cookie: `{name_or_err}`")


# =============================================================================
# Destination Chat Text Input Handler
# =============================================================================
@app.on_message(filters.text & filters.private & ~filters.regex(r"^/") & ~filters.regex(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+'))
async def destination_text_input(client: Client, message: Message):
    user_id = message.from_user.id
    if await _check_banned_user(message):
        return

    # Custom playlist input
    if user_id in _user_playlist_input:
        state = _user_playlist_input.pop(user_id)
        pl_id = state["pl_id"]
        total = state["total"]
        mode = state["mode"]
        action = state.get("action", "download")
        text = message.text.strip()
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await message.reply_text("⚠️ Playlist data has expired. Send the link again.")
            return
        entries = pl_data["entries"]

        selected_indices = []
        if mode in ("range", "pick"):
            try:
                selected_indices = _parse_playlist_range(text, total)
            except ValueError as e:
                await message.reply_text(f"⚠️ {e}\n\nFormat: <code>3</code> or <code>1-5</code> or <code>1,3,7</code> or <code>1,3-5,7</code>\nTotal videos: {total}", parse_mode=enums.ParseMode.HTML)
                return

        if not selected_indices:
            await message.reply_text("⚠️ No valid video was selected.")
            return

        # Route to appropriate action handler
        if action == "description":
            asyncio.create_task(_handle_playlist_descriptions(client, message, pl_data, selected_indices))
            return
        elif action == "formats":
            asyncio.create_task(_handle_playlist_formats(client, message, pl_data, selected_indices))
            return

        # Default: download action
        # Get quality from playlist selected quality cache
        quality = _playlist_selected_quality.pop(pl_id, "720p")

        # Create a fake CallbackQuery-like object for _execute_playlist_batch
        class _FakeQueryForPlaylist:
            def __init__(self, msg, usr):
                self.message = msg
                self.from_user = usr
                self.data = ""
            async def answer(self, *a, **kw):
                pass

        status_msg = await message.reply_text(
            f"📑 <b>Starting {len(selected_indices)} videos from playlist...</b>\n"
            f"Quality: <code>{quality}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        fake_q = _FakeQueryForPlaylist(status_msg, message.from_user)
        asyncio.create_task(_execute_playlist_batch(
            client, fake_q, pl_id, selected_indices, quality,
        ))
        return

    # Secondary DB URI input
    if user_id in _user_waiting_db_uri:
        _user_waiting_db_uri.discard(user_id)
        uri = message.text.strip()
        status_msg = await message.reply_text("🔄 <i>Connecting to database...</i>", parse_mode=enums.ParseMode.HTML)
        ok, msg = await db.add_secondary_db(uri)
        check_tag = Emojis.tag(Emojis.CHECK, "✅") if ok else Emojis.tag(Emojis.CROSS, "❌")
        await status_msg.edit_text(f"{check_tag} {msg}", parse_mode=enums.ParseMode.HTML)
        return

    if user_id in _user_waiting_dest:
        del _user_waiting_dest[user_id]
        target_text = message.text.strip()
        try:
            target_chat = int(target_text)
        except ValueError:
            target_chat = target_text

        valid, info = await verify_destination_chat(client, target_chat, user_id)
        if valid:
            await db.update_preference(user_id, "destination_chat_id", target_chat)
            check_tag = Emojis.tag(Emojis.CHECK, "✅")
            await message.reply_text(f"{check_tag} <b>Destination Chat Updated!</b>\n\nTarget: <code>{info}</code>", parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(info, parse_mode=enums.ParseMode.HTML)
        return


# =============================================================================
# YouTube URL Handler (PM & Groups, Channels Ignored)
# =============================================================================
@app.on_message(filters.regex(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+') & (filters.private | filters.group))
async def youtube_link_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if await _check_banned_user(message):
        return

    user_id = user.id
    url = message.text.strip()

    # 1. ForceSub Check
    joined, missing = await check_forcesub(client, user_id)
    if not joined:
        await message.reply_text(
            "⚠️ <b>Access Restricted!</b>\n\nPlease join our update channels to use the bot:",
            reply_markup=get_forcesub_keyboard(missing),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # 2. Check for Playlist URL
    if "playlist?list=" in url or "&list=" in url:
        status_msg = await message.reply_text("📑 <i>Analyzing YouTube Playlist...</i>", parse_mode=enums.ParseMode.HTML)

        # Extract playlist ID from URL
        import re as _re
        pl_match = _re.search(r'list=([A-Za-z0-9_-]+)', url)
        pl_raw_id = pl_match.group(1) if pl_match else ""

        # 1. Try Innertube lightweight extraction (no cookies, no per-video formats)
        pl_info = None
        if pl_raw_id:
            try:
                pl_info = await get_playlist_info(pl_raw_id)
            except Exception as e:
                logger.warning(f"Innertube playlist extraction failed: {e}")

        # 2. Fall back to yt-dlp for entries list (lightweight, playlist_end=50)
        entries = []
        pl_title = "YouTube Playlist"
        pl_id = pl_raw_id or str(uuid.uuid4())[:8]
        pl_views = 0
        channel_name = ""
        subscribers = ""
        thumbnail_url = ""

        if pl_info:
            pl_title = pl_info.get("title", "YouTube Playlist")
            entries = pl_info.get("entries", [])
            pl_id = pl_raw_id or pl_id
            pl_views = pl_info.get("views", 0)
            channel_name = pl_info.get("channel_name", "")
            subscribers = pl_info.get("subscribers", "")
            thumbnail_url = pl_info.get("thumbnail", "")

        # Always try yt-dlp to fill missing views or as full fallback
        extraction_url = url
        # Seed-based YouTube Mixes are watch playlists; their /playlist page
        # can be unviewable. Keep the seed explicit for a consistent snapshot.
        if pl_raw_id.startswith("RD") and len(pl_raw_id) == 13:
            extraction_url = f"https://www.youtube.com/watch?v={pl_raw_id[2:]}&list={pl_raw_id}"
        info = await extract_info_via_ytdlp(extraction_url, playlist_end=50, is_playlist=True)
        if info and "entries" in info:
            if info.get("entries"):
                pl_title = info.get("title", pl_title)
                entries = info.get("entries", [])
                pl_id = info.get("id") or pl_id
            yt_views = info.get("view_count")
            if yt_views and yt_views > 0:
                pl_views = yt_views

        if not entries:
            await status_msg.edit_text(
                "❌ <b>Failed to extract playlist.</b>\n\nPossible reasons:\n• Playlist is private or age-restricted\n• YouTube rate limit (429)\n• Network error\n\nTry again later or send individual video links.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        entries = preserve_playlist_positions(entries)
        count = len(entries)
        # Bind callbacks to this extraction snapshot. Reopening a playlist
        # (especially a dynamic Mix) must not replace an older menu's links.
        pl_id = uuid.uuid4().hex[:12]

        # Cache playlist data (lightweight — no per-video metadata)
        _playlist_cache[pl_id] = {
            "title": pl_title,
            "entries": entries,
            "url": url,
            "total_count": count,
            "views": pl_views,
            "channel_name": channel_name,
            "subscribers": subscribers,
        }

        # Get first video ID for button callbacks (lightweight — just the ID)
        first_entry = entries[0] if entries else None
        first_vid = first_entry.get("id", "") if first_entry else ""
        if not first_vid:
            first_url = first_entry.get("url", "") if first_entry else ""
            first_vid = extract_video_id(first_url) or str(uuid.uuid4())[:8]

        # Build clean playlist caption (no first-video metadata needed)
        caption = build_playlist_caption({
            "title": pl_title,
            "total_count": count,
            "views": pl_views,
            "channel_name": channel_name,
            "subscribers": subscribers,
        })

        # Build keyboard without quality data (lightweight)
        keyboard = build_playlist_quality_keyboard(
            pl_id, first_vid, qualities=None,
            total_count=count,
        )

        # Use thumbnail from first video or playlist
        if not thumbnail_url and first_vid:
            thumbnail_url = get_thumbnail_url(first_vid)

        try:
            if thumbnail_url:
                photo_caption = caption if len(caption) <= 1024 else (caption[:1015] + "...")
                await message.reply_photo(
                    photo=thumbnail_url,
                    caption=photo_caption,
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.HTML,
                )
            else:
                await message.reply_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.HTML,
                )
            await status_msg.delete()
        except Exception as e:
            logger.warning(f"reply_photo failed ({e}); falling back to reply_text")
            await message.reply_text(
                caption,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
            )
            await status_msg.delete()
        return

    vid = extract_video_id(url)
    if not vid:
        return

    status_msg = await message.reply_text("⚡ <i>Resolving YouTube metadata & formats...</i>", parse_mode=enums.ParseMode.HTML)

    metadata = None
    qualities = None

    # Priority 1: Multi-Cookie yt-dlp Extractor (Full DASH formats & accurate sizes)
    info = await extract_info_via_ytdlp(url)
    if info:
        try:
            metadata, qualities = parse_ytdlp_info(info)
        except Exception as e:
            logger.warning(f"Failed to parse yt-dlp info: {e}")

    # Priority 2: Fallback to Innertube Fast Path
    if not metadata or not qualities:
        player_data = await fetch_player_data(vid)
        if player_data:
            metadata = parse_metadata(player_data, vid)
            formats_data = parse_available_formats(player_data.get("streamingData", {}), metadata["duration_sec"])
            qualities = formats_data["qualities"]

    if not metadata or not qualities:
        await status_msg.edit_text("❌ <b>Could not resolve video stream.</b> Video may be private, age-gated, or unavailable.")
        return

    _video_cache[vid] = {"metadata": metadata, "qualities": qualities, "url": url}

    prefs = await db.get_preferences(user_id)
    caption = build_metadata_caption(
        metadata,
        qualities,
        preferred_codec=prefs.get("codec"),
        preferred_container=prefs.get("container"),
    )
    keyboard = build_main_keyboard(vid, qualities, preferred_codec=prefs.get("codec"), audio_formats=metadata.get("audio_formats"), current_audio_codec=prefs.get("audio_codec", "M4A"))

    thumb_url = metadata["thumbnail"]
    try:
        photo_caption = caption if len(caption) <= 1024 else (caption[:1015] + "...")
        await message.reply_photo(
            photo=thumb_url,
            caption=photo_caption,
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.HTML,
        )
        await status_msg.delete()
    except Exception as e:
        logger.warning(f"reply_photo failed ({e}); falling back to reply_text")
        await message.reply_text(
            caption,
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.HTML,
        )
        await status_msg.delete()


# =============================================================================
# On-Demand Video Cache Helper
# =============================================================================
async def _ensure_video_cache(vid: str, url: str | None = None) -> dict | None:
    """Ensure a video's metadata and qualities are cached. Returns the cache entry or None."""
    cache = _video_cache.get(vid)
    if cache:
        return cache

    if not url:
        url = f"https://youtu.be/{vid}"

    metadata = None
    qualities = None

    # Priority 1: Multi-Cookie yt-dlp Extractor (Full DASH formats & accurate sizes)
    info = await extract_info_via_ytdlp(url)
    if info:
        try:
            metadata, qualities = parse_ytdlp_info(info)
        except Exception as e:
            logger.warning(f"Failed to parse yt-dlp info: {e}")

    # Priority 2: Fallback to Innertube Fast Path
    if not metadata or not qualities:
        player_data = await fetch_player_data(vid)
        if player_data:
            try:
                metadata = parse_metadata(player_data, vid)
                formats_data = parse_available_formats(player_data.get("streamingData", {}), metadata["duration_sec"])
                qualities = formats_data["qualities"]
            except Exception:
                pass

    if metadata and qualities:
        _video_cache[vid] = {"metadata": metadata, "qualities": qualities, "url": url}
        return _video_cache[vid]

    return None


# =============================================================================
# Callback Query Handlers
# =============================================================================
@app.on_callback_query()
async def callback_handler(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    # Ban check for all user-facing callbacks (skip for admins/owner)
    if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
        if await db.is_banned(user_id):
            reason = await db.get_ban_reason(user_id) or "No reason provided"
            try:
                await query.answer(
                    f"You are banned from using this bot.\nReason: {reason}\nContact @NexonContactBot for help.",
                    show_alert=True,
                )
            except Exception:
                pass
            return

    if data.startswith("speedtest_cancel:"):
        try:
            owner_id = int(data.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            await query.answer("Invalid or expired speedtest request.", show_alert=True)
            return
        if user_id != owner_id:
            await query.answer("This speedtest request is not yours.", show_alert=True)
            return
        await query.message.edit_text("❌ Speedtest cancelled.")
        await query.answer()
        return

    if data.startswith("speedtest_confirm:"):
        try:
            owner_id = int(data.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            await query.answer("Invalid or expired speedtest request.", show_alert=True)
            return
        if user_id != owner_id:
            await query.answer("This speedtest request is not yours.", show_alert=True)
            return
        if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
            await query.answer("Admin only.", show_alert=True)
            return
        if not await queue_manager.try_begin_maintenance():
            await query.answer("Try after active transfer completes.", show_alert=True)
            return
        await query.answer("Speedtest started…")
        try:
            await query.message.edit_text(
                "⚡ <b>Speedtest running…</b>\nNetwork will be fully busy for a while.",
                parse_mode=enums.ParseMode.HTML,
            )
            report = await speedtest_service.run()
            if report.photo_url:
                try:
                    speedtest_image = await download_speedtest_image(report.photo_url)
                    await query.message.reply_photo(
                        photo=speedtest_image,
                        caption=report.text,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    await query.message.edit_text(report.text, parse_mode=enums.ParseMode.HTML)
                else:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
            else:
                await query.message.edit_text(report.text, parse_mode=enums.ParseMode.HTML)
        except SpeedtestBusyError:
            await query.message.edit_text("⚠️ A speedtest is already running.")
        except SpeedtestCooldownError as exc:
            await query.message.edit_text(
                f"⏳ Cooldown active. Try after <code>{int(exc.retry_after) + 1}s</code>.",
                parse_mode=enums.ParseMode.HTML,
            )
        except SpeedtestError as exc:
            safe_error = html.escape(str(exc)[:500])
            await query.message.edit_text(
                f"❌ <b>Speedtest failed</b>\n<code>{safe_error}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        finally:
            await queue_manager.end_maintenance()
        return

    # 1. Verify ForceSub
    if data == "verify_forcesub":
        joined, missing = await check_forcesub(client, user_id)
        if joined:
            await query.answer("✅ Verification successful! You can now use the bot.", show_alert=True)
            await query.message.delete()
        else:
            await query.answer("❌ You have not joined all channels yet!", show_alert=True)
        return

    # 2. Close Menu
    if data == "close_menu":
        await query.message.delete()
        return

    # 2b. Noop (page indicator button — do nothing)
    if data == "noop":
        await query.answer()
        return

    # 2c. Queue Refresh button
    if data == "q_refresh":
        now = time.time()
        last = _refresh_cooldown.get(user_id, 0)
        if now - last < _REFRESH_COOLDOWN_SECONDS:
            remaining = int(_REFRESH_COOLDOWN_SECONDS - (now - last))
            await query.answer(f"⏳ Please wait {remaining}s before refreshing again.", show_alert=True)
            return
        _refresh_cooldown[user_id] = now
        progress_dashboard.request_refresh(user_id, force=True)
        await query.answer("🔄 Refreshed!")
        return

    # Global queue refresh
    if data == "gq_refresh":
        if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
            await query.answer("⛔ Admin-only.", show_alert=True)
            return
        now = time.time()
        last = _refresh_cooldown.get(user_id, 0)
        if now - last < _REFRESH_COOLDOWN_SECONDS:
            remaining = int(_REFRESH_COOLDOWN_SECONDS - (now - last))
            await query.answer(f"⏳ Please wait {remaining}s before refreshing again.", show_alert=True)
            return
        _refresh_cooldown[user_id] = now
        await _render_globalqueue_page(client, query.message, 1, edit=True, user_id=user_id)
        await query.answer("🔄 Refreshed!")
        return

    # Global queue pagination
    if data.startswith("gqpage:"):
        if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
            await query.answer("⛔ Admin-only.", show_alert=True)
            return
        parts = data.split(":")
        page = int(parts[1]) if len(parts) > 1 else 1
        await _render_globalqueue_page(client, query.message, page, edit=True, user_id=user_id)
        await query.answer()
        return

    # User queue pagination
    if data.startswith("qpage:"):
        parts = data.split(":")
        page = int(parts[1]) if len(parts) > 1 else 1
        await _render_queue_page(client, query.message, page, edit=True, user_id=user_id)
        await query.answer()
        return

    # 3. Settings Navigation
    if data == "open_settings":
        prefs = await db.get_preferences(user_id)
        await query.message.reply_text(
            "<b>⚙️ User Preferences & Settings</b>",
            reply_markup=build_settings_keyboard(prefs),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data == "open_history":
        total = await db.get_user_history_count(user_id)
        if not total:
            await query.answer("📭 No download history found.", show_alert=True)
            return
        _HISTORY_PER_PAGE = 10
        _HISTORY_MAX_ITEMS = 20
        page = 1
        display_total = min(total, _HISTORY_MAX_ITEMS)
        history = await db.get_user_history_paged(user_id, page=page, per_page=_HISTORY_PER_PAGE)
        total_pages = max(1, min(-(-display_total // _HISTORY_PER_PAGE), 2))

        lines = []
        buttons = []
        start_num = (page - 1) * _HISTORY_PER_PAGE + 1
        for i, rec in enumerate(history, start_num):
            title = rec.get("title", "Untitled")[:40]
            quality = rec.get("quality", "")
            mode = rec.get("mode", "video")
            size = rec.get("size_bytes", 0)
            ts = rec.get("timestamp", 0)
            size_str = f"{size / (1024*1024):.1f}MB" if size else "N/A"
            mode_icon = "🎵" if mode == "audio" else "📹"
            date_str = time.strftime("%d.%m.%y", time.localtime(ts)) if ts else "N/A"
            lines.append(f"{i}. {mode_icon} <code>{html.escape(title)}</code> | {quality} | {size_str} | {date_str}")
            vid = rec.get("video_id", "")
            buttons.append([InlineKeyboardButton(f"🔄 Resend #{i}", callback_data=f"re dl:{vid}:{quality}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"hpage:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"hpage:{page + 1}"))
        buttons.append(nav_row)
        buttons.append([InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))])

        caption = f"<b>📜 Latest Downloads</b> (showing {display_total} of {total})\n\n" + "\n".join(lines)
        await query.message.reply_text(
            caption,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        await query.answer()
        return

    if data.startswith("hpage:"):
        page = int(data.split(":")[1])
        total = await db.get_user_history_count(user_id)
        _HISTORY_PER_PAGE = 10
        _HISTORY_MAX_ITEMS = 20
        display_total = min(total, _HISTORY_MAX_ITEMS)
        total_pages = max(1, min(-(-display_total // _HISTORY_PER_PAGE), 2))
        page = max(1, min(page, total_pages))
        history = await db.get_user_history_paged(user_id, page=page, per_page=_HISTORY_PER_PAGE)

        lines = []
        buttons = []
        start_num = (page - 1) * _HISTORY_PER_PAGE + 1
        for i, rec in enumerate(history, start_num):
            title = rec.get("title", "Untitled")[:40]
            quality = rec.get("quality", "")
            mode = rec.get("mode", "video")
            size = rec.get("size_bytes", 0)
            ts = rec.get("timestamp", 0)
            size_str = f"{size / (1024*1024):.1f}MB" if size else "N/A"
            mode_icon = "🎵" if mode == "audio" else "📹"
            date_str = time.strftime("%d.%m.%y", time.localtime(ts)) if ts else "N/A"
            lines.append(f"{i}. {mode_icon} <code>{html.escape(title)}</code> | {quality} | {size_str} | {date_str}")
            vid = rec.get("video_id", "")
            buttons.append([InlineKeyboardButton(f"🔄 Resend #{i}", callback_data=f"re dl:{vid}:{quality}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"hpage:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"hpage:{page + 1}"))
        buttons.append(nav_row)
        buttons.append([InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))])

        caption = f"<b>📜 Latest Downloads</b> (showing {display_total} of {total})\n\n" + "\n".join(lines)
        try:
            await query.message.edit_text(
                caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except MessageNotModified:
            pass
        await query.answer()
        return

    if data.startswith("toggle_setting:"):
        key = data.split(":")[1]
        prefs = await db.get_preferences(user_id)
        if key == "media_preference":
            new_val = "document" if prefs.get("media_preference") == "video" else "video"
        elif key == "split_mode":
            new_val = "binary" if prefs.get("split_mode") == "streamable" else "streamable"
        elif key == "codec":
            codecs = ["AVC", "AV1", "VP9"]
            current = prefs.get("codec", "AVC")
            idx = codecs.index(current) if current in codecs else 0
            new_val = codecs[(idx + 1) % len(codecs)]
        elif key == "container":
            containers = ["MP4", "MKV"]
            current = prefs.get("container", "MP4")
            idx = containers.index(current) if current in containers else 0
            new_val = containers[(idx + 1) % len(containers)]
        else:
            new_val = None

        if new_val:
            await db.update_preference(user_id, key, new_val)
            prefs = await db.get_preferences(user_id)
            await query.message.edit_reply_markup(build_settings_keyboard(prefs))
        await query.answer(f"Updated {key} to {new_val}")
        return

    if data == "prompt_destination":
        _user_waiting_dest[user_id] = query.message.chat.id
        await query.message.reply_text(
            "🎯 <b>Send your target Channel/Group ID or Username</b>\n\n"
            "<i>Make sure you have added the bot as an Administrator with Post Messages rights first!</i>\n"
            "Example: <code>-1001234567890</code> or <code>@MyChannel</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data == "reset_destination":
        await db.update_preference(user_id, "destination_chat_id", None)
        prefs = await db.get_preferences(user_id)
        await query.message.edit_reply_markup(build_settings_keyboard(prefs))
        await query.answer("Destination reset to Private Message!", show_alert=True)
        return

    # 4. Codec Sub-Menu
    if data.startswith("menu_codec:"):
        vid = data.split(":")[1]
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        current_ext = prefs.get("container", "MP4")
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=current_codec, preferred_container=current_ext)
        kb = build_codec_keyboard(vid, current_codec, current_ext, cache["qualities"], current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=cache["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer()
        return

    if data.startswith("menu_main:"):
        vid = data.split(":")[1]
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        prefs = await db.get_preferences(user_id)
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=prefs.get("codec", "AVC"), preferred_container=prefs.get("container", "MP4"))
        kb = build_main_keyboard(vid, cache["qualities"], preferred_codec=prefs.get("codec", "AVC"), audio_formats=cache["metadata"].get("audio_formats"), current_audio_codec=prefs.get("audio_codec", "M4A"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer()
        return

    if data.startswith("set_codec:"):
        _, vid, new_codec = data.split(":")
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        await db.update_preference(user_id, "codec", new_codec)
        prefs = await db.get_preferences(user_id)
        current_ext = prefs.get("container", "MP4")
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=new_codec, preferred_container=current_ext)
        kb = build_codec_keyboard(vid, new_codec, current_ext, cache["qualities"], current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=cache["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Codec switched to {new_codec}!")
        return

    if data.startswith("set_audio_codec:"):
        _, vid, new_audio_codec = data.split(":")
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        await db.update_preference(user_id, "audio_codec", new_audio_codec)
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        current_ext = prefs.get("container", "MP4")
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=current_codec, preferred_container=current_ext)
        kb = build_codec_keyboard(vid, current_codec, current_ext, cache["qualities"], current_audio_codec=new_audio_codec, audio_formats=cache["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Audio codec switched to {new_audio_codec}!")
        return

    if data.startswith("set_ext:"):
        _, vid, new_ext = data.split(":")
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        await db.update_preference(user_id, "container", new_ext)
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=current_codec, preferred_container=new_ext)
        kb = build_codec_keyboard(vid, current_codec, new_ext, cache["qualities"], current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=cache["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Format switched to {new_ext}!")
        return

    # 5. Description Button
    if data.startswith("desc:"):
        vid = data.split(":")[1]
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        desc = cache["metadata"].get("description", "") or "No description available."
        parts = split_description(desc)
        total_parts = len(parts)
        for index, part in enumerate(parts, start=1):
            heading = "📜 Video Description" if total_parts == 1 else f"📜 Description Part {index}/{total_parts}"
            await query.message.reply_text(
                f"{heading}\n\n{part}",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.DISABLED,
            )
        await query.answer()
        return

    # 5b. Playlist Descriptions Button — opens range selector
    if data.startswith("pl_desc:"):
        pl_id = data.split(":")[1]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("Session expired, please resend playlist link.", show_alert=True)
            return

        entries = pl_data["entries"]
        total = len(entries)
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "range", "action": "description"}
        await query.message.reply_text(
            f"📜 <b>Select videos for Description</b>\n\n"
            f"Total videos: <code>{total}</code>\n\n"
            f"<b>Examples:</b>\n"
            f"<code>3</code> — video 3 only\n"
            f"<code>1-5</code> — videos 1 to 5\n"
            f"<code>1,3,7</code> — specific videos\n"
            f"<code>1,3-5,7,10-12</code> — mixed",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 5c. Playlist List Formats Button — opens range selector
    if data.startswith("pl_list:"):
        pl_id = data.split(":")[1]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("Session expired, please resend playlist link.", show_alert=True)
            return

        entries = pl_data["entries"]
        total = len(entries)
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "range", "action": "formats"}
        await query.message.reply_text(
            f"📑 <b>Select videos for Format List</b>\n\n"
            f"Total videos: <code>{total}</code>\n\n"
            f"<b>Examples:</b>\n"
            f"<code>3</code> — video 3 only\n"
            f"<code>1-5</code> — videos 1 to 5\n"
            f"<code>1,3,7</code> — specific videos\n"
            f"<code>1,3-5,7,10-12</code> — mixed",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 6. Direct Link Button ([ 🔗 Link ])
    if data.startswith("link:"):
        vid = data.split(":")[1]
        cache = await _ensure_video_cache(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        direct_url = None
        for q in cache["qualities"].values():
            if q.get("url"):
                direct_url = q["url"]
                break

        if direct_url:
            await query.message.reply_text(
                f"🔗 <b>Direct Playable Stream Link:</b>\n\n"
                f"<a href='{direct_url}'>Click here to stream video</a>\n\n"
                f"⚠️ <i>Note: Direct GoogleVideo URLs expire within a few hours.</i>",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await query.message.reply_text(
                f"👁️ <b>Watch on Poketube:</b>\nhttps://poketube.fun/watch?v={vid}",
                disable_web_page_preview=False,
            )
        await query.answer()
        return

    # 7. Formats List Button ([ 📑 List ])
    if data.startswith("list:"):
        vid = data.split(":")[1]
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        lines = ["<b>📑 Available Video Streams:</b>\n"]
        for q_name, q_val in cache["qualities"].items():
            sz = q_val.get("total_size", 0) / (1024 * 1024)
            lines.append(f"• <code>{q_name}</code> — {sz:.1f} MB | {q_val.get('fps', 30)}fps | Codec: {q_val.get('codec', 'AVC')}")

        await query.message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
        await query.answer()
        return



    # 8. Screenshots Generator Button ([ 📸 Screenshots ]) — Quality Selection
    if data.startswith("shots:"):
        vid = data.split(":")[1]
        cache = await _ensure_video_cache(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        qualities = cache.get("qualities", {})
        video_heights = sorted(
            [int(q.replace("p", "")) for q in qualities.keys() if q.endswith("p") and q[:-1].isdigit()],
        )
        if not video_heights:
            await query.answer("No video qualities available for screenshots.", show_alert=True)
            return

        rows = []
        row = []
        for h in video_heights:
            row.append(InlineKeyboardButton(
                f"📹 {h}p",
                callback_data=f"shots_go:{vid}:{h}p",
                **_style_kwargs("primary")
            ))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("✖️ Cancel", callback_data="close_menu", **_style_kwargs("danger"))])

        await query.message.reply_text(
            f"📸 <b>Select Screenshot Quality</b>\n\n"
            f"<i>Choose which quality stream to extract frames from:</i>",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 8a. Screenshots execution with selected quality
    if data.startswith("shots_go:"):
        parts = data.split(":")
        vid = parts[1]
        selected_quality = parts[2] if len(parts) > 2 else ""
        cache = await _ensure_video_cache(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        screenshot_key = (query.message.chat.id, vid)
        if screenshot_key in _active_screenshot_jobs:
            await query.answer("📸 Screenshots are already being prepared.", show_alert=True)
            return
        if not await queue_manager.try_begin_auxiliary():
            await query.answer("⚡ Speedtest running. Try after it completes.", show_alert=True)
            return
        _active_screenshot_jobs.add(screenshot_key)

        status_msg = await query.message.reply_text(
            f"📸 <i>Extracting screenshots at <b>{html.escape(selected_quality)}</b>...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()

        work_dir = os.path.join(Config.DOWNLOAD_DIR, f"shots_{vid}_{uuid.uuid4().hex[:6]}")
        os.makedirs(work_dir, exist_ok=True)
        try:
            shots = []
            metadata = cache.get("metadata", {})
            qualities = cache.get("qualities", {})
            duration = float(metadata.get("duration_sec") or 0)

            direct_url, quality_label = "", ""
            q_data = qualities.get(selected_quality, {})
            entries = [q_data, *((q_data.get("formats_by_codec") or {}).values())]
            for entry in entries:
                url = str((entry or {}).get("url") or "").strip()
                h = int((entry or {}).get("height") or 0)
                if url and h > 0 and url.startswith("http") and ".m3u8" not in url:
                    direct_url = url
                    quality_label = f"{h}p"
                    break

            if not direct_url:
                direct_url, quality_label = select_screenshot_source(metadata, qualities)

            if direct_url:
                shots = await generate_screenshots(
                    direct_url,
                    work_dir,
                    count=5,
                    duration_hint=duration,
                )

            if not shots:
                await status_msg.edit_text(
                    "❌ Full-duration stream URL is not available; first-10s screenshots were not sent."
                )
                return

            if shots:
                title_text = html.escape(str(cache['metadata'].get('title') or 'Untitled'))
                quality_text = f" | 📺 <b>{html.escape(quality_label)}</b>" if quality_label else ""
                caption = (
                    f"📸 <b>Screenshots:</b> {title_text}{quality_text}\n"
                    "<i>Timeline: 10% • 30% • 50% • 70% • 90%</i>"
                )
                if len(shots) == 1:
                    await client.send_photo(
                        chat_id=query.message.chat.id,
                        photo=shots[0],
                        caption=caption,
                        reply_to_message_id=query.message.id,
                    )
                else:
                    media_group = [
                        InputMediaPhoto(media=s, caption=caption if i == 0 else "")
                        for i, s in enumerate(shots)
                    ]
                    await client.send_media_group(chat_id=query.message.chat.id, media=media_group, reply_to_message_id=query.message.id)
                await status_msg.delete()
                return

            await status_msg.edit_text("❌ Could not extract frame screenshots for this video.")
        except Exception as e:
            logger.error(f"Error generating screenshots: {e}")
            await status_msg.edit_text(f"❌ Error: {e}")
        finally:
            _active_screenshot_jobs.discard(screenshot_key)
            await queue_manager.end_auxiliary()
            shutil.rmtree(work_dir, ignore_errors=True)
        return

    # 9. Rich MediaInfo Generator Button ([ ℹ️ MediaInfo ])
    if data.startswith("minfo:"):
        vid = data.split(":")[1]
        cache = await _ensure_video_cache(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return

        status_msg = await query.message.reply_text("ℹ️ <i>Extracting MediaInfo for all qualities...</i>", parse_mode=enums.ParseMode.HTML)
        await query.answer()

        work_dir = os.path.join(Config.DOWNLOAD_DIR, f"minfo_{vid}_{uuid.uuid4().hex[:6]}")
        os.makedirs(work_dir, exist_ok=True)
        try:
            metadata = cache.get("metadata", {})
            qualities = cache.get("qualities", {})

            if not metadata or not qualities:
                await status_msg.edit_text("❌ <b>No format data available for this video.</b>", parse_mode=enums.ParseMode.HTML)
                return

            summary, full_report, premium_md = build_all_qualities_mediainfo(metadata, qualities)

            # Upload styled markdown
            paste_url = None
            if premium_md:
                paste_url = await upload_to_pastyx(
                    content=premium_md,
                    title=f"MediaInfo — {metadata.get('title', '')[:50]}",
                    author="@NexTubeBot",
                )

            kb_buttons = []
            if paste_url:
                kb_buttons.append([InlineKeyboardButton("📊 View MediaInfo", url=paste_url)])
            kb_buttons.append([InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))])

            # Save text report file — guard against empty reports
            txt_file = os.path.join(work_dir, "mediainfo.txt")
            if full_report:
                with open(txt_file, "w", encoding="utf-8") as f:
                    f.write(full_report)

            # Telegram caption limit is 1024 characters
            if len(summary) > 1024:
                summary = summary[:1020] + "..."

            # Only send document if the file has content; otherwise send text-only
            if full_report and os.path.exists(txt_file) and os.path.getsize(txt_file) > 0:
                await client.send_document(
                    chat_id=query.message.chat.id,
                    document=txt_file,
                    caption=summary,
                    reply_markup=InlineKeyboardMarkup(kb_buttons),
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=query.message.id,
                )
            else:
                await query.message.reply_text(
                    summary,
                    reply_markup=InlineKeyboardMarkup(kb_buttons),
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=query.message.id,
                )
            await status_msg.delete()
            return

        except Exception as e:
            logger.error(f"Error extracting mediainfo: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Error: {e}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return

    # 10. Cookie Management Callbacks
    if data.startswith("chk_cookie:"):
        cname = data.split(":")[1]
        st = await cookie_manager.validate_cookie(cname)
        status_text = "VALID & WORKING" if st == "valid" else ("DEGRADED" if st == "degraded" else ("EXPIRED" if st == "expired" else st.upper()))
        await query.answer(f"🍪 {cname}: {status_text}", show_alert=True)
        try:
            text, kb = get_cookies_panel(user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return

    if data.startswith("show_cookie:"):
        cname = data.split(":")[1]
        cookie_path = os.path.join(Config.COOKIES_DIR, cname)
        if not os.path.exists(cookie_path):
            await query.answer("Cookie file not found!", show_alert=True)
            return
        try:
            await client.send_document(
                chat_id=query.message.chat.id,
                document=cookie_path,
                caption=f"🍪 <b>{cname}</b>",
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=query.message.id,
            )
        except Exception as e:
            await query.answer(f"Error sending file: {e}", show_alert=True)
        await query.answer()
        return

    if data == "open_cookies":
        if user_id not in Config.ADMIN_ID and user_id != Config.OWNER_ID:
            await query.answer("Admin only!", show_alert=True)
            return
        text, kb = get_cookies_panel(user_id)
        await query.message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        await query.answer()
        return

    if data == "add_cookie_prompt":
        is_admin = (user_id in Config.ADMIN_ID) or (user_id == Config.OWNER_ID)
        if not is_admin:
            await query.answer("Admin only!", show_alert=True)
            return
        await query.message.reply_text(
            "🍪 <b>How to Add a New Cookie File:</b>\n\n"
            "1. Export your YouTube cookies using <b>Cookie-Editor</b> extension in <b>Netscape</b> format.\n"
            "2. Send the exported <code>.txt</code> file here as a document in this private chat.\n"
            "3. The bot will auto-sanitize <code>#HttpOnly_</code> prefixes, validate against YouTube, and add to active rotation pool!",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data == "del_cookie_menu":
        is_admin = (user_id in Config.ADMIN_ID) or (user_id == Config.OWNER_ID)
        if not is_admin:
            await query.answer("Admin only!", show_alert=True)
            return
        all_cookies = cookie_manager.get_all_cookies()
        await query.message.edit_text(
            "🗑️ <b>Select Cookie File to Delete:</b>",
            reply_markup=build_cookie_delete_keyboard(all_cookies),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data.startswith("del_cookie_req:"):
        cname = data.split(":")[1]
        await query.message.edit_text(
            f"⚠️ <b>Are you sure you want to delete <code>{cname}</code>?</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm Delete", callback_data=f"del_cookie_confirm:{cname}", **_style_kwargs("danger"))],
                [InlineKeyboardButton("❌ Cancel", callback_data="open_cookies", **_style_kwargs("danger"))],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data.startswith("del_cookie_confirm:"):
        cname = data.split(":")[1]
        ok, msg = cookie_manager.delete_cookie_file(cname)
        await query.answer(msg, show_alert=True)
        text, kb = get_cookies_panel(user_id)
        await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return

    if data.startswith("set_resolver:"):
        is_admin = (user_id in Config.ADMIN_ID) or (user_id == Config.OWNER_ID)
        if not is_admin:
            await query.answer("Admin only!", show_alert=True)
            return
        cname = data.split(":", 1)[1]
        if cname == "none":
            cookie_manager.set_resolver_cookie(None)
            try:
                await db.set_resolver_cookie(None)
            except Exception:
                pass
            await query.answer("📌 Resolver cookie removed!", show_alert=True)
        else:
            cookie_manager.set_resolver_cookie(cname)
            try:
                await db.set_resolver_cookie(cname)
            except Exception:
                pass
            await query.answer(f"📌 {cname} set as resolver cookie!", show_alert=True)
        try:
            text, kb = get_cookies_panel(user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return

    # ── Multi-DB Callbacks ──
    if data == "db_add_prompt":
        if user_id != Config.OWNER_ID:
            await query.answer("Owner only!", show_alert=True)
            return
        _user_waiting_db_uri.add(user_id)
        await query.message.reply_text(
            "➕ <b>Send Secondary MongoDB URI</b>\n\n"
            "Format: <code>mongodb+srv://user:pass@host/dbname</code>\n\n"
            "<i>The bot will validate the connection before saving.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data == "db_remove_menu":
        if user_id != Config.OWNER_ID:
            await query.answer("Owner only!", show_alert=True)
            return
        sec_uris = db.get_secondary_uris()
        if not sec_uris:
            await query.answer("No secondary databases to remove.", show_alert=True)
            return
        rows = []
        for uri in sec_uris:
            rows.append([InlineKeyboardButton(f"🗑️ {uri}", callback_data=f"db_rm:{uri}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="db_back")])
        rows.append([InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))])
        await query.message.edit_text(
            "🗑️ <b>Select Database to Remove:</b>",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data.startswith("db_rm:"):
        if user_id != Config.OWNER_ID:
            await query.answer("Owner only!", show_alert=True)
            return
        masked_uri = data[6:]
        actual_uri = None
        for client, _ in db._secondary_clients:
            uri = getattr(client, "_secondary_uri", "")
            if uri:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(uri)
                    if parsed.password:
                        masked = uri.replace(parsed.password, "***")
                    else:
                        masked = uri
                    if masked == masked_uri:
                        actual_uri = uri
                        break
                except Exception:
                    pass
        if actual_uri:
            ok, msg = await db.remove_secondary_db(actual_uri)
            await query.answer(msg, show_alert=True)
        else:
            await query.answer("URI not found.", show_alert=True)
        sec_uris = db.get_secondary_uris()
        sec_count = len(sec_uris)
        sec_list = ""
        if sec_uris:
            for i, uri in enumerate(sec_uris, 1):
                sec_list += f"  {i}. <code>{uri}</code>\n"
        else:
            sec_list = "  <i>None configured</i>"
        try:
            await query.message.edit_text(
                f"<b>🗄️ Multi-Database Manager</b>\n\n"
                f"<b>Primary DB:</b>\n  <code>{db._primary_uri[:50]}...</code>\n\n"
                f"<b>Secondary DBs:</b> <code>{sec_count}</code>\n{sec_list}\n"
                f"<i>Secondary DBs provide backup + failover for cache data.</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Database", callback_data="db_add_prompt")],
                    [InlineKeyboardButton("➖ Remove Database", callback_data="db_remove_menu")],
                    [InlineKeyboardButton("🔄 Refresh", callback_data="db_refresh")],
                    [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
                ]),
            )
        except Exception:
            pass
        return

    if data == "db_refresh":
        if user_id != Config.OWNER_ID:
            await query.answer("Owner only!", show_alert=True)
            return
        sec_uris = db.get_secondary_uris()
        sec_count = len(sec_uris)
        sec_list = ""
        if sec_uris:
            for i, uri in enumerate(sec_uris, 1):
                sec_list += f"  {i}. <code>{uri}</code>\n"
        else:
            sec_list = "  <i>None configured</i>"
        try:
            await query.message.edit_text(
                f"<b>🗄️ Multi-Database Manager</b>\n\n"
                f"<b>Primary DB:</b>\n  <code>{db._primary_uri[:50]}...</code>\n\n"
                f"<b>Secondary DBs:</b> <code>{sec_count}</code>\n{sec_list}\n"
                f"<i>Secondary DBs provide backup + failover for cache data.</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Database", callback_data="db_add_prompt")],
                    [InlineKeyboardButton("➖ Remove Database", callback_data="db_remove_menu")],
                    [InlineKeyboardButton("🔄 Refresh", callback_data="db_refresh")],
                    [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
                ]),
            )
        except Exception:
            pass
        await query.answer("Refreshed!")
        return

    if data == "db_back":
        if user_id != Config.OWNER_ID:
            await query.answer("Owner only!", show_alert=True)
            return
        sec_uris = db.get_secondary_uris()
        sec_count = len(sec_uris)
        sec_list = ""
        if sec_uris:
            for i, uri in enumerate(sec_uris, 1):
                sec_list += f"  {i}. <code>{uri}</code>\n"
        else:
            sec_list = "  <i>None configured</i>"
        try:
            await query.message.edit_text(
                f"<b>🗄️ Multi-Database Manager</b>\n\n"
                f"<b>Primary DB:</b>\n  <code>{db._primary_uri[:50]}...</code>\n\n"
                f"<b>Secondary DBs:</b> <code>{sec_count}</code>\n{sec_list}\n"
                f"<i>Secondary DBs provide backup + failover for cache data.</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Database", callback_data="db_add_prompt")],
                    [InlineKeyboardButton("➖ Remove Database", callback_data="db_remove_menu")],
                    [InlineKeyboardButton("🔄 Refresh", callback_data="db_refresh")],
                    [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
                ]),
            )
        except Exception:
            pass
        await query.answer()
        return

    # 10b. Other quality button — show codec sub-menu
    if data.startswith("other:"):
        vid = data.split(":")[1]
        cache = _video_cache.get(vid)
        if not cache:
            await query.answer("Session expired, please resend link.", show_alert=True)
            return
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        current_ext = prefs.get("container", "MP4")
        caption = build_metadata_caption(cache["metadata"], cache["qualities"], preferred_codec=current_codec, preferred_container=current_ext)
        kb = build_codec_keyboard(vid, current_codec, current_ext, cache["qualities"], current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=cache["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer()
        return

    # 10c. Leaderboard period filter
    # 10e. Retry failed download
    if data.startswith("retry:"):
        token = data.split(":")[1]
        params = _consume_retry_token(token, user_id)
        if not params:
            await query.answer("⚠️ Retry token expired or invalid. Send a new link.", show_alert=True)
            return
        await query.answer("🔄 Retrying download...", show_alert=False)
        fake_msg = Message(
            id=0,
            date=None,
            chat=query.message.chat,
            text=params["url"],
            from_user=query.from_user,
        )
        fake_msg._client = client
        # Apply saved preferences
        if params.get("codec"):
            await db.update_preference(user_id, "codec", params["codec"])
        if params.get("container"):
            await db.update_preference(user_id, "container", params["container"])
        await youtube_link_handler(client, fake_msg)
        return

    if data == "open_leaderboard":
        leaders = await db.get_leaderboard(limit=10, period="all")
        user_stats = await db.get_user_rank(user_id) if query.from_user else {"rank": "N/A", "total_downloads": 0, "total_bytes": 0}

        crown = Emojis.tag(Emojis.CROWN, "👑")
        top_badge = Emojis.tag(Emojis.TOP, "🔝")
        badges = ["🥇", "🥈", "🥉", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅"]

        text = f"{crown} <b>TOP 10 DOWNLOADERS — ALL-TIME</b> {top_badge}\n\n"
        if leaders:
            for idx, l in enumerate(leaders):
                b = badges[idx] if idx < len(badges) else "•"
                uid = l.get("user_id", "")
                name = l.get("first_name") or l.get("username") or f"User {uid}"
                username = l.get("username", "")
                if username:
                    display = f"{name} (@{username})"
                else:
                    display = f"{name} (<code>{uid}</code>)"
                gb = l.get("total_bytes", 0) / (1024 ** 3)
                text += f"{b} <b>{idx+1}. {display}</b>\n   <code>{l['total_downloads']} Videos ({gb:.2f} GB)</code>\n"
        else:
            text += "<i>No downloads recorded yet!</i>\n"

        my_gb = user_stats.get("total_bytes", 0) / (1024 ** 3)
        text += (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Your Rank:</b> #{user_stats.get('rank', 'N/A')} "
            f"(<code>{user_stats.get('total_downloads', 0)} Videos | {my_gb:.2f} GB</code>)"
        )

        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Weekly", callback_data="lb:weekly"),
                    InlineKeyboardButton("Monthly", callback_data="lb:monthly"),
                    InlineKeyboardButton("All-Time", callback_data="lb:all"),
                ],
                [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data.startswith("lb:"):
        period = data.split(":")[1]
        leaders = await db.get_leaderboard(limit=10, period=period)
        user_stats = await db.get_user_rank(user_id) if query.from_user else {"rank": "N/A", "total_downloads": 0, "total_bytes": 0}

        crown = Emojis.tag(Emojis.CROWN, "👑")
        top_badge = Emojis.tag(Emojis.TOP, "🔝")
        badges = ["🥇", "🥈", "🥉", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅"]
        period_labels = {"weekly": "WEEKLY", "monthly": "MONTHLY", "all": "ALL-TIME"}

        text = f"{crown} <b>TOP 10 DOWNLOADERS — {period_labels.get(period, 'ALL-TIME')}</b> {top_badge}\n\n"
        if leaders:
            for idx, l in enumerate(leaders):
                b = badges[idx] if idx < len(badges) else "•"
                uid = l.get("user_id", "")
                name = l.get("first_name") or l.get("username") or f"User {uid}"
                username = l.get("username", "")
                if username:
                    display = f"{name} (@{username})"
                else:
                    display = f"{name} (<code>{uid}</code>)"
                gb = l.get("total_bytes", 0) / (1024 ** 3)
                text += f"{b} <b>{idx+1}. {display}</b>\n   <code>{l['total_downloads']} Videos ({gb:.2f} GB)</code>\n"
        else:
            text += "<i>No downloads recorded for this period!</i>\n"

        my_gb = user_stats.get("total_bytes", 0) / (1024 ** 3)
        text += (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Your Rank:</b> #{user_stats.get('rank', 'N/A')} "
            f"(<code>{user_stats.get('total_downloads', 0)} Videos | {my_gb:.2f} GB</code>)"
        )

        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Weekly", callback_data="lb:weekly"),
                    InlineKeyboardButton("Monthly", callback_data="lb:monthly"),
                    InlineKeyboardButton("All-Time", callback_data="lb:all"),
                ],
            [InlineKeyboardButton("✖️ Close", callback_data="close_menu", **_style_kwargs("danger"))],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 10d. Re-download from history — check dump channel cache first
    if data.startswith("re dl:"):
        parts = data.split(":")
        if len(parts) >= 3:
            re_vid = parts[1]
            re_quality = parts[2]

            re_cached = await db.get_cached_media_simple(re_vid, re_quality)
            if re_cached and re_cached.get("message_ids") and re_cached.get("dump_chat_id"):
                await query.answer("⚡ Sending from cache instantly...", show_alert=False)
                re_dest = await get_target_upload_chat(user_id, query.message.chat.id, query.message.chat.type)
                re_reply_id = query.message.id if re_dest == query.message.chat.id else None
                for re_msg_id in re_cached["message_ids"]:
                    try:
                        await flood_retry(
                            client.copy_message,
                            chat_id=re_dest,
                            from_chat_id=re_cached["dump_chat_id"],
                            message_id=re_msg_id,
                            reply_to_message_id=re_reply_id,
                        )
                    except Exception as exc:
                        logger.error(f"History resend cache copy failed {re_vid}: {exc}")
                re_title = re_cached.get("title", "")
                re_size = re_cached.get("size_bytes", 0)
                await db.record_download(user_id, re_vid, re_title, re_size, quality=re_quality)
            else:
                # Cache miss — fall back to full download
                video_url = f"https://youtu.be/{re_vid}"
                await query.answer("🔄 Cache miss, starting re-download...", show_alert=False)
                fake_msg = Message(
                    id=0,
                    date=None,
                    chat=query.message.chat,
                    text=video_url,
                    from_user=query.from_user,
                )
                fake_msg._client = client
                await youtube_link_handler(client, fake_msg)
        return

    # 10f. Playlist download handlers
    if data.startswith("pldl:"):
        parts = data.split(":")
        if len(parts) >= 3:
            pl_id = parts[1]
            range_str = parts[2]
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data:
                await query.answer("⚠️ Playlist data has expired. Send the link again.", show_alert=True)
                return
            entries = pl_data["entries"]
            total = len(entries)

            try:
                selected = _parse_playlist_range(range_str, total)
            except ValueError as e:
                await query.answer(str(e).replace("<code>", "").replace("</code>", ""), show_alert=True)
                return

            if not selected:
                await query.answer("⚠️ No videos in this range.", show_alert=True)
                return

            status_msg = await query.message.reply_text(
                f"📑 <b>Starting {len(selected)} videos from playlist...</b>\n"
                f"Progress: 0/{len(selected)}",
                parse_mode=enums.ParseMode.HTML,
            )
            started = 0
            for idx in selected:
                entry = entries[idx]
                entry_url = entry.get("url") or f"https://youtu.be/{entry.get('id', '')}"
                if not entry_url or not entry_url.startswith("http"):
                    continue
                try:
                    asyncio.create_task(playlist_auto_download(
                        client, query.from_user, query.message.chat, entry_url,
                    ))
                    started += 1
                    if started % 2 == 0 or started == len(selected):
                        await status_msg.edit_text(
                            f"📑 <b>Starting videos from playlist...</b>\n"
                            f"Progress: {started}/{len(selected)} started",
                            parse_mode=enums.ParseMode.HTML,
                        )
                except Exception:
                    pass
                await asyncio.sleep(0.3)
            await status_msg.edit_text(
                f"✅ <b>Playlist started!</b> {started}/{len(selected)} videos queued for download.",
                parse_mode=enums.ParseMode.HTML,
            )
        return

    if data.startswith("pl_custom:"):
        pl_id = data.split(":")[1]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("⚠️ Playlist data has expired.", show_alert=True)
            return
        total = len(pl_data["entries"])
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "range"}
        await query.message.reply_text(
            f"🔢 <b>Enter video selection</b>\n\n"
            f"Total videos: <code>{total}</code>\n\n"
            f"<b>Examples:</b>\n"
            f"<code>3</code> — video 3 only\n"
            f"<code>1-5</code> — videos 1 to 5\n"
            f"<code>1,3,7</code> — specific videos\n"
            f"<code>1,3-5,7,10-12</code> — mixed",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    if data.startswith("pl_pick:"):
        pl_id = data.split(":")[1]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("⚠️ Playlist data has expired.", show_alert=True)
            return
        total = len(pl_data["entries"])
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "pick"}
        await query.message.reply_text(
            f"🎯 <b>Select videos to download</b>\n\n"
            f"Total videos: <code>{total}</code>\n\n"
            f"<b>Examples:</b>\n"
            f"<code>3</code> — video 3 only\n"
            f"<code>1-5</code> — videos 1 to 5\n"
            f"<code>1,3,7</code> — specific videos\n"
            f"<code>1,3-5,7,10-12</code> — mixed",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 10f-b. Playlist Quality Selection Callbacks
    if data.startswith("pldl_start:"):
        parts = data.split(":")
        if len(parts) >= 3:
            pl_id = parts[1]
            quality = parts[2]
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data:
                await query.answer("⚠️ Playlist data has expired. Send the link again.", show_alert=True)
                return
            entries = pl_data["entries"]
            total = len(entries)
            pl_title = pl_data.get("title", "Playlist")

            # Store selected quality for this playlist
            _playlist_selected_quality[pl_id] = quality

            # Show video selection keyboard (Download All / Custom Range / Pick Videos)
            kb = build_playlist_download_keyboard(pl_id, quality, total, pl_title)

            # Update the message with selection options
            try:
                await query.message.edit_text(
                    f"📑 <b>Playlist Ready!</b>\n\n"
                    f"• <b>Title:</b> {html.escape(pl_title)}\n"
                    f"• <b>Total Videos:</b> <code>{total}</code>\n"
                    f"• <b>Selected Quality:</b> <code>{quality}</code>\n\n"
                    f"👇 Choose which videos to download:",
                    reply_markup=kb,
                    parse_mode=enums.ParseMode.HTML,
                )
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_caption(
                        caption=f"📑 <b>Playlist Ready!</b>\n\n"
                        f"• <b>Title:</b> {html.escape(pl_title)}\n"
                        f"• <b>Total Videos:</b> <code>{total}</code>\n"
                        f"• <b>Selected Quality:</b> <code>{quality}</code>\n\n"
                        f"👇 Choose which videos to download:",
                        reply_markup=kb,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass
            await query.answer()
        return

    if data.startswith("pl_codec:"):
        try:
            parts = data.split(":")
            pl_id = parts[1]
            pl_q_data = _playlist_quality_cache.get(pl_id)
            if not pl_q_data:
                pl_data = _playlist_cache.get(pl_id)
                if not pl_data or not pl_data.get("entries"):
                    await query.answer("Session expired, resend playlist link.", show_alert=True)
                    return
                entry = pl_data["entries"][0]
                entry_vid = entry.get("id", "")
                entry_url = entry.get("url") or f"https://youtu.be/{entry_vid}"
                if not entry_vid:
                    entry_vid = extract_video_id(entry_url) or ""
                if not entry_vid:
                    await query.answer("Could not resolve first video.", show_alert=True)
                    return
                cache = await _ensure_video_cache(entry_vid, entry_url)
                if not cache:
                    await query.answer("Failed to extract video metadata.", show_alert=True)
                    return
                pl_q_data = {
                    "first_vid": entry_vid,
                    "metadata": cache["metadata"],
                    "qualities": cache["qualities"],
                    "url": cache["url"],
                }
                _playlist_quality_cache[pl_id] = pl_q_data
            prefs = await db.get_preferences(user_id)
            current_codec = prefs.get("codec", "AVC")
            current_ext = prefs.get("container", "MP4")
            pl_data = _playlist_cache.get(pl_id)
            total_count = len(pl_data["entries"]) if pl_data else 0
            caption = build_metadata_caption(pl_q_data["metadata"], pl_q_data["qualities"], preferred_codec=current_codec, preferred_container=current_ext)
            pl_info_text = f"📑 <b>YouTube Playlist</b> • <code>{total_count} videos</code>\n\n"
            caption = pl_info_text + caption
            first_vid = pl_q_data["first_vid"]
            kb = build_playlist_codec_keyboard(pl_id, first_vid, current_codec, current_ext, pl_q_data["qualities"], total_count=total_count, current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=pl_q_data["metadata"].get("audio_formats"))
            try:
                await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                except MessageNotModified:
                    pass
                except Exception:
                    try:
                        await query.message.edit_reply_markup(kb)
                    except MessageNotModified:
                        pass
            await query.answer()
        except Exception as e:
            try:
                await query.answer(f"Error: {str(e)[:50]}", show_alert=True)
            except Exception:
                pass
        return

    if data.startswith("pl_set_codec:"):
        _, pl_id, new_codec = data.split(":")
        pl_q_data = _playlist_quality_cache.get(pl_id)
        if not pl_q_data:
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data or not pl_data.get("entries"):
                await query.answer("Session expired.", show_alert=True)
                return
            entry = pl_data["entries"][0]
            first_vid = entry.get("id", "")
            entry_url = entry.get("url") or f"https://youtu.be/{first_vid}"
            if not first_vid:
                first_vid = extract_video_id(entry_url) or ""
            cache = await _ensure_video_cache(first_vid, entry_url)
            if not cache:
                await query.answer("Failed to extract.", show_alert=True)
                return
            pl_q_data = {"first_vid": first_vid, "metadata": cache["metadata"], "qualities": cache["qualities"], "url": cache["url"]}
            _playlist_quality_cache[pl_id] = pl_q_data
        first_vid = pl_q_data["first_vid"]
        await db.update_preference(user_id, "codec", new_codec)
        prefs = await db.get_preferences(user_id)
        current_ext = prefs.get("container", "MP4")
        pl_data = _playlist_cache.get(pl_id)
        total_count = len(pl_data["entries"]) if pl_data else 0
        caption = build_metadata_caption(pl_q_data["metadata"], pl_q_data["qualities"], preferred_codec=new_codec, preferred_container=current_ext)
        pl_info = f"📑 <b>YouTube Playlist</b> • <code>{total_count} videos</code>\n\n"
        caption = pl_info + caption
        kb = build_playlist_codec_keyboard(pl_id, first_vid, new_codec, current_ext, pl_q_data["qualities"], total_count=total_count, current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=pl_q_data["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Codec switched to {new_codec}!")
        return

    if data.startswith("pl_set_ext:"):
        _, pl_id, new_ext = data.split(":")
        pl_q_data = _playlist_quality_cache.get(pl_id)
        if not pl_q_data:
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data or not pl_data.get("entries"):
                await query.answer("Session expired.", show_alert=True)
                return
            entry = pl_data["entries"][0]
            first_vid = entry.get("id", "")
            entry_url = entry.get("url") or f"https://youtu.be/{first_vid}"
            if not first_vid:
                first_vid = extract_video_id(entry_url) or ""
            cache = await _ensure_video_cache(first_vid, entry_url)
            if not cache:
                await query.answer("Failed to extract.", show_alert=True)
                return
            pl_q_data = {"first_vid": first_vid, "metadata": cache["metadata"], "qualities": cache["qualities"], "url": cache["url"]}
            _playlist_quality_cache[pl_id] = pl_q_data
        first_vid = pl_q_data["first_vid"]
        await db.update_preference(user_id, "container", new_ext)
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        pl_data = _playlist_cache.get(pl_id)
        total_count = len(pl_data["entries"]) if pl_data else 0
        caption = build_metadata_caption(pl_q_data["metadata"], pl_q_data["qualities"], preferred_codec=current_codec, preferred_container=new_ext)
        pl_info = f"📑 <b>YouTube Playlist</b> • <code>{total_count} videos</code>\n\n"
        caption = pl_info + caption
        kb = build_playlist_codec_keyboard(pl_id, first_vid, current_codec, new_ext, pl_q_data["qualities"], total_count=total_count, current_audio_codec=prefs.get("audio_codec", "M4A"), audio_formats=pl_q_data["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Format switched to {new_ext}!")
        return

    if data.startswith("pl_set_audio_codec:"):
        _, pl_id, new_audio_codec = data.split(":")
        pl_q_data = _playlist_quality_cache.get(pl_id)
        if not pl_q_data:
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data or not pl_data.get("entries"):
                await query.answer("Session expired.", show_alert=True)
                return
            entry = pl_data["entries"][0]
            first_vid = entry.get("id", "")
            entry_url = entry.get("url") or f"https://youtu.be/{first_vid}"
            if not first_vid:
                first_vid = extract_video_id(entry_url) or ""
            cache = await _ensure_video_cache(first_vid, entry_url)
            if not cache:
                await query.answer("Failed to extract.", show_alert=True)
                return
            pl_q_data = {"first_vid": first_vid, "metadata": cache["metadata"], "qualities": cache["qualities"], "url": cache["url"]}
            _playlist_quality_cache[pl_id] = pl_q_data
        first_vid = pl_q_data["first_vid"]
        await db.update_preference(user_id, "audio_codec", new_audio_codec)
        prefs = await db.get_preferences(user_id)
        current_codec = prefs.get("codec", "AVC")
        current_ext = prefs.get("container", "MP4")
        pl_data = _playlist_cache.get(pl_id)
        total_count = len(pl_data["entries"]) if pl_data else 0
        caption = build_metadata_caption(pl_q_data["metadata"], pl_q_data["qualities"], preferred_codec=current_codec, preferred_container=current_ext)
        pl_info = f"📑 <b>YouTube Playlist</b> • <code>{total_count} videos</code>\n\n"
        caption = pl_info + caption
        kb = build_playlist_codec_keyboard(pl_id, first_vid, current_codec, current_ext, pl_q_data["qualities"], total_count=total_count, current_audio_codec=new_audio_codec, audio_formats=pl_q_data["metadata"].get("audio_formats"))
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer(f"Audio codec switched to {new_audio_codec}!")
        return

    if data.startswith("pl_back:"):
        parts = data.split(":")
        pl_id = parts[1]
        pl_q_data = _playlist_quality_cache.get(pl_id)
        first_vid = pl_q_data["first_vid"] if pl_q_data else (parts[2] if len(parts) > 2 else "")
        pl_data = _playlist_cache.get(pl_id)
        total_count = len(pl_data["entries"]) if pl_data else 0
        if pl_q_data:
            prefs = await db.get_preferences(user_id)
            caption = build_metadata_caption(pl_q_data["metadata"], pl_q_data["qualities"], preferred_codec=prefs.get("codec"), preferred_container=prefs.get("container"))
            pl_info_text = f"📑 <b>YouTube Playlist</b> • <code>{total_count} videos</code>\n\n"
            caption = pl_info_text + caption
            kb = build_playlist_quality_keyboard(pl_id, first_vid, pl_q_data["qualities"], preferred_codec=prefs.get("codec"), total_count=total_count, audio_formats=pl_q_data["metadata"].get("audio_formats"), current_audio_codec=prefs.get("audio_codec", "M4A"))
        elif pl_data:
            caption = build_playlist_caption(pl_data)
            kb = build_playlist_quality_keyboard(pl_id, first_vid, qualities=None, total_count=total_count)
        else:
            await query.answer("Session expired, resend playlist link.", show_alert=True)
            return
        try:
            await query.message.edit_caption(caption=caption[:1015] if len(caption) > 1020 else caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.edit_text(text=caption, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except Exception:
                try:
                    await query.message.edit_reply_markup(kb)
                except MessageNotModified:
                    pass
        await query.answer()
        return

    # 10f-c. Playlist Download Go — actual download after video selection
    if data.startswith("pldl_go:"):
        parts = data.split(":")
        if len(parts) >= 4:
            pl_id = parts[1]
            quality = parts[2]
            range_str = parts[3]
            pl_data = _playlist_cache.get(pl_id)
            if not pl_data:
                await query.answer("⚠️ Playlist data has expired. Send the link again.", show_alert=True)
                return
            entries = pl_data["entries"]
            total = len(entries)

            try:
                selected = _parse_playlist_range(range_str, total)
            except ValueError as e:
                await query.answer(str(e).replace("<code>", "").replace("</code>", ""), show_alert=True)
                return

            if not selected:
                await query.answer("⚠️ No videos in this range.", show_alert=True)
                return

            await query.answer("🚀 Starting playlist download...")
            asyncio.create_task(_execute_playlist_batch(
                client, query, pl_id, selected, quality,
            ))
        return

    # 10f-d. Playlist Custom Range with Quality
    if data.startswith("pl_custom_q:"):
        parts = data.split(":")
        pl_id = parts[1]
        quality = parts[2]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("⚠️ Playlist data has expired.", show_alert=True)
            return
        total = len(pl_data["entries"])
        _playlist_selected_quality[pl_id] = quality
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "range"}
        await query.message.reply_text(
            f"🔢 <b>Enter video selection</b>\n\n"
            f"Total videos: <code>{total}</code>\n"
            f"Quality: <code>{quality}</code>\n\n"
            f"<b>Examples:</b>\n"
            f"<code>3</code> — video 3 only\n"
            f"<code>1-5</code> — videos 1 to 5\n"
            f"<code>1,3,7</code> — specific videos\n"
            f"<code>1,3-5,7,10-12</code> — mixed",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 10f-e. Playlist Pick Videos with Quality
    if data.startswith("pl_pick_q:"):
        parts = data.split(":")
        pl_id = parts[1]
        quality = parts[2]
        pl_data = _playlist_cache.get(pl_id)
        if not pl_data:
            await query.answer("⚠️ Playlist data has expired.", show_alert=True)
            return
        total = len(pl_data["entries"])
        _playlist_selected_quality[pl_id] = quality
        _user_playlist_input[user_id] = {"pl_id": pl_id, "total": total, "mode": "pick"}
        await query.message.reply_text(
            f"🎯 <b>Select videos to download</b>\n\n"
            f"Total videos: <code>{total}</code>\n"
            f"Quality: <code>{quality}</code>\n"
            f"Format: <code>1,3,7,10</code> (comma-separated)",
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        return

    # 12. Direct Download Trigger (Zero-Prompting Workflow & Instant Dump Channel Cache)
    if data.startswith("dl:"):
        _, vid, quality = data.split(":")
        await execute_download(client, query, vid, quality)
        return


# =============================================================================
# Playlist Auto-Download (Batch — skips quality selection, uses preferred quality)
# =============================================================================
async def playlist_auto_download(client: Client, user, chat, url: str):
    """Auto-download a single video from a playlist batch using user's preferred settings."""
    user_id = user.id

    vid = extract_video_id(url)
    if not vid:
        return

    # Resolve metadata
    metadata = None
    qualities = None
    info = await extract_info_via_ytdlp(url)
    if info:
        try:
            metadata, qualities = parse_ytdlp_info(info)
        except Exception:
            pass
    if not metadata or not qualities:
        player_data = await fetch_player_data(vid)
        if player_data:
            metadata = parse_metadata(player_data, vid)
            formats_data = parse_available_formats(player_data.get("streamingData", {}), metadata["duration_sec"])
            qualities = formats_data["qualities"]

    if not metadata or not qualities:
        return

    _video_cache[vid] = {"metadata": metadata, "qualities": qualities, "url": url}
    title = metadata.get("title", "Video")

    # Determine best quality from user preferences
    prefs = await db.get_preferences(user_id)
    requested_codec = prefs.get("codec", "AVC")
    requested_container = prefs.get("container", "MP4")
    media_pref = prefs.get("media_preference", "video")

    # Pick best matching quality
    quality = "720p"  # default fallback
    pref_order = ["1080p", "720p", "480p", "360p", "240p"]
    for q in pref_order:
        if q in qualities:
            quality = q
            break

    # Build a fake CallbackQuery-like object for execute_download
    progress_msg = await client.send_message(
        chat_id=chat.id,
        text=f"⏳ <b>Playlist Auto-Download</b>\n"
        f"Starting: <code>{html.escape(title[:60])}</code>...",
        parse_mode=enums.ParseMode.HTML,
    )

    class _FakeQuery:
        def __init__(self, msg, usr):
            self.message = msg
            self.from_user = usr
            self.data = ""
        async def answer(self, *a, **kw):
            pass

    fake_q = _FakeQuery(progress_msg, user)
    try:
        await execute_download(client, fake_q, vid, quality)
    except Exception as e:
        logger.error(f"Playlist auto-download failed for {vid}: {e}")
        try:
            await progress_msg.edit_text(f"❌ <b>Failed:</b> <code>{html.escape(str(e)[:100])}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


# =============================================================================
# Playlist Sequential Batch Download with Smart Look-Ahead Pipeline
# =============================================================================
_PLAYLIST_LOOKAHEAD = 3  # how many upcoming videos to prefetch in background

def select_playlist_quality(requested: str, qualities: dict, is_audio: bool = False) -> str:
    """Select exact or nearest higher resolution; fallback to nearest lower if none above."""
    if is_audio or requested == "best" or requested in qualities:
        return requested
    if not requested.endswith("p") or not requested[:-1].isdigit():
        raise ValueError(f"Invalid quality: {requested}")
    available = sorted(int(q[:-1]) for q in qualities if q.endswith("p") and q[:-1].isdigit())
    higher = next((height for height in available if height >= int(requested[:-1])), None)
    if higher is not None:
        return f"{higher}p"
    # No format at or above requested — pick the highest available (nearest lower)
    if available:
        return f"{available[-1]}p"
    raise ValueError(f"No video format available at or above {requested}")


async def _get_verified_upload_destination(
    client: Client,
    query: CallbackQuery,
) -> int | str | None:
    """Resolve and verify a custom destination before any download is queued."""
    user_id = query.from_user.id
    current_chat_id = query.message.chat.id
    dest_chat = await get_target_upload_chat(
        user_id, current_chat_id, query.message.chat.type
    )

    # The current chat is already reachable because the callback originated there.
    # Custom PM destinations must be checked again in case permissions changed
    # after the preference was originally saved.
    if dest_chat == current_chat_id:
        return dest_chat

    valid, reason = await verify_destination_chat(client, dest_chat, user_id)
    if valid:
        return dest_chat

    bot_username = getattr(getattr(client, "me", None), "username", None)
    bot_name = f"@{bot_username}" if bot_username else "this bot"
    try:
        await query.answer(
            "Destination channel access required. Download was not started.",
            show_alert=True,
        )
    except Exception:
        pass
    try:
        await query.message.reply_text(
            "❌ <b>Download Not Started</b>\n\n"
            f"I cannot send files to destination <code>{html.escape(str(dest_chat))}</code>.\n"
            f"Add {html.escape(bot_name)} as an administrator and enable "
            "<b>Post Messages</b>, then set the destination again in /settings.\n\n"
            f"<b>Reason:</b> {html.escape(str(reason))}",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("Could not send destination validation message: %s", exc)
    return None


async def _execute_playlist_batch(
    client: Client,
    query: CallbackQuery,
    pl_id: str,
    selected_indices: list[int],
    quality: str,
):
    """Process selected items serially using the normal cache and transfer helpers."""
    pl_data = _playlist_cache.get(pl_id)
    if not pl_data:
        return

    user_id = query.from_user.id
    entries = pl_data["entries"]
    total = len(selected_indices)
    prefs = await db.get_preferences(user_id)
    requested_codec = prefs.get("codec", "AVC")
    requested_container = prefs.get("container", "MP4")
    media_pref = prefs.get("media_preference", "video")
    split_pref = prefs.get("split_mode", "streamable")
    is_audio = quality.lower() in {"audio", "mp3"} or (quality.lower().startswith("a") and quality.lower()[1:].isdigit())

    dest_chat = await _get_verified_upload_destination(client, query)
    if dest_chat is None:
        return

    if is_audio:
        mode = quality.lower()
        audio_pref = prefs.get("audio_codec", "M4A").upper()
        if mode == "audio":
            requested_codec = "M4A"
        elif mode.startswith("a") and mode[1:].isdigit():
            bitrate = int(mode[1:])
            if audio_pref == "OPUS":
                requested_codec = f"OPUS {bitrate}K"
            elif audio_pref == "MP3" or bitrate > 256:
                requested_codec = f"MP3 {bitrate}K"
            else:
                requested_codec = "M4A"
        else:
            requested_codec = "MP3 320K"
        requested_container = requested_codec

    # Create single task_id for entire playlist
    task_id = str(uuid.uuid4())[:8]
    dedupe_key = queue_manager.build_dedupe_key(
        user_id, pl_id, quality, requested_codec, requested_container,
        "audio" if is_audio else "video",
    )
    accepted, reason, queue_pos = await queue_manager.try_register_task(
        task_id, user_id, is_playlist=True, dedupe_key=dedupe_key
    )
    if not accepted:
        await query.answer(reason, show_alert=True)
        return

    # Send initial progress message
    try:
        progress_msg = await query.message.reply_text(
            f"📑 <b>Playlist Download Started</b> (Global Pos: #{queue_pos})\n"
            f"Total: <code>{total}</code> videos | Quality: <code>{quality}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        await queue_manager.complete_task(task_id)
        return

    # Create tracker with playlist count
    tracker = ProgressTracker(
        progress_msg,
        task_id,
        title=f"📑 Playlist ({total} videos)",
        task_by=(query.from_user.first_name or query.from_user.username or str(user_id)),
        user_id=user_id,
    )
    tracker.playlist_total = total
    if query.message.chat.type == enums.ChatType.PRIVATE:
        await progress_dashboard.register(tracker)
    _active_trackers[task_id] = tracker

    is_doc = (media_pref == "document")

    completed_count = 0
    failed_count = 0
    cached_sent_count = 0

    async def _metadata(entry_vid, entry_url):
        player_data = None
        try:
            player_data = await asyncio.wait_for(fetch_player_data(entry_vid), timeout=15)
        except (asyncio.TimeoutError, Exception):
            pass
        entry_metadata = None
        entry_qualities = None
        if player_data:
            try:
                entry_metadata = parse_metadata(player_data, entry_vid)
                formats_data = parse_available_formats(player_data.get("streamingData", {}), entry_metadata["duration_sec"])
                entry_qualities = formats_data["qualities"]
            except Exception:
                pass
        if not entry_metadata or not entry_qualities:
            try:
                entry_info = await asyncio.wait_for(
                    extract_info_via_ytdlp(entry_url), timeout=45
                )
                if entry_info:
                    entry_metadata, entry_qualities = parse_ytdlp_info(entry_info)
            except (asyncio.TimeoutError, Exception):
                pass

        if not entry_metadata or not entry_qualities:
            raise RuntimeError("Could not resolve video formats")


        return entry_metadata, entry_qualities

    async def _cache_hit(entry_vid, cache_quality):
        cached = await db.get_cached_media(
            video_id=entry_vid, quality=cache_quality, as_document=is_doc,
            split_mode=split_pref, codec=requested_codec, container=requested_container,
        )
        if not cached or not cached.get("message_ids") or not cached.get("dump_chat_id"):
            return False
        # Reject old requested-quality aliases with a different delivered quality.
        if cached.get("effective_quality", cache_quality) != cache_quality:
            return False
        await tracker.show_stage("Cache", f"Sending video {tracker.playlist_index}/{total}")
        for message_id in cached["message_ids"]:
            if queue_manager.is_cancelled(task_id):
                raise asyncio.CancelledError
            await flood_retry(client.copy_message, chat_id=dest_chat,
                              from_chat_id=cached["dump_chat_id"], message_id=message_id,
                              reply_to_message_id=query.message.id if dest_chat == query.message.chat.id else None)
        await db.record_download(user_id, entry_vid, cached.get("title", "Video"),
                                 cached.get("size_bytes", 0), quality=cache_quality,
                                 mode="audio" if is_audio else "video")
        return True

    async def _process_item(entry, seq_idx):
        entry_url = entry.get("url") or f"https://youtu.be/{entry.get('id', '')}"
        entry_vid = extract_video_id(entry_url)
        if not entry_vid:
            raise RuntimeError("Invalid video URL")
        if await _cache_hit(entry_vid, quality):
            return
        await tracker.show_stage("Queued", "Waiting for download slot")

        # ── PHASE 1: DOWNLOAD ── download slot held, released as soon as file hits disk
        async with queue_manager.semaphore:
            if queue_manager.is_cancelled(task_id):
                raise asyncio.CancelledError
            await tracker.show_stage("Formats", f"Resolving video {seq_idx}/{total}")
            entry_metadata, entry_qualities = await _metadata(entry_vid, entry_url)
            effective_q = select_playlist_quality(quality, entry_qualities, is_audio)
            if effective_q != quality and await _cache_hit(entry_vid, effective_q):
                return
            _video_cache[entry_vid] = {"metadata": entry_metadata, "qualities": entry_qualities, "url": entry_url}
            selected = quality_for_preferences(entry_qualities.get(effective_q, {}),
                                               preferred_codec=requested_codec,
                                               preferred_container=requested_container)
            tracker.expected_download_bytes = int(selected.get("total_size") or 0)
            work_dir = os.path.join(Config.DOWNLOAD_DIR, f"{task_id}_item_{seq_idx}")
            os.makedirs(work_dir, exist_ok=True)
            work_dirs.add(work_dir)
            selector, extra = build_quality_selector(effective_q, prefs.get("codec"),
                                                     prefs.get("container"), prefs.get("audio_codec", "M4A"))
            await queue_manager.set_task_phase(task_id, "downloading")
            if tracker._queued:
                tracker._queued = False
                if tracker.dashboard:
                    tracker.dashboard.request_refresh(user_id, force=True)
            await tracker.show_stage("Download", f"Video {seq_idx}/{total}: {effective_q}")
            success, msg = await download_media(
                url=entry_url, format_selector=selector,
                output_template=os.path.join(work_dir, "%(title)s.%(ext)s"),
                task_id=task_id, extra_args=extra, is_playlist=True,
                progress_hook=tracker.update_download_progress,
                progress_reset_hook=tracker.reset_download_attempt,
                stage_hook=tracker.begin_processing_stage,
                stage_end_hook=tracker.end_processing_stage,
            )
            if not success:
                raise RuntimeError(msg)
            if queue_manager.is_cancelled(task_id):
                raise asyncio.CancelledError
            files = [os.path.join(work_dir, f) for f in os.listdir(work_dir)
                     if not f.endswith((".part", ".ytdl")) and not re.search(r"\.f\d+\.[^.]+$", f)]
            if "MKV merge fallback" in msg:
                files = [f for f in files if f.lower().endswith(".mkv")] or files
            if not files:
                raise RuntimeError("Downloaded file not found")
            downloaded_file = max(files, key=os.path.getmtime)
            file_size = os.path.getsize(downloaded_file)
            media_profile = await get_media_profile(downloaded_file)
            entry_title = entry_metadata.get("title", "Video")
            downloaded_ext = os.path.splitext(downloaded_file)[1].lower()
            force_document = (
                not is_audio
                and downloaded_ext not in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
            )

            # Download phase complete — download slot released immediately.
            # File now lives on disk; upload will acquire its own slot below.

        # ── PHASE 2: UPLOAD ── upload slot held independently; download slot free
        async with queue_manager.upload_semaphore:
            await queue_manager.set_task_phase(task_id, "processing")

            max_limit = uploader.max_upload_size
            if file_size > max_limit:
                await tracker.show_stage("Splitting", f"Video {seq_idx}/{total}")
                if split_pref == "binary":
                    split_files = await split_binary_file(downloaded_file, max_limit, task_id)
                    as_doc = True
                else:
                    split_files = await split_streamable_video(downloaded_file, max_limit, task_id)
                    as_doc = (media_pref == "document") or force_document
            else:
                split_files = [downloaded_file]
                as_doc = (media_pref == "document") or force_document

            if not split_files:
                raise RuntimeError("Could not split video")

            if any(re.search(r"\.\d{3}$", path) for path in split_files):
                as_doc = True

            user_thumb = await db.get_custom_thumbnail(user_id)
            thumb_path = await prepare_thumbnail(
                work_dir=work_dir,
                video_id=entry_vid,
                metadata_url=entry_metadata.get("thumbnail"),
                custom_path=user_thumb,
                video_path=None if is_audio else downloaded_file,
                media_label="audio" if is_audio else "video",
            )

            meta = entry_metadata
            channel_name = meta.get("channel_name") or meta.get("channel") or "YouTube"
            views = meta.get("views", "0")
            dur_str = meta.get("duration_str")
            if not dur_str or dur_str == "N/A":
                dur_secs = meta.get("duration_sec") or meta.get("duration") or 0
                if isinstance(dur_secs, (int, float)) and dur_secs > 0:
                    mins, secs = divmod(int(dur_secs), 60)
                    hrs, mins = divmod(mins, 60)
                    dur_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"
                else:
                    dur_str = "N/A"

            if not hasattr(_execute_playlist_batch, "_bot_username"):
                try:
                    _me = await client.get_me()
                    _execute_playlist_batch._bot_username = _me.username or "NexTubeRobot"
                except Exception:
                    _execute_playlist_batch._bot_username = "NexTubeRobot"
            bot_user = _execute_playlist_batch._bot_username

            dump_message_ids = []
            reply_id = query.message.id if dest_chat == query.message.chat.id else None
            total_parts = len(split_files)

            await queue_manager.set_task_phase(task_id, "uploading")

            for idx, part_file in enumerate(split_files, 1):
                if queue_manager.is_cancelled(task_id):
                    return

                tracker.begin_upload(idx, total_parts)

                part_profile = media_profile
                if total_parts > 1 and not part_file.rsplit(".", 1)[-1].isdigit():
                    probed_part = await get_media_profile(part_file)
                    if probed_part.get("duration"):
                        part_profile = probed_part

                caption_text = build_delivery_caption(
                    title=entry_title,
                    channel=channel_name,
                    views=views,
                    requested_quality=effective_q,
                    file_path=part_file,
                    file_size=os.path.getsize(part_file),
                    profile=part_profile,
                    preferred_codec=requested_codec,
                    as_document=as_doc,
                    part_index=idx,
                    total_parts=total_parts,
                    fallback_duration=dur_str,
                    bot_username=bot_user,
                    video_url=f"https://www.youtube.com/watch?v={entry_vid}" if entry_vid else "",
                )

                uploaded_ok, dump_msg_id = await uploader.upload_media_file(
                    bot_client=client,
                    target_chat_id=dest_chat,
                    file_path=part_file,
                    caption=caption_text,
                    thumb_path=thumb_path,
                    as_document=as_doc,
                    reply_to_message_id=reply_id,
                    media_type="audio" if is_audio else "video",
                    media_title=entry_title,
                    performer=channel_name,
                    progress_callback=tracker.pyrogram_upload_progress,
                )
                if queue_manager.is_cancelled(task_id):
                    return
                if not uploaded_ok:
                    raise RuntimeError("Telegram upload failed")
                if dump_msg_id:
                    dump_message_ids.append(dump_msg_id)

            if len(dump_message_ids) == len(split_files) and Config.DUMP_CHANNEL:
                await db.save_cached_media(
                    video_id=entry_vid,
                    quality=effective_q,
                    as_document=is_doc,
                    split_mode=split_pref,
                    dump_chat_id=Config.DUMP_CHANNEL,
                    message_ids=dump_message_ids,
                    title=entry_title,
                    file_size_bytes=file_size,
                    codec=requested_codec,
                    container=requested_container,
                    effective_quality=effective_q,
                )

            await db.record_download(user_id, entry_vid, entry_title, file_size,
                                     quality=effective_q, mode="audio" if is_audio else "video")

    work_dirs = set()
    failed_urls: list[str] = []
    playlist_url = pl_data.get("url", "")
    try:
        for seq_idx, entry_idx in enumerate(selected_indices, 1):
            if queue_manager.is_cancelled(task_id):
                raise asyncio.CancelledError
            tracker.playlist_index = seq_idx
            tracker.title = f"📑 Playlist ({seq_idx}/{total})"
            tracker.reset_download_attempt()
            tracker.expected_download_bytes = 0
            tracker.last_download_speed = tracker.last_upload_speed = "0 B/s"
            await tracker.show_stage("Cache", f"Checking video {seq_idx}/{total}")
            try:
                await _process_item(entries[entry_idx], seq_idx)
                completed_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed_count += 1
                entry_url = entries[entry_idx].get("url") or f"https://youtu.be/{entries[entry_idx].get('id', '')}"
                if entry_url and entry_url.startswith("http"):
                    failed_urls.append(entry_url)
                logger.exception("Playlist %s item %s failed", task_id, seq_idx)
                await tracker.show_terminal(f"⚠️ Video {seq_idx}/{total} failed: {html.escape(str(exc)[:200])}")
            finally:
                await tracker.end_processing_stage()
                for path in list(work_dirs):
                    await asyncio.to_thread(shutil.rmtree, path, True)
                    work_dirs.discard(path)
            # Preserve controlled Telegram pacing; yt-dlp retains cookie sleeps.
            if seq_idx < total:
                await asyncio.sleep(0.4)

        # ── Build completion summary ──
        failed_pastyx_url = ""
        if failed_urls:
            failed_text = "\n".join(failed_urls)
            failed_pastyx_url = await upload_to_pastyx(
                content=failed_text,
                title=f"Failed Playlist URLs — {pl_data.get('title', 'Playlist')}",
                author="@NexTubeBot",
            ) or ""

        summary_lines = []
        if failed_count == 0:
            summary_lines.append(f"✅ <b>Playlist complete:</b> <code>{completed_count}/{total}</code> delivered; <code>{failed_count}</code> failed.")
        else:
            summary_lines.append(f"✅ <b>Playlist complete:</b> <code>{completed_count}/{total}</code> delivered; <code>{failed_count}</code> failed.")
            if failed_pastyx_url:
                summary_lines.append(f"❌ <b>Failed Urls :</b> <a href=\"{html.escape(failed_pastyx_url)}\"><b>FAILED_URLS_PASTYX_LINK</b></a>")
            else:
                failed_list = "\n".join(f"<code>{html.escape(u)}</code>" for u in failed_urls[:10])
                summary_lines.append(f"❌ <b>Failed Urls :</b>\n{failed_list}")
        if playlist_url:
            summary_lines.append(f"🔗 <b>Playlist Url:</b> <a href=\"{html.escape(playlist_url)}\"><b>Watch On Youtube</b></a>")

        completion_text = "\n".join(summary_lines)
        try:
            await tracker.message.reply_text(
                completion_text,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            pass
    except asyncio.CancelledError:
        pass
    finally:
        await tracker.close()
        _active_trackers.pop(task_id, None)
        await queue_manager.complete_task(task_id)
        if tracker.dashboard:
            await progress_dashboard.remove(tracker)
            progress_dashboard.refresh_all()


# =============================================================================
# Download Execution Pipeline (With Instant Dump Channel Cache)
# =============================================================================
async def execute_download(client: Client, query: CallbackQuery, vid: str, quality: str):
    user_id = query.from_user.id
    cache = _video_cache.get(vid)
    if not cache:
        await query.answer("⚠️ Session expired. Please re-send the YouTube link.", show_alert=True)
        return

    title = cache["metadata"].get("title", "Video")
    url = cache["url"]
    prefs = await db.get_preferences(user_id)

    media_pref = prefs.get("media_preference", "video")
    split_pref = prefs.get("split_mode", "streamable")
    dest_chat = await _get_verified_upload_destination(client, query)
    if dest_chat is None:
        return
    is_doc = (media_pref == "document")
    audio_mode = quality.lower()
    is_audio = audio_mode in {"audio", "mp3"} or audio_mode.startswith("a") and audio_mode[1:].isdigit()
    if is_audio:
        audio_codec_pref = prefs.get("audio_codec", "M4A").upper()
        if audio_mode == "audio":
            requested_codec = "M4A"
        elif audio_mode.startswith("a") and audio_mode[1:].isdigit():
            bitrate = int(audio_mode[1:])
            if audio_codec_pref == "OPUS":
                requested_codec = f"OPUS {bitrate}K"
            elif audio_codec_pref == "MP3" or bitrate > 256:
                requested_codec = f"MP3 {bitrate}K"
            else:
                requested_codec = "M4A"
        else:
            requested_codec = "MP3 320K"
    else:
        requested_codec = prefs.get("codec", "AVC")
    requested_container = requested_codec if is_audio else prefs.get("container", "MP4")

    # -------------------------------------------------------------------------
    # ⚡ 1. DUMP CHANNEL INSTANT CACHE CHECK (0.01s Fast Delivery)
    # -------------------------------------------------------------------------
    cached_entry = await db.get_cached_media(
        video_id=vid,
        quality=quality,
        as_document=is_doc,
        split_mode=split_pref,
        codec=requested_codec,
        container=requested_container,
    )
    if cached_entry and cached_entry.get("message_ids") and cached_entry.get("dump_chat_id"):
        await query.answer("⚡ Found in Dump Channel Cache! Sending instantly...", show_alert=False)
        reply_id = query.message.id if dest_chat == query.message.chat.id else None

        for msg_id in cached_entry["message_ids"]:
            try:
                await flood_retry(
                    client.copy_message,
                    chat_id=dest_chat,
                    from_chat_id=cached_entry["dump_chat_id"],
                    message_id=msg_id,
                    reply_to_message_id=reply_id,
                )
            except Exception as e:
                logger.error(f"Error copying cached message {msg_id}: {e}")

        # Record download metrics
        await db.record_download(user_id, vid, title, cached_entry.get("size_bytes", 0), quality=quality, mode="audio" if is_audio else "video")
        return

    # -------------------------------------------------------------------------
    # 2. Normal Download Workflow
    # -------------------------------------------------------------------------
    if speedtest_service.is_running:
        await query.answer("⚡ Speedtest running. Start download after it completes.", show_alert=True)
        return

    task_id = str(uuid.uuid4())[:8]
    dedupe_key = queue_manager.build_dedupe_key(
        user_id, vid, quality, requested_codec, requested_container,
        "audio" if is_audio else "video",
    )
    accepted, reason, queue_pos = await queue_manager.try_register_task(
        task_id, user_id, is_playlist=False, dedupe_key=dedupe_key
    )
    if not accepted:
        await query.answer(reason, show_alert=True)
        return

    try:
        progress_msg = await query.message.reply_text(
            f"⏳ <b>Task Queued</b> (Global Pos: #{queue_pos})\n"
            f"Starting download for <code>{html.escape(title[:50])}</code>...",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        await queue_manager.complete_task(task_id)
        await query.answer("Progress message was not created; task was safely rolled back.", show_alert=True)
        return
    await query.answer("🚀 Download started!")

    expected_download_bytes = 0
    if not is_audio:
        quality_data = cache.get("qualities", {}).get(quality)
        if quality_data:
            selected_quality = quality_for_preferences(
                quality_data,
                preferred_codec=prefs.get("codec"),
                preferred_container=prefs.get("container"),
            )
            expected_download_bytes = int(selected_quality.get("total_size") or 0)

    tracker = ProgressTracker(
        progress_msg,
        task_id,
        title,
        task_by=(query.from_user.first_name or query.from_user.username or str(user_id)),
        user_id=user_id,
        expected_download_bytes=expected_download_bytes,
    )
    if query.message.chat.type == enums.ChatType.PRIVATE:
        await progress_dashboard.register(tracker)
    _active_trackers[task_id] = tracker
    work_dir = os.path.join(Config.DOWNLOAD_DIR, task_id)
    os.makedirs(work_dir, exist_ok=True)

    completed_successfully = False
    try:
        # ── PHASE 1: DOWNLOAD ── download slot held, released as soon as file hits disk
        async with queue_manager.semaphore:
            if queue_manager.is_cancelled(task_id):
                return

            out_tmpl = os.path.join(work_dir, "%(title)s.%(ext)s")

            # Set phase to downloading
            await queue_manager.set_task_phase(task_id, "downloading")
            tracker._queued = False
            if tracker.dashboard:
                tracker.dashboard.request_refresh(user_id, force=True)

            # Determine exact format selector respecting user preferences
            selector, extra = build_quality_selector(
                quality_label=quality,
                preferred_codec=prefs.get("codec"),
                preferred_container=prefs.get("container"),
                preferred_audio_codec=prefs.get("audio_codec", "M4A"),
            )

            # Execute yt-dlp download
            success, msg = await download_media(
                url=url,
                format_selector=selector,
                output_template=out_tmpl,
                task_id=task_id,
                progress_hook=tracker.update_download_progress,
                progress_reset_hook=tracker.reset_download_attempt,
                stage_hook=tracker.begin_processing_stage,
                stage_end_hook=tracker.end_processing_stage,
                extra_args=extra,
            )

            if not success or queue_manager.is_cancelled(task_id):
                if not queue_manager.is_cancelled(task_id):
                    safe_error = html.escape(re.sub(r"\s+", " ", str(msg)[-1500:]).strip())
                    await tracker.show_terminal(
                        f"❌ <b>Download failed</b>\n<code>{safe_error}</code>",
                    )
                    retry_token = _create_retry_token(user_id, url, quality, requested_codec, requested_container, "audio" if is_audio else "video")
                    try:
                        await tracker.message.reply_text(
                            "🔄 <b>Want to retry?</b>",
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry Download", callback_data=f"retry:{retry_token}")]]),
                        )
                    except Exception:
                        pass
                return

            # Find downloaded file
            files = [
                os.path.join(work_dir, f)
                for f in os.listdir(work_dir)
                if not f.endswith((".part", ".ytdl"))
                and not re.search(r"\.f\d+\.[^.]+$", f)
            ]
            if "MKV merge fallback" in msg:
                mkv_files = [path for path in files if path.lower().endswith(".mkv")]
                if mkv_files:
                    files = mkv_files
            if not files:
                await tracker.show_terminal("❌ Could not find downloaded file.")
                retry_token = _create_retry_token(user_id, url, quality, requested_codec, requested_container, "audio" if is_audio else "video")
                try:
                    await tracker.message.reply_text(
                        "🔄 <b>Want to retry?</b>",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry Download", callback_data=f"retry:{retry_token}")]]),
                    )
                except Exception:
                    pass
                return
            # A failed first mux can leave a partial destination behind. The
            # newest complete output is the one produced by the successful
            # yt-dlp attempt.
            downloaded_file = max(files, key=os.path.getmtime)
            file_size = os.path.getsize(downloaded_file)
            media_profile = await get_media_profile(downloaded_file)
            downloaded_ext = os.path.splitext(downloaded_file)[1].lower()
            force_document = (
                not is_audio
                and downloaded_ext not in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
            )

            # Download phase complete — download slot released immediately.
            # File now lives on disk; upload will acquire its own slot below.

        # ── PHASE 2: UPLOAD ── upload slot held independently; download slot free
        async with queue_manager.upload_semaphore:
            # Set phase to processing (post-download, pre-upload)
            await queue_manager.set_task_phase(task_id, "processing")

            # Record metrics in MongoDB
            await db.record_download(user_id, vid, title, file_size, quality=quality, mode="audio" if is_audio else "video")

            # Splitting check
            max_limit = uploader.max_upload_size
            if file_size > max_limit:
                await tracker.show_stage("Splitting", "Preparing Telegram parts")
                if split_pref == "binary":
                    split_files = await split_binary_file(downloaded_file, max_limit, task_id)
                    as_doc = True
                else:
                    split_files = await split_streamable_video(downloaded_file, max_limit, task_id)
                    as_doc = (media_pref == "document") or force_document
            else:
                split_files = [downloaded_file]
                as_doc = (media_pref == "document") or force_document

            if not split_files:
                if queue_manager.is_cancelled(task_id):
                    return
                await tracker.show_terminal("❌ Could not split this file safely.")
                retry_token = _create_retry_token(user_id, url, quality, requested_codec, requested_container, "audio" if is_audio else "video")
                try:
                    await tracker.message.reply_text(
                        "🔄 <b>Want to retry?</b>",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry Download", callback_data=f"retry:{retry_token}")]]),
                    )
                except Exception:
                    pass
                return

            # A streamable split can intentionally fall back to exact binary
            # chunks when FFmpeg cannot preserve a valid playable container.
            # Binary chunks must always be delivered as Telegram documents.
            if any(re.search(r"\.\d{3}$", path) for path in split_files):
                as_doc = True

            # Always normalize thumbnails for Telegram (JPEG, <=320px and
            # <=200KB). The old raw-download path caused Telegram to silently
            # discard many YouTube thumbnails.
            user_thumb = await db.get_custom_thumbnail(user_id)
            thumb_path = await prepare_thumbnail(
                work_dir=work_dir,
                video_id=vid,
                metadata_url=cache.get("metadata", {}).get("thumbnail"),
                custom_path=user_thumb,
                video_path=None if is_audio else downloaded_file,
                media_label="audio" if is_audio else "video",
            )

            # Build Rich, High-End UI/UX Caption with Complete Video Quality & Specs
            meta = cache.get("metadata", {})
            channel_name = meta.get("channel_name") or meta.get("channel") or "YouTube"
            views = meta.get("views", "0")
            dur_str = meta.get("duration_str")
            if not dur_str or dur_str == "N/A":
                dur_secs = meta.get("duration_sec") or meta.get("duration") or 0
                if isinstance(dur_secs, (int, float)) and dur_secs > 0:
                    mins, secs = divmod(int(dur_secs), 60)
                    hrs, mins = divmod(mins, 60)
                    dur_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"
                else:
                    dur_str = "N/A"

            if not hasattr(execute_download, "_bot_username"):
                try:
                    _me = await client.get_me()
                    execute_download._bot_username = _me.username or "NexTubeRobot"
                except Exception:
                    execute_download._bot_username = "NexTubeRobot"
            bot_user = execute_download._bot_username

            dump_message_ids = []
            reply_id = query.message.id if dest_chat == query.message.chat.id else None
            total_parts = len(split_files)

            # Set phase to uploading
            await queue_manager.set_task_phase(task_id, "uploading")

            for idx, part_file in enumerate(split_files, 1):
                if queue_manager.is_cancelled(task_id):
                    return

                tracker.begin_upload(idx, total_parts)

                part_profile = media_profile
                if total_parts > 1 and not part_file.rsplit(".", 1)[-1].isdigit():
                    probed_part = await get_media_profile(part_file)
                    if probed_part.get("duration"):
                        part_profile = probed_part

                caption_text = build_delivery_caption(
                    title=title,
                    channel=channel_name,
                    views=views,
                    requested_quality=quality,
                    file_path=part_file,
                    file_size=os.path.getsize(part_file),
                    profile=part_profile,
                    preferred_codec=requested_codec,
                    as_document=as_doc,
                    part_index=idx,
                    total_parts=total_parts,
                    fallback_duration=dur_str,
                    bot_username=bot_user,
                    video_url=f"https://www.youtube.com/watch?v={vid}" if vid else "",
                )

                uploaded_ok, dump_msg_id = await uploader.upload_media_file(
                    bot_client=client,
                    target_chat_id=dest_chat,
                    file_path=part_file,
                    caption=caption_text,
                    thumb_path=thumb_path,
                    as_document=as_doc,
                    reply_to_message_id=reply_id,
                    media_type="audio" if is_audio else "video",
                    media_title=title,
                    performer=channel_name,
                    progress_callback=tracker.pyrogram_upload_progress,
                )
                if queue_manager.is_cancelled(task_id):
                    return
                if uploaded_ok and dump_msg_id:
                    dump_message_ids.append(dump_msg_id)

            # Save in Dump Channel Cache for instant delivery on future requests
            if dump_message_ids and Config.DUMP_CHANNEL:
                await db.save_cached_media(
                    video_id=vid,
                    quality=quality,
                    as_document=is_doc,
                    split_mode=split_pref,
                    dump_chat_id=Config.DUMP_CHANNEL,
                    message_ids=dump_message_ids,
                    title=title,
                    file_size_bytes=file_size,
                    codec=requested_codec,
                    container=requested_container,
                )

            completed_successfully = True

    except Exception as e:
        logger.error(f"Error executing task {task_id}: {e}", exc_info=True)
        try:
            safe_error = html.escape(re.sub(r"\s+", " ", str(e)[:1200]).strip())
            await tracker.show_terminal(
                f"❌ <b>Error</b>\n<code>{safe_error}</code>",
            )
            if not queue_manager.is_cancelled(task_id):
                retry_token = _create_retry_token(user_id, url, quality, requested_codec, requested_container, "audio" if is_audio else "video")
                await tracker.message.reply_text(
                    "🔄 <b>Want to retry?</b>",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry Download", callback_data=f"retry:{retry_token}")]]),
                )
        except Exception:
            pass
    finally:
        await tracker.close()
        _active_trackers.pop(task_id, None)
        await queue_manager.complete_task(task_id)
        if tracker.dashboard:
            await progress_dashboard.remove(tracker)
            progress_dashboard.refresh_all()
        elif completed_successfully:
            try:
                await progress_msg.delete()
            except Exception:
                pass
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


# =============================================================================
# Playlist Range Action Handlers (Descriptions / Formats / Screenshots)
# =============================================================================
async def _handle_playlist_descriptions(client: Client, message, pl_data: dict, selected_indices: list[int]):
    """Process descriptions for selected playlist videos with adaptive FloodWait throttling."""
    entries = pl_data["entries"]
    total = len(selected_indices)
    status_msg = await message.reply_text(
        f"📜 <b>Sending descriptions for {total} videos...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    sent = 0
    send_delay = 1.0  # adaptive delay between sends
    for seq_idx, entry_idx in enumerate(selected_indices, 1):
        entry = entries[entry_idx]
        entry_url = entry.get("url") or f"https://youtu.be/{entry.get('id', '')}"
        if not entry_url or not entry_url.startswith("http"):
            continue
        entry_vid = extract_video_id(entry_url)
        if not entry_vid:
            continue

        entry_title = f"Video {seq_idx}"
        desc = ""
        player_data = await fetch_player_data(entry_vid)
        if player_data:
            try:
                entry_md = parse_metadata(player_data, entry_vid)
                entry_title = entry_md.get("title", entry_title)
                desc = entry_md.get("description", "") or ""
            except Exception:
                pass
        if not desc:
            entry_info = await extract_info_via_ytdlp(entry_url)
            if entry_info:
                entry_title = entry_info.get("title", entry_title)
                desc = entry_info.get("description", "") or ""
        if not desc:
            desc = "No description available."

        parts = split_description(desc)
        total_parts = len(parts)
        for part_idx, part in enumerate(parts, start=1):
            heading = f"📜 Video {seq_idx}/{total}: {entry_title}"
            if total_parts > 1:
                heading += f" (Part {part_idx}/{total_parts})"
            try:
                await message.reply_text(
                    f"{heading}\n\n{part}",
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.DISABLED,
                )
                sent += 1
                # Adaptive: slow down after each successful send
                send_delay = min(send_delay + 0.2, 3.0)
            except FloodWait as e:
                wait_time = min(e.value + 2, 90)
                await asyncio.sleep(wait_time)
                send_delay = min(send_delay + 1.0, 5.0)
            except Exception:
                pass
        # Update progress
        try:
            await status_msg.edit_text(
                f"📜 <b>Progress:</b> {seq_idx}/{total} videos processed...",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        if seq_idx < total:
            await asyncio.sleep(send_delay)

    try:
        await status_msg.edit_text(f"✅ <b>Descriptions sent:</b> {sent}/{total} videos")
    except Exception:
        pass


async def _handle_playlist_formats(client: Client, message, pl_data: dict, selected_indices: list[int]):
    """Process format lists for selected playlist videos with adaptive FloodWait throttling."""
    entries = pl_data["entries"]
    total = len(selected_indices)
    status_msg = await message.reply_text(
        f"📑 <b>Fetching formats for {total} videos...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    sent = 0
    send_delay = 1.0
    for seq_idx, entry_idx in enumerate(selected_indices, 1):
        entry = entries[entry_idx]
        entry_url = entry.get("url") or f"https://youtu.be/{entry.get('id', '')}"
        if not entry_url or not entry_url.startswith("http"):
            continue
        entry_vid = extract_video_id(entry_url)
        if not entry_vid:
            continue

        entry_metadata = None
        entry_qualities = None
        player_data = await fetch_player_data(entry_vid)
        if player_data:
            try:
                entry_metadata = parse_metadata(player_data, entry_vid)
                formats_data = parse_available_formats(player_data.get("streamingData", {}), entry_metadata["duration_sec"])
                entry_qualities = formats_data["qualities"]
            except Exception:
                pass
        if not entry_metadata or not entry_qualities:
            entry_info = await extract_info_via_ytdlp(entry_url)
            if entry_info:
                try:
                    entry_metadata, entry_qualities = parse_ytdlp_info(entry_info)
                except Exception:
                    pass
        if not entry_metadata or not entry_qualities:
            continue

        entry_title = entry_metadata.get("title", f"Video {seq_idx}")
        dur_str = entry_metadata.get("duration_str", "N/A")

        lines = [f"📹 <b>Video {seq_idx}/{total}: {html.escape(entry_title[:60])}</b>"]
        lines.append(f"⏱️ {dur_str}\n")

        lines.append("📸 Video Formats:")
        sorted_q = sorted(
            [(k, v) for k, v in entry_qualities.items() if k.replace("p", "").isdigit()],
            key=lambda item: int(item[0].replace("p", ""))
        )
        for q_name, q_data in sorted_q:
            display_data = quality_for_preferences(q_data)
            sz_str = format_bytes_human(display_data.get("total_size", 0))
            w = display_data.get("width", 0)
            h = display_data.get("height", 0)
            dim_str = f" ({w}x{h})" if w and h else ""
            lines.append(f"📹 {q_name}: {sz_str}{dim_str}")

        audio_formats = entry_metadata.get("audio_formats", [])
        if audio_formats:
            lines.append("")
            lines.append("🎵 Audio Formats:")
            for af in audio_formats:
                abr = af.get("bitrate", 0)
                sz = af.get("size", 0)
                sz_str = format_bytes_human(sz) if sz else "N/A"
                ext = af.get("ext", "")
                codec_label = "AAC" if af.get("is_m4a") else ext.upper()
                lines.append(f"🎧 {abr}kbps ({codec_label}): {sz_str}")

        try:
            await message.reply_text(
                "\n".join(lines),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
            send_delay = min(send_delay + 0.2, 3.0)
        except FloodWait as e:
            wait_time = min(e.value + 2, 90)
            await asyncio.sleep(wait_time)
            send_delay = min(send_delay + 1.0, 5.0)
        except Exception:
            pass
        try:
            await status_msg.edit_text(
                f"📑 <b>Progress:</b> {seq_idx}/{total} videos processed...",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        if seq_idx < total:
            await asyncio.sleep(send_delay)

    try:
        await status_msg.edit_text(f"✅ <b>Format lists sent:</b> {sent}/{total} videos")
    except Exception:
        pass





# =============================================================================
# Startup Main
# =============================================================================
async def init_services():
    logger.info("Initializing Database & Upload Engine...")
    await db.init()
    await uploader.init()
    # Load cookies from MongoDB to disk (cross-device persistence)
    try:
        await db.load_cookies_to_disk()
    except Exception as e:
        logger.warning(f"Could not load cookies from MongoDB: {e}")
    cookie_manager.refresh_pool()
    # Load resolver cookie setting from MongoDB
    try:
        resolver_filename = await db.get_resolver_cookie()
        if resolver_filename:
            cookie_manager.set_resolver_cookie(resolver_filename)
            logger.info(f"📌 Resolver cookie loaded: {resolver_filename}")
    except Exception as e:
        logger.warning(f"Could not load resolver cookie from MongoDB: {e}")
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

    # Cleanup orphaned download directories from previous crashed sessions
    try:
        _orphan_cleaned = 0
        for _entry in os.listdir(Config.DOWNLOAD_DIR):
            _path = os.path.join(Config.DOWNLOAD_DIR, _entry)
            if os.path.isdir(_path) and _entry != "thumbnails":
                shutil.rmtree(_path, ignore_errors=True)
                _orphan_cleaned += 1
        if _orphan_cleaned:
            logger.info(f"🧹 Cleaned {_orphan_cleaned} orphaned download directories from previous session")
    except Exception as _e:
        logger.warning(f"Orphan cleanup error: {_e}")

    # --- Health-check HTTP server (keeps hosting platform from sending SIGTERM) ---
    async def _start_health_server():
        port_value = os.getenv("PORT", "").strip()
        if not port_value:
            logger.info("PORT is not set; health-check server is disabled")
            return
        try:
            import aiohttp.web as _web
        except ImportError:
            logger.info("aiohttp not installed; health-check server skipped (not needed on VPS/laptop)")
            return
        async def _health_handler(request):
            return _web.Response(text="OK", status=200)
        app_health = _web.Application()
        app_health.router.add_get("/", _health_handler)
        app_health.router.add_get("/health", _health_handler)
        try:
            port = int(port_value)
        except ValueError:
            logger.warning(f"Invalid PORT={port_value!r}; health-check server is disabled")
            return
        runner = _web.AppRunner(app_health)
        await runner.setup()
        site = _web.TCPSite(runner, "0.0.0.0", port)
        try:
            await site.start()
            logger.info(f"🩺 Health-check server listening on port {port}")
        except Exception as e:
            logger.warning(f"Health-check server failed to start on port {port}: {e}")

    asyncio.create_task(_start_health_server())

    # Start retry token cleanup background task
    asyncio.create_task(_cleanup_retry_tokens())

    # Register bot commands in Telegram menu (background task — waits for client)
    async def _register_bot_commands():
        try:
            await asyncio.sleep(5)  # Wait for client to connect
            from pyrogram.raw import functions, types
            commands = [
                types.BotCommand(command="start", description="Start the bot & welcome message"),
                types.BotCommand(command="settings", description="User preferences & delivery settings"),
                types.BotCommand(command="cookies", description="Multi-cookie pool management"),
                types.BotCommand(command="leaderboard", description="Top downloaders leaderboard"),
                types.BotCommand(command="history", description="Your download history"),
                types.BotCommand(command="queue", description="Check your active tasks in queue"),
                types.BotCommand(command="setthumb", description="Set custom thumbnail (reply to image)"),
                types.BotCommand(command="viewthumb", description="View your current thumbnail"),
                types.BotCommand(command="delthumb", description="Delete your custom thumbnail"),
                types.BotCommand(command="stats", description="Global bot statistics (Admin only)"),
                types.BotCommand(command="status", description="Server and database health (Admin only)"),
                types.BotCommand(command="globalqueue", description="Global queue view (Admin only)"),
                types.BotCommand(command="speedtest", description="Network speedtest (Admin only)"),
                types.BotCommand(command="broadcast", description="Broadcast message to all users (Admin only)"),
                types.BotCommand(command="ban", description="Ban a user (Admin only)"),
                types.BotCommand(command="unban", description="Unban a user (Admin only)"),
                types.BotCommand(command="banlist", description="List all banned users (Admin only)"),
                types.BotCommand(command="unbanall", description="Unban all users (Owner only)"),
                types.BotCommand(command="clearcache", description="Clear dump channel cache (Admin only)"),
                types.BotCommand(command="clearqueue", description="Clear your queue (All users)"),
                types.BotCommand(command="clearglobalqueue", description="Clear ALL queues (Owner only)"),
                types.BotCommand(command="db", description="Multi-database management (Owner only)"),
                types.BotCommand(command="logs", description="View live bot logs (Admin only)"),
                types.BotCommand(command="restart", description="Stop all tasks and restart (Owner/Admin)"),
                types.BotCommand(command="update", description="Git pull and restart (Owner/Admin)"),
            ]
            await app.invoke(functions.bots.SetBotCommands(
                scope=types.BotCommandScopeDefault(),
                lang_code="en",
                commands=commands,
            ))
            logger.info("Bot commands registered in Telegram menu!")
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")
    asyncio.create_task(_register_bot_commands())

    # Background task: Monitor primary DB size, remind owner at 400MB
    async def _monitor_db_size():
        while True:
            await asyncio.sleep(1800)
            try:
                info = await db.check_db_size()
                if info.get("should_warn"):
                    db.mark_db_warning_sent()
                    await app.send_message(
                        chat_id=Config.OWNER_ID,
                        text=(
                            f"⚠️ <b>Primary Database Size Alert</b>\n\n"
                            f"• <b>Data Size:</b> <code>{info['data_size_mb']} MB</code>\n"
                            f"• <b>Storage:</b> <code>{info['storage_mb']} MB</code>\n"
                            f"• <b>Collections:</b> <code>{info['collections']}</code>\n\n"
                            f"Primary DB is over 400MB. Consider adding a secondary database for cache failover.\n"
                            f"Use /db to manage databases."
                        ),
                        parse_mode=enums.ParseMode.HTML,
                    )
                    logger.warning(f"⚠️ Primary DB size warning sent to owner ({info['data_size_mb']} MB)")
            except Exception as e:
                logger.warning(f"DB size monitor error: {e}")
    asyncio.create_task(_monitor_db_size())

    # Background task: Monitor yt-dlp releases, notify owner when update available
    async def _monitor_ytdlp_version():
        import aiohttp
        import importlib.metadata
        _notif_key = "ytdlp_update_notified"
        while True:
            await asyncio.sleep(3600)  # check every hour
            try:
                current = importlib.metadata.version("yt-dlp")
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://pypi.org/pypi/yt-dlp/json", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                latest = data.get("info", {}).get("version", "")
                if not latest or latest == current:
                    continue
                # Avoid duplicate notifications for same version
                if db._db is not None:
                    meta_coll = db._db["bot_meta"]
                    doc = await meta_coll.find_one({"_key": _notif_key})
                    if doc and doc.get("version") == latest:
                        continue
                    await meta_coll.update_one(
                        {"_key": _notif_key}, {"$set": {"version": latest}}, upsert=True,
                    )
                await app.send_message(
                    chat_id=Config.OWNER_ID,
                    text=(
                        f"🔄 <b>yt-dlp Update Available</b>\n\n"
                        f"• <b>Current:</b> <code>{current}</code>\n"
                        f"• <b>Latest:</b> <code>{latest}</code>\n\n"
                        f"Updates often include YouTube fixes:\n"
                        f"403 errors, signature changes, format extraction,\n"
                        f"EJS challenge components, and player updates.\n\n"
                        f"<b>Includes:</b> yt-dlp + EJS/JS runtime support\n\n"
                        f"⚠️ <b>HF Spaces:</b> Click <b>Rebuild</b> to update.\n"
                        f"Restart will NOT work — changes don't persist\n"
                        f"in a running container.\n\n"
                        f"🔄 <b>VPS:</b> Run <code>pip install -U yt-dlp</code>\n"
                        f"then restart the bot service."
                    ),
                    parse_mode=enums.ParseMode.HTML,
                )
                logger.info(f"🔄 yt-dlp update notification sent: {current} → {latest}")
            except Exception as e:
                logger.debug(f"yt-dlp version check skipped: {e}")
    asyncio.create_task(_monitor_ytdlp_version())

    # Background task: Monitor wzgram (Pyrogram fork) releases, notify owner when update available
    async def _monitor_wzgram_version():
        import aiohttp
        import importlib.metadata
        _notif_key = "wzgram_update_notified"
        while True:
            await asyncio.sleep(3600)  # check every hour
            try:
                current = importlib.metadata.version("wzgram")
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://pypi.org/pypi/wzgram/json", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                latest = data.get("info", {}).get("version", "")
                if not latest or latest == current:
                    continue
                if db._db is not None:
                    meta_coll = db._db["bot_meta"]
                    doc = await meta_coll.find_one({"_key": _notif_key})
                    if doc and doc.get("version") == latest:
                        continue
                    await meta_coll.update_one(
                        {"_key": _notif_key}, {"$set": {"version": latest}}, upsert=True,
                    )
                await app.send_message(
                    chat_id=Config.OWNER_ID,
                    text=(
                        f"⚡ <b>wzgram Update Available</b>\n\n"
                        f"• <b>Current:</b> <code>{current}</code>\n"
                        f"• <b>Latest:</b> <code>{latest}</code>\n\n"
                        f"wzgram is the Pyrogram fork used for\n"
                        f"Telegram uploads, sessions & Bot API.\n\n"
                        f"⚠️ <b>Requirements.txt pin must be updated:</b>\n"
                        f"<code>wzgram=={latest}</code>\n\n"
                        f"⚠️ <b>HF Spaces:</b> Update pin in requirements.txt\n"
                        f"then click <b>Rebuild</b>.\n"
                        f"Restart will NOT work.\n\n"
                        f"🔄 <b>VPS:</b> Run <code>pip install -U wzgram</code>\n"
                        f"then restart the bot service.\n\n"
                        f"📎 <a href=\"https://github.com/rjriajul/wzgram/releases/tag/v{latest}\">Release Notes</a>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info(f"⚡ wzgram update notification sent: {current} → {latest}")
            except Exception as e:
                logger.debug(f"wzgram version check skipped: {e}")
    asyncio.create_task(_monitor_wzgram_version())

    logger.info("🚀 Advanced YouTube Downloader Bot is running and ready for messages!")


async def run_bot():
    await init_services()
    await app.start()
    notice_chat = os.environ.pop("BOT_RESTART_CHAT", "")
    notice_text = os.environ.pop("BOT_RESTART_NOTICE", "✅ Bot restarted. All previous tasks stopped.")
    if notice_chat:
        try:
            await app.send_message(int(notice_chat), notice_text)
        except Exception:
            logger.warning("Could not deliver restart confirmation")

    # Retain normal signal-driven shutdown as well as command-driven restart.
    idle_task = asyncio.create_task(idle())
    restart_wait = asyncio.create_task(_restart_event.wait())
    await asyncio.wait([idle_task, restart_wait], return_when=asyncio.FIRST_COMPLETED)
    restart = _restart_request
    for task in (idle_task, restart_wait):
        if not task.done():
            task.cancel()
    await queue_manager.cancel_all_tasks()
    try:
        await asyncio.wait_for(app.stop(), 15)
    except Exception:
        logger.warning("Client shutdown timed out; closing remaining work")
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5)
    await asyncio.to_thread(stop_children)
    if restart:
        chat_id, update = restart
        repo = os.path.dirname(os.path.abspath(__file__))
        notice = "✅ Bot restarted. All previous tasks stopped."
        if update:
            try:
                await git_command(repo, "pull", "--ff-only")
                notice = "✅ Git pull successful. Bot restarted with updated code."
            except Exception:
                notice = "⚠️ Git pull failed. Bot restarted with existing code; local edits were not discarded."
        os.environ["BOT_RESTART_CHAT"] = str(chat_id)
        os.environ["BOT_RESTART_NOTICE"] = notice
        replace_bot(repo)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())
