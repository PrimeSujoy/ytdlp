# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import time
import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

logger = logging.getLogger("Database")
MEDIA_CACHE_VERSION = 2


def cache_quality_candidates(requested_quality: str, is_audio: bool = False) -> list[str]:
    """Return cache keys that can satisfy the playlist request without metadata.

    Older playlist cache rows were stored under the effective fallback quality
    instead of the quality selected by the user.  Search those historical keys
    in the same order used by the playlist quality fallback.
    """
    requested = str(requested_quality or "best")
    if is_audio:
        return [requested]

    fallback_order = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "best"]
    return [requested, *(quality for quality in fallback_order if quality != requested)]


class Database:
    def __init__(self, uri: str):
        self._primary_uri = uri
        self._client = AsyncIOMotorClient(uri)
        try:
            self.db = self._client.get_default_database()
        except Exception:
            self.db = None

        if self.db is None or getattr(self.db, "name", "") == "test":
            self.db = self._client["advanced_ytdlp_bot"]

        self.users = self.db["users"]
        self.preferences = self.db["preferences"]
        self.downloads = self.db["downloads"]
        self.stats = self.db["stats"]
        self.cached_media = self.db["cached_media"]
        self.thumbnails = self.db["thumbnails"]

        # Multi-DB: secondary connections for cache failover
        self._secondary_clients: list[tuple[AsyncIOMotorClient, object]] = []
        self._owner_db_warning_sent = False

    async def init(self):
        """Ensure indexes for optimal query speeds."""
        try:
            await self.users.create_index("user_id", unique=True)
            await self.preferences.create_index("user_id", unique=True)
            await self.thumbnails.create_index("user_id", unique=True)
            await self.downloads.create_index([("user_id", 1), ("timestamp", -1)])
            await self.downloads.create_index("timestamp")
            await self.cached_media.create_index([
                ("video_id", 1),
                ("quality", 1),
                ("as_document", 1),
                ("split_mode", 1)
            ], unique=True)
            logger.info("✅ MongoDB Indexes Initialized")
        except Exception as e:
            logger.warning(f"⚠️ Index initialization notice: {e}")

        # Load and connect secondary DBs
        await self._load_secondary_uris()

    async def check_db_size(self) -> dict:
        """Check primary DB size. Returns dict with size info."""
        try:
            stats = await self.db.command("dbStats")
            data_size_mb = stats.get("dataSize", 0) / (1024 * 1024)
            storage_mb = stats.get("storageSize", 0) / (1024 * 1024)
            collections = stats.get("collections", 0)
            return {
                "data_size_mb": round(data_size_mb, 2),
                "storage_mb": round(storage_mb, 2),
                "collections": collections,
                "should_warn": data_size_mb >= 400 and not self._owner_db_warning_sent,
            }
        except Exception as e:
            logger.warning(f"⚠️ Could not check DB size: {e}")
            return {"data_size_mb": 0, "storage_mb": 0, "collections": 0, "should_warn": False}

    def mark_db_warning_sent(self):
        self._owner_db_warning_sent = True

    # -------------------------------------------------------------------------
    # Multi-DB: Secondary Connection Management
    # -------------------------------------------------------------------------
    async def _load_secondary_uris(self):
        try:
            settings = await self.db["settings"].find_one({"_id": "secondary_dbs"})
            if not settings:
                return
            uris = settings.get("uris", [])
            for uri in uris:
                await self._connect_secondary(uri, quiet=True)
        except Exception as e:
            logger.warning(f"⚠️ Could not load secondary DB URIs: {e}")

    async def _connect_secondary(self, uri: str, quiet: bool = False) -> bool:
        for client, _ in self._secondary_clients:
            if getattr(client, "_secondary_uri", "") == uri:
                if not quiet:
                    logger.warning("⚠️ This URI is already connected.")
                return False
        try:
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
            await client.admin.command("ping")
            secondary_db = client.get_default_database()
            if secondary_db is None or getattr(secondary_db, "name", "") == "test":
                secondary_db = client["advanced_ytdlp_bot"]
            client._secondary_uri = uri
            self._secondary_clients.append((client, secondary_db))
            if not quiet:
                logger.info(f"✅ Secondary DB connected: {uri[:40]}...")
            return True
        except Exception as e:
            if not quiet:
                logger.error(f"❌ Secondary DB connection failed: {e}")
            return False

    async def add_secondary_db(self, uri: str) -> tuple[bool, str]:
        uri = uri.strip()
        if not uri:
            return False, "❌ URI is empty."
        if uri == self._primary_uri:
            return False, "❌ This is the primary URI."
        if not uri.startswith("mongodb"):
            return False, "❌ Invalid URI. Must start with mongodb:// or mongodb+srv://"
        ok = await self._connect_secondary(uri)
        if not ok:
            return False, "❌ Connection failed. Check URI and try again."
        try:
            settings = await self.db["settings"].find_one({"_id": "secondary_dbs"})
            existing = settings.get("uris", []) if settings else []
            if uri not in existing:
                existing.append(uri)
            await self.db["settings"].update_one(
                {"_id": "secondary_dbs"},
                {"$set": {"uris": existing}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not save secondary URI to settings: {e}")
        return True, f"✅ Connected! Total secondary DBs: {len(self._secondary_clients)}"

    async def remove_secondary_db(self, uri: str) -> tuple[bool, str]:
        uri = uri.strip()
        removed = False
        for i, (client, _) in enumerate(self._secondary_clients):
            if getattr(client, "_secondary_uri", "") == uri:
                try:
                    client.close()
                except Exception:
                    pass
                self._secondary_clients.pop(i)
                removed = True
                break
        try:
            settings = await self.db["settings"].find_one({"_id": "secondary_dbs"})
            existing = settings.get("uris", []) if settings else []
            existing = [u for u in existing if u != uri]
            await self.db["settings"].update_one(
                {"_id": "secondary_dbs"},
                {"$set": {"uris": existing}},
                upsert=True,
            )
        except Exception:
            pass
        if removed:
            return True, f"✅ Removed. Remaining: {len(self._secondary_clients)}"
        return False, "❌ URI not found in connected list."

    def get_secondary_uris(self) -> list[str]:
        result = []
        for client, _ in self._secondary_clients:
            uri = getattr(client, "_secondary_uri", "")
            if uri:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(uri)
                    if parsed.password:
                        masked = uri.replace(parsed.password, "***")
                        result.append(masked)
                    else:
                        result.append(uri)
                except Exception:
                    result.append(uri[:40] + "...")
        return result

    # -------------------------------------------------------------------------
    # Multi-DB: Cache Mirroring
    # -------------------------------------------------------------------------
    async def _mirror_cache_to_secondaries(self, doc: dict, filter_query: dict):
        for client, sdb in self._secondary_clients:
            try:
                await sdb["cached_media"].update_one(filter_query, {"$set": doc}, upsert=True)
            except Exception as e:
                logger.warning(f"⚠️ Secondary DB mirror write failed: {e}")

    # -------------------------------------------------------------------------
    # Multi-DB: Cache Failover Read
    # -------------------------------------------------------------------------
    async def _fallback_cache_read(self, filter_query: dict) -> dict | None:
        for client, sdb in self._secondary_clients:
            try:
                result = await sdb["cached_media"].find_one(filter_query)
                if result:
                    return result
            except Exception:
                continue
        return None

    # -------------------------------------------------------------------------
    # User Management
    # -------------------------------------------------------------------------
    async def add_or_update_user(self, user_id: int, username: str = "", first_name: str = ""):
        now = time.time()
        user = await self.users.find_one({"user_id": user_id})
        if not user:
            new_user = {
                "user_id": user_id,
                "username": username or "",
                "first_name": first_name or "",
                "joined_date": now,
                "last_active": now,
                "is_banned": False,
                "is_subscribed": False,
            }
            await self.users.insert_one(new_user)
            await self.preferences.update_one(
                {"user_id": user_id},
                {
                    "$setOnInsert": {
                        "user_id": user_id,
                        "media_preference": Config.DEFAULT_MEDIA_PREFERENCE,
                        "codec": Config.DEFAULT_CODEC,
                        "container": Config.DEFAULT_CONTAINER,
                        "split_mode": Config.DEFAULT_SPLIT_MODE,
                        "destination_chat_id": None,
                        "subtitle_lang": None,
                    }
                },
                upsert=True,
            )
        else:
            await self.users.update_one(
                {"user_id": user_id},
                {"$set": {"username": username or user.get("username", ""),
                          "first_name": first_name or user.get("first_name", ""),
                          "last_active": now}}
            )

    async def get_user(self, user_id: int):
        return await self.users.find_one({"user_id": user_id})

    async def is_banned(self, user_id: int) -> bool:
        user = await self.users.find_one({"user_id": user_id}, {"is_banned": 1})
        return bool(user and user.get("is_banned", False))

    async def get_ban_reason(self, user_id: int) -> str | None:
        """Return the ban reason for a user, or None if not banned / no reason."""
        user = await self.users.find_one({"user_id": user_id}, {"ban_reason": 1})
        if user and user.get("is_banned"):
            return user.get("ban_reason")
        return None

    async def set_ban_status(self, user_id: int, is_banned: bool, reason: str | None = None):
        update: dict = {"$set": {"is_banned": is_banned}}
        if is_banned:
            update["$set"]["ban_reason"] = reason or "No reason provided"
        else:
            update["$unset"] = {"ban_reason": ""}
        await self.users.update_one({"user_id": user_id}, update, upsert=True)

    async def unban_all(self) -> int:
        """Unban all banned users. Returns count of unbanned users."""
        result = await self.users.update_many(
            {"is_banned": True},
            {"$set": {"is_banned": False}, "$unset": {"ban_reason": ""}}
        )
        return result.modified_count

    async def get_banned_users_paged(self, page: int = 1, per_page: int = 10) -> tuple[list, int]:
        """Return (list of banned user dicts, total_banned) for pagination."""
        total = await self.users.count_documents({"is_banned": True})
        skip = (page - 1) * per_page
        cursor = self.users.find(
            {"is_banned": True},
            {"user_id": 1, "username": 1, "first_name": 1, "ban_reason": 1}
        ).sort("user_id", 1).skip(skip).limit(per_page)
        users = await cursor.to_list(length=per_page)
        return users, total

    async def set_subscribed(self, user_id: int, status: bool):
        await self.users.update_one({"user_id": user_id}, {"$set": {"is_subscribed": status}}, upsert=True)

    async def is_subscribed(self, user_id: int) -> bool:
        user = await self.users.find_one({"user_id": user_id}, {"is_subscribed": 1})
        return bool(user and user.get("is_subscribed", False))

    async def get_all_users(self):
        """Returns cursor of all active users for broadcast."""
        return self.users.find({"is_banned": False}, {"user_id": 1})

    # -------------------------------------------------------------------------
    # User Preferences
    # -------------------------------------------------------------------------
    async def get_preferences(self, user_id: int) -> dict:
        prefs = await self.preferences.find_one({"user_id": user_id})
        if not prefs:
            prefs = {
                "user_id": user_id,
                "media_preference": Config.DEFAULT_MEDIA_PREFERENCE,
                "codec": Config.DEFAULT_CODEC,
                "container": Config.DEFAULT_CONTAINER,
                "split_mode": Config.DEFAULT_SPLIT_MODE,
                "destination_chat_id": None,
                "subtitle_lang": None,
            }
            await self.preferences.update_one({"user_id": user_id}, {"$set": prefs}, upsert=True)
        return prefs

    async def update_preference(self, user_id: int, key: str, value):
        await self.preferences.update_one(
            {"user_id": user_id},
            {"$set": {key: value}},
            upsert=True
        )

    # -------------------------------------------------------------------------
    # Custom Thumbnail System
    # -------------------------------------------------------------------------
    async def set_custom_thumbnail(self, user_id: int, file_id: str):
        await self.thumbnails.update_one(
            {"user_id": user_id},
            {"$set": {"file_id": file_id, "updated_at": time.time()}},
            upsert=True,
        )

    async def get_custom_thumbnail(self, user_id: int) -> str | None:
        doc = await self.thumbnails.find_one({"user_id": user_id})
        return doc.get("file_id") if doc else None

    async def delete_custom_thumbnail(self, user_id: int) -> bool:
        res = await self.thumbnails.delete_one({"user_id": user_id})
        return res.deleted_count > 0

    # -------------------------------------------------------------------------
    # Dump Channel Instant Cache System (Multi-DB)
    # -------------------------------------------------------------------------
    async def save_cached_media(
        self,
        video_id: str,
        quality: str,
        as_document: bool,
        split_mode: str,
        dump_chat_id: int,
        message_ids: list[int],
        title: str,
        file_size_bytes: int,
        codec: str,
        container: str,
        effective_quality: str | None = None,
    ):
        doc = {
            "video_id": video_id,
            "quality": quality,
            "as_document": as_document,
            "split_mode": split_mode,
            "dump_chat_id": dump_chat_id,
            "message_ids": message_ids,
            "title": title,
            "size_bytes": file_size_bytes,
            "codec": codec,
            "container": container,
            "effective_quality": effective_quality or quality,
            "cache_version": MEDIA_CACHE_VERSION,
            "cached_at": time.time(),
        }
        filter_query = {
            "video_id": video_id,
            "quality": quality,
            "as_document": as_document,
            "split_mode": split_mode,
        }
        await self.cached_media.update_one(filter_query, {"$set": doc}, upsert=True)
        logger.info(f"⚡ Cached {video_id} ({quality}) in Dump Channel {dump_chat_id} with msgs {message_ids}")
        if self._secondary_clients:
            asyncio.get_event_loop().create_task(
                self._mirror_cache_to_secondaries(doc, filter_query)
            )

    async def get_cached_media(
        self,
        video_id: str,
        quality: str,
        as_document: bool,
        split_mode: str,
        codec: str,
        container: str,
    ) -> dict | None:
        filter_query = {
            "video_id": video_id,
            "quality": quality,
            "as_document": as_document,
            "split_mode": split_mode,
            "codec": codec,
            "container": container,
            "cache_version": MEDIA_CACHE_VERSION,
        }
        try:
            result = await self.cached_media.find_one(filter_query)
            if result:
                return result
        except Exception as e:
            logger.warning(f"⚠️ Primary DB cache read failed: {e}")
        return await self._fallback_cache_read(filter_query)

    async def get_cached_media_simple(self, video_id: str, quality: str) -> dict | None:
        filter_query = {
            "video_id": video_id,
            "quality": quality,
            "cache_version": MEDIA_CACHE_VERSION,
        }
        try:
            result = await self.cached_media.find_one(filter_query)
            if result:
                return result
        except Exception as e:
            logger.warning(f"⚠️ Primary DB simple cache read failed: {e}")
        return await self._fallback_cache_read(filter_query)

    # -------------------------------------------------------------------------
    # Download Metrics & Leaderboard
    # -------------------------------------------------------------------------
    async def record_download(self, user_id: int, video_id: str, title: str, file_size_bytes: int, quality: str = "", mode: str = "video"):
        now = time.time()
        record = {
            "user_id": user_id,
            "video_id": video_id,
            "title": title,
            "size_bytes": file_size_bytes,
            "quality": quality,
            "mode": mode,
            "timestamp": now,
        }
        await self.downloads.insert_one(record)
        # Keep only last 30 records per user to prevent DB bloat
        MAX_HISTORY = 30
        count = await self.downloads.count_documents({"user_id": user_id})
        if count > MAX_HISTORY:
            # Get oldest record IDs to delete
            oldest = await self.downloads.find(
                {"user_id": user_id}
            ).sort("timestamp", 1).limit(count - MAX_HISTORY).to_list(length=count - MAX_HISTORY)
            if oldest:
                ids_to_delete = [doc["_id"] for doc in oldest]
                await self.downloads.delete_many({"_id": {"$in": ids_to_delete}})

    async def get_user_history(self, user_id: int, limit: int = 10) -> list[dict]:
        """Returns recent downloads for a specific user, newest first."""
        cursor = self.downloads.find(
            {"user_id": user_id},
            {"_id": 0, "video_id": 1, "title": 1, "quality": 1, "mode": 1, "size_bytes": 1, "timestamp": 1},
        ).sort("timestamp", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_user_history_count(self, user_id: int) -> int:
        """Returns total download count for a user."""
        return await self.downloads.count_documents({"user_id": user_id})

    async def get_user_history_paged(self, user_id: int, page: int = 1, per_page: int = 10) -> list[dict]:
        """Returns paginated downloads for a specific user, newest first."""
        skip = (page - 1) * per_page
        cursor = self.downloads.find(
            {"user_id": user_id},
            {"_id": 0, "video_id": 1, "title": 1, "quality": 1, "mode": 1, "size_bytes": 1, "timestamp": 1},
        ).sort("timestamp", -1).skip(skip).limit(per_page)
        return await cursor.to_list(length=per_page)

    async def get_leaderboard(self, limit: int = 10, period: str = "all") -> list[dict]:
        """
        Returns top users ranked by total download count and total size in bytes.
        period: 'weekly', 'monthly', 'all'
        """
        match_query = {}
        now = time.time()
        if period == "weekly":
            match_query = {"timestamp": {"$gte": now - 7 * 86400}}
        elif period == "monthly":
            match_query = {"timestamp": {"$gte": now - 30 * 86400}}

        pipeline = [
            {"$match": match_query} if match_query else {"$match": {}},
            {
                "$group": {
                    "_id": "$user_id",
                    "total_downloads": {"$sum": 1},
                    "total_bytes": {"$sum": "$size_bytes"},
                }
            },
            {"$sort": {"total_bytes": -1, "total_downloads": -1}},
            {"$limit": limit},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "user_id",
                    "as": "user_info",
                }
            },
            {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}},
            {
                "$project": {
                    "user_id": "$_id",
                    "total_downloads": 1,
                    "total_bytes": 1,
                    "username": "$user_info.username",
                    "first_name": "$user_info.first_name",
                }
            },
        ]
        return await self.downloads.aggregate(pipeline).to_list(length=limit)

    async def get_user_rank(self, user_id: int) -> dict:
        """Returns specific user's total downloads, bytes, and approximate rank."""
        user_stats = await self.downloads.aggregate([
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": "$user_id", "total_downloads": {"$sum": 1}, "total_bytes": {"$sum": "$size_bytes"}}}
        ]).to_list(length=1)

        if not user_stats:
            return {"rank": "N/A", "total_downloads": 0, "total_bytes": 0}

        my_bytes = user_stats[0]["total_bytes"]
        higher_count = await self.downloads.aggregate([
            {"$group": {"_id": "$user_id", "total_bytes": {"$sum": "$size_bytes"}}},
            {"$match": {"total_bytes": {"$gt": my_bytes}}},
            {"$count": "count"}
        ]).to_list(length=1)

        rank = (higher_count[0]["count"] + 1) if higher_count else 1
        return {
            "rank": rank,
            "total_downloads": user_stats[0]["total_downloads"],
            "total_bytes": my_bytes,
        }

    async def get_global_stats(self) -> dict:
        total_users = await self.users.count_documents({})
        total_downloads = await self.downloads.count_documents({})
        bytes_agg = await self.downloads.aggregate([
            {"$group": {"_id": None, "total_bytes": {"$sum": "$size_bytes"}}}
        ]).to_list(length=1)
        total_bytes = bytes_agg[0]["total_bytes"] if bytes_agg else 0
        return {
            "total_users": total_users,
            "total_downloads": total_downloads,
            "total_bytes": total_bytes,
        }

    # -------------------------------------------------------------------------
    # Cookie Persistence (MongoDB)
    # -------------------------------------------------------------------------
    async def save_cookie(self, filename: str, content_bytes: bytes):
        """Save a cookie file to MongoDB for cross-device persistence."""
        import base64
        b64 = base64.b64encode(content_bytes).decode("utf-8")
        await self.db["cookies"].update_one(
            {"filename": filename},
            {"$set": {"filename": filename, "content_b64": b64, "updated_at": time.time()}},
            upsert=True,
        )

    async def get_all_cookies_from_db(self) -> list[dict]:
        """Retrieve all saved cookies from MongoDB."""
        import base64
        cursor = self.db["cookies"].find({})
        result = []
        async for doc in cursor:
            content_bytes = base64.b64decode(doc["content_b64"])
            result.append({
                "filename": doc["filename"],
                "content_bytes": content_bytes,
            })
        return result

    async def delete_cookie_from_db(self, filename: str):
        """Delete a cookie from MongoDB."""
        await self.db["cookies"].delete_one({"filename": filename})

    async def load_cookies_to_disk(self):
        """Load all cookies from MongoDB to disk on startup."""
        import base64
        cursor = self.db["cookies"].find({})
        loaded = 0
        async for doc in cursor:
            content_bytes = base64.b64decode(doc["content_b64"])
            filename = doc["filename"]
            path = os.path.join(Config.COOKIES_DIR, filename)
            try:
                with open(path, "wb") as f:
                    f.write(content_bytes)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load cookie {filename} to disk: {e}")
        if loaded:
            logger.info(f"Loaded {loaded} cookies from MongoDB to disk")

    # -------------------------------------------------------------------------
    # Resolver Cookie Persistence
    # -------------------------------------------------------------------------
    async def set_resolver_cookie(self, filename: str | None):
        """Save the resolver cookie filename to MongoDB. Pass None to clear."""
        if filename:
            await self.db["settings"].update_one(
                {"key": "resolver_cookie"},
                {"$set": {"key": "resolver_cookie", "value": filename, "updated_at": time.time()}},
                upsert=True,
            )
        else:
            await self.db["settings"].delete_one({"key": "resolver_cookie"})

    async def get_resolver_cookie(self) -> str | None:
        """Retrieve the resolver cookie filename from MongoDB."""
        doc = await self.db["settings"].find_one({"key": "resolver_cookie"})
        if doc:
            return doc.get("value")
        return None


# Global database instance
db = Database(Config.DB_URI)
