# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import logging
from pyrogram import Client, enums
from database import db

logger = logging.getLogger("Destination")


async def verify_destination_chat(client: Client, chat_target: str | int, requesting_user_id: int) -> tuple[bool, str]:
    """
    Verifies that the bot is an Administrator with message posting permissions
    in the target channel/group, and that the requesting user is authorized.
    """
    try:
        chat = await client.get_chat(chat_target)
    except Exception as e:
        return False, f"❌ Bot cannot find or access chat: {e}"

    if chat.type == enums.ChatType.CHANNEL:
        try:
            bot_member = await client.get_chat_member(chat.id, "me")
            if not bot_member.privileges or not bot_member.privileges.can_post_messages:
                return False, "❌ Bot must be an **Administrator** with **Post Messages** permission in this channel."
        except Exception as e:
            return False, f"❌ Failed to verify bot admin rights in channel: {e}"

    elif chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        try:
            bot_member = await client.get_chat_member(chat.id, "me")
            if bot_member.status in [enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED]:
                return False, "❌ Bot is restricted or banned from sending messages in this group."
        except Exception as e:
            return False, f"❌ Failed to verify bot status in group: {e}"
    else:
        return False, "❌ Destination must be a Channel, Group, or Supergroup."

    # Verify user is an admin or member of that chat (prevents dumping files into random chats)
    try:
        user_member = await client.get_chat_member(chat.id, requesting_user_id)
        if user_member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
            return False, "❌ You must be a member or admin of the destination chat."
    except Exception:
        # In private channels, get_chat_member for user might fail if bot isn't owner, which is fine if bot is admin
        pass

    return True, chat.title or str(chat.id)


async def get_target_upload_chat(user_id: int, current_chat_id: int, chat_type: enums.ChatType) -> int:
    """
    Determines the final upload destination chat ID based on routing rules:
    - If triggered inside a Group/Supergroup: always upload directly into that group.
    - If triggered inside PM: upload to configured destination channel/group if set, otherwise PM.
    """
    if chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return current_chat_id

    prefs = await db.get_preferences(user_id)
    dest_id = prefs.get("destination_chat_id")
    if dest_id:
        return dest_id

    return current_chat_id
