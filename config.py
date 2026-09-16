# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Hosted platforms inject environment variables directly.
    pass


def _parse_comma_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,\s]+", str(value)) if item.strip()]


def _parse_int_list(value) -> list[int]:
    if isinstance(value, list):
        return [int(item) for item in value if str(item).strip().lstrip("-").isdigit()]
    items = _parse_comma_list(value)
    result = []
    for item in items:
        try:
            result.append(int(item))
        except ValueError:
            pass
    return result


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class Config:
    # =========================================================================
    # 📝 FILL YOUR CONFIGURATION VARIABLES DIRECTLY BELOW:
    # =========================================================================

    # 1. MongoDB Database Link (DB_URI / DB-URI)
    DB_URI = os.getenv("DB_URI", "")

    # 2. Telegram Bot API Credentials (from https://my.telegram.org & @BotFather)
    API_ID = int(os.getenv("API_ID", ""))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")

    # 3. Bot Owner & Admins
    OWNER_ID = int(os.getenv("OWNER_ID", "8259548957"))
    ADMIN_ID = _parse_int_list(os.getenv("ADMIN_ID", "8259548957,7383334104"))
    if OWNER_ID and OWNER_ID not in ADMIN_ID:
        ADMIN_ID.append(OWNER_ID)

    # 4. Force Subscribe Channels / Groups (comma-separated, all must be joined)
    # Example: FORCESUB = "@NubDevs,-1003705473321,@NexonBots,-1003183447271"
    FORCESUB = _parse_comma_list(os.getenv("FORCESUB", "-1003705473321,-1003183447271"))

    # 5. Dump Channel for Instant Cache & Storage
    # All downloaded videos will be forwarded here and served within milliseconds on duplicate requests
    DUMP_CHANNEL = int(os.getenv("DUMP_CHANNEL", "-1004407713190"))

    # 6. 4GB Premium UserBot Upload Session (STRICTLY OPTIONAL)
    STRING_SESSION = os.getenv("STRING_SESSION", "").strip()
    LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "0"))  # Required if using STRING_SESSION

    # 7. Telegram Premium App Credentials (for custom premium emojis)
    PREMIUM_API_ID = int(os.getenv("PREMIUM_API_ID", "") or os.getenv("API_ID", "0"))
    PREMIUM_API_HASH = os.getenv("PREMIUM_API_HASH", "") or os.getenv("API_HASH", "")

    # 8. Optional yt-dlp proxy (HTTP / HTTPS / SOCKS4 / SOCKS5).
    # Separate username/password fields support NordVPN service credentials.
    # "anonymous" protects cookie sessions by proxying only cookie-free attempts.
    YTDLP_PROXY_URL = os.getenv("YTDLP_PROXY_URL", os.getenv("PROXY_URL", "")).strip()
    YTDLP_PROXY_USERNAME = os.getenv(
        "YTDLP_PROXY_USERNAME", os.getenv("PROXY_USERNAME", "")
    ).strip()
    YTDLP_PROXY_PASSWORD = os.getenv(
        "YTDLP_PROXY_PASSWORD", os.getenv("PROXY_PASSWORD", "")
    ).strip()
    YTDLP_PROXY_MODE = os.getenv("YTDLP_PROXY_MODE", "all").strip().lower()

    # =========================================================================
    # ⚙️ STORAGE & BOT DEFAULTS:
    # =========================================================================
    # Local runs use a writable project folder. Container manifests explicitly
    # override this with /data so a persistent volume can be mounted there.
    DATA_DIR = os.path.abspath(os.getenv("DATA_DIR", "data"))
    COOKIES_DIR = os.path.abspath(
        os.getenv("COOKIES_DIR", os.path.join(DATA_DIR, "cookies"))
    )
    DOWNLOAD_DIR = os.path.abspath(
        os.getenv("DOWNLOAD_DIR", os.path.join(DATA_DIR, "downloads"))
    )

    MAX_CONCURRENT_DOWNLOADS = 2
    MAX_CONCURRENT_UPLOADS = 1
    MAX_PLAYLIST_QUEUE_PER_USER = 1
    MAX_SINGLE_QUEUE_PER_USER = 2
    # Parallel DASH/HLS fragments for one yt-dlp task. Two is the safest
    # setting to avoid YouTube cookie degradation from aggressive connections.
    # Operators can tune it between 1 and 4 if cookies are not a concern.
    YTDLP_CONCURRENT_FRAGMENTS = _bounded_int_env(
        "YTDLP_CONCURRENT_FRAGMENTS", 2, 1, 4
    )

    # Default Preferences (User can toggle in /settings)
    DEFAULT_CODEC = "AVC"                 # AVC / AV1 / VP9
    DEFAULT_CONTAINER = "MP4"             # MP4 / MKV
    DEFAULT_SPLIT_MODE = "streamable"     # streamable / binary
    DEFAULT_MEDIA_PREFERENCE = "video"    # video / document

    # File Size Caps (in Bytes)
    STD_MAX_FILE_SIZE = 2_097_152_000       # Telegram standard: 2000 MiB
    PREMIUM_MAX_FILE_SIZE = 4_194_304_000   # Telegram Premium: 4000 MiB
    STREAM_SPLIT_MARGIN = 3_000_000          # FFmpeg container/keyframe headroom


class Emojis:
    """Exact Telegram Custom Premium Emoji IDs (from emoji.txt)."""
    CROWN = 5217822164362739968       # 👑 Leaderboard & Owner
    UPLOAD = 5463122435425448565      # ⬆️ Uploading Stream
    DOWNLOAD = 5406745015365943482    # ⬇️ Downloading Stream
    CHECK = 5427009714745517609       # ✅ Verified / Active / Valid
    CHECK_2 = 5206607081334906820     # ✔️ Done / Success
    CROSS = 5210952531676504517       # ❌ Cancel / Expired / Error
    TOP = 5463071033256848094         # 🔝 Top Downloaders
    LOVE = 5337080053119336309        # 👍 Like / Reaction

    @staticmethod
    def tag(emoji_id: int, fallback: str) -> str:
        """Returns Telegram HTML custom emoji tag with a text fallback."""
        if emoji_id:
            return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
        return fallback
