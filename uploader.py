# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import re
import logging
import asyncio
from pyrogram import Client, enums
from pyrogram.errors import FloodWait
from config import Config
from media_processor import get_media_dimensions, get_media_duration
from rate_limiter import rate_limiter

logger = logging.getLogger("Uploader")

_BINARY_CHUNK_RE = re.compile(r"\.\d{3}$")


async def flood_retry(func, *args, retries=5, **kwargs):
    """Executes a Pyrogram method with automatic FloodWait backoff retries."""
    for attempt in range(retries):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            wait_time = min(e.value, 60)
            logger.warning(f"FloodWait encountered: sleeping for {wait_time}s...")
            await asyncio.sleep(wait_time)
        except Exception as e:
            if attempt == retries - 1:
                raise e
    raise FloodWait(retries * 5)


class UploadEngine:
    def __init__(self):
        self.userbot: Client | None = None
        self.is_premium_userbot = False
        self.max_upload_size = Config.STD_MAX_FILE_SIZE

    async def init(self):
        """Validates optional STRING_SESSION and verifies Telegram Premium status."""
        if not Config.STRING_SESSION:
            logger.info("ℹ️ No STRING_SESSION provided: standard 2.0 GB upload limit active.")
            self.max_upload_size = Config.STD_MAX_FILE_SIZE
            self.is_premium_userbot = False
            return

        try:
            self.userbot = Client(
                "userbot_session",
                session_string=Config.STRING_SESSION,
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                no_updates=True,
            )
            await self.userbot.start()
            me = await self.userbot.get_me()
            if getattr(me, "is_premium", False):
                self.is_premium_userbot = True
                self.max_upload_size = Config.PREMIUM_MAX_FILE_SIZE
                logger.info(f"✅ Verified Telegram Premium UserBot: @{me.username or me.id} (4.0 GB uploads active)")
            else:
                self.is_premium_userbot = False
                self.max_upload_size = Config.STD_MAX_FILE_SIZE
                logger.warning(f"⚠️ UserBot @{me.username or me.id} is NOT Premium. Falling back to 2.0 GB limit.")
            await self.userbot.stop()
        except Exception as e:
            logger.error(f"❌ Failed to validate STRING_SESSION: {e}. Falling back to 2.0 GB limit.")
            self.userbot = None
            self.is_premium_userbot = False
            self.max_upload_size = Config.STD_MAX_FILE_SIZE

    async def upload_media_file(
        self,
        bot_client: Client,
        target_chat_id: int,
        file_path: str,
        caption: str,
        thumb_path: str | None,
        as_document: bool,
        reply_to_message_id: int | None,
        media_type: str = "video",
        media_title: str | None = None,
        performer: str | None = None,
        progress_callback=None,
    ) -> tuple[bool, int | None]:
        """
        Uploads file to Telegram and copies it to DUMP_CHANNEL.
        Returns: (success: bool, dump_channel_message_id: int | None)
        """
        file_size = os.path.getsize(file_path)
        is_binary_chunk = bool(_BINARY_CHUNK_RE.search(file_path))

        # Binary chunks are ALWAYS sent as document
        send_as_doc = as_document or is_binary_chunk
        send_as_audio = media_type == "audio" and not send_as_doc

        width, height = 1280, 720
        duration = 0
        if not send_as_doc:
            duration = await get_media_duration(file_path)
            if not send_as_audio:
                width, height = await get_media_dimensions(file_path)

        sent_msg = None
        dump_msg_id = None

        # 4GB UserBot Upload Path (Files >= 2.0 GB)
        if file_size >= Config.STD_MAX_FILE_SIZE and self.is_premium_userbot and Config.LOG_CHANNEL and self.userbot:
            logger.info(f"Uploading large file ({file_size / (1024*1024):.1f} MB) via Premium UserBot...")
            await self.userbot.start()
            try:
                if send_as_audio:
                    sent = await flood_retry(
                        self.userbot.send_audio,
                        Config.LOG_CHANNEL,
                        audio=file_path,
                        caption=caption,
                        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                        duration=duration,
                        performer=(performer or "YouTube")[:64],
                        title=(media_title or os.path.basename(file_path))[:64],
                        parse_mode=enums.ParseMode.HTML,
                        progress=progress_callback,
                    )
                elif not send_as_doc:
                    _thumb = thumb_path if thumb_path and os.path.exists(thumb_path) else None
                    sent = await flood_retry(
                        self.userbot.send_video,
                        Config.LOG_CHANNEL,
                        video=file_path,
                        caption=caption,
                        thumb=_thumb,
                        video_cover=_thumb,
                        width=width,
                        height=height,
                        duration=duration,
                        supports_streaming=True,
                        parse_mode=enums.ParseMode.HTML,
                        progress=progress_callback,
                    )
                else:
                    sent = await flood_retry(
                        self.userbot.send_document,
                        Config.LOG_CHANNEL,
                        document=file_path,
                        caption=caption,
                        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                        parse_mode=enums.ParseMode.HTML,
                        progress=progress_callback,
                    )

                if sent is None:
                    return False, None

                rate_limiter.record_send()

                # Copy message to target destination
                sent_msg = await flood_retry(
                    bot_client.copy_message,
                    chat_id=target_chat_id,
                    from_chat_id=sent.chat.id,
                    message_id=sent.id,
                    reply_to_message_id=reply_to_message_id,
                )
                rate_limiter.record_send()

                # Also forward/copy to DUMP_CHANNEL if configured
                if Config.DUMP_CHANNEL:
                    try:
                        dump_copy = await flood_retry(
                            bot_client.copy_message,
                            chat_id=Config.DUMP_CHANNEL,
                            from_chat_id=sent.chat.id,
                            message_id=sent.id,
                        )
                        dump_msg_id = dump_copy.id
                        rate_limiter.record_send()
                    except Exception as e:
                        logger.warning(f"Could not forward large file to DUMP_CHANNEL: {e}")

                # Cleanup log message
                try:
                    await self.userbot.delete_messages(sent.chat.id, sent.id)
                except Exception:
                    pass

                return True, dump_msg_id
            finally:
                await self.userbot.stop()

        # Standard 2GB Upload Path
        else:
            if send_as_audio:
                sent_msg = await flood_retry(
                    bot_client.send_audio,
                    target_chat_id,
                    audio=file_path,
                    caption=caption,
                    thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                    duration=duration,
                    performer=(performer or "YouTube")[:64],
                    title=(media_title or os.path.basename(file_path))[:64],
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                )
            elif not send_as_doc:
                _thumb = thumb_path if thumb_path and os.path.exists(thumb_path) else None
                sent_msg = await flood_retry(
                    bot_client.send_video,
                    target_chat_id,
                    video=file_path,
                    caption=caption,
                    thumb=_thumb,
                    video_cover=_thumb,
                    width=width,
                    height=height,
                    duration=duration,
                    supports_streaming=True,
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                )
            else:
                sent_msg = await flood_retry(
                    bot_client.send_document,
                    target_chat_id,
                    document=file_path,
                    caption=caption,
                    thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                )

            if sent_msg is None:
                return False, None

            rate_limiter.record_send()

            # Copy to DUMP_CHANNEL for instant cache retrieval
            if Config.DUMP_CHANNEL and sent_msg:
                try:
                    dump_copy = await flood_retry(
                        bot_client.copy_message,
                        chat_id=Config.DUMP_CHANNEL,
                        from_chat_id=target_chat_id,
                        message_id=sent_msg.id,
                    )
                    dump_msg_id = dump_copy.id
                    rate_limiter.record_send()
                except Exception as e:
                    logger.warning(f"Could not copy to DUMP_CHANNEL: {e}")

            return True, dump_msg_id


# Global upload engine
uploader = UploadEngine()
