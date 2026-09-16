# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import logging
from pyrogram import Client, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from config import Config, Emojis
from database import db

logger = logging.getLogger("ForceSub")


async def is_user_member(client: Client, chat_id: str | int, user_id: int) -> bool:
    try:
        resolved = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        member = await client.get_chat_member(resolved, user_id)
        if member.status in [
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        ]:
            return True
        return False
    except Exception as e:
        logger.debug(f"Membership check failed for {chat_id}: {e}")
        return False


async def check_forcesub(client: Client, user_id: int) -> tuple[bool, list[dict]]:
    """
    Checks if the user has joined all configured FORCESUB channels/groups.
    Returns: (is_fully_joined: bool, missing_chats: list[{'title', 'invite_link'}])
    """
    if not Config.FORCESUB or user_id in Config.ADMIN_ID:
        return True, []

    missing = []
    for chat_target in Config.FORCESUB:
        # Convert numeric string IDs to int for Pyrogram
        resolved = int(chat_target) if str(chat_target).lstrip("-").isdigit() else chat_target
        joined = await is_user_member(client, resolved, user_id)
        if not joined:
            try:
                chat = await client.get_chat(resolved)
                title = chat.title or str(chat_target)
                link = chat.invite_link
                if not link and chat.username:
                    link = f"https://t.me/{chat.username}"
                if not link:
                    link = (await client.create_chat_invite_link(chat.id)).invite_link
                missing.append({"title": title, "link": link})
            except Exception as e:
                logger.error(f"Failed to fetch invite link for {chat_target}: {e}")
                try:
                    invite = await client.create_chat_invite_link(resolved)
                    missing.append({"title": str(chat_target), "link": invite.invite_link})
                except Exception as e2:
                    logger.error(f"Failed to create invite link for {chat_target}: {e2}")
                    missing.append({"title": str(chat_target), "link": ""})

    is_all_joined = len(missing) == 0
    await db.set_subscribed(user_id, is_all_joined)
    return is_all_joined, missing


def get_forcesub_keyboard(missing_chats: list[dict]) -> InlineKeyboardMarkup:
    """Builds an inline keyboard with buttons to join missing channels and a verification button."""
    buttons = []
    for chat in missing_chats:
        buttons.append([
            InlineKeyboardButton(
                text=f"📢 Join {chat['title']}",
                url=chat["link"],
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="✅ Verify Membership",
            callback_data="verify_forcesub",
        )
    ])
    return InlineKeyboardMarkup(buttons)


async def handle_chat_member_updated(client: Client, update: ChatMemberUpdated):
    """Real-time event listener for user joins/leaves on FORCESUB channels."""
    if not update.new_chat_member or not update.from_user:
        return

    user_id = update.from_user.id
    new_status = update.new_chat_member.status
    old_status = update.old_chat_member.status if update.old_chat_member else None

    # Detect Join Event
    if new_status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR] and old_status in [
        enums.ChatMemberStatus.LEFT,
        enums.ChatMemberStatus.BANNED,
        None,
    ]:
        fully_joined, _ = await check_forcesub(client, user_id)
        if fully_joined:
            await db.set_subscribed(user_id, True)
            try:
                check_tag = Emojis.tag(Emojis.CHECK, "✅")
                await client.send_message(
                    chat_id=user_id,
                    text=(
                        f"🎉 {check_tag} <b>Verification Successful!</b>\n\n"
                        f"You have joined our updates channel and are now verified to use the bot.\n"
                        f"Send any YouTube link to start downloading!"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as e:
                logger.debug(f"Could not send join DM to {user_id}: {e}")

    # Detect Leave Event
    elif new_status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
        await db.set_subscribed(user_id, False)
        try:
            cross_tag = Emojis.tag(Emojis.CROSS, "❌")
            await client.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ {cross_tag} <b>Channel Membership Alert</b>\n\n"
                    f"You have left our update channel! Your bot access has been temporarily unverified.\n"
                    f"Please re-join the channel to continue using the bot."
                ),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            logger.debug(f"Could not send leave DM to {user_id}: {e}")
