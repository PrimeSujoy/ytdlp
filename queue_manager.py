# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import asyncio
import logging
from config import Config

logger = logging.getLogger("QueueManager")


class QueueManager:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)
        # Separate semaphore for uploads — decoupled so download slots
        # release the instant the file hits disk, not after upload finishes.
        self.upload_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_UPLOADS)
        # Tracking active tasks per user: {user_id: {"playlists": int, "singles": int}}
        self._user_tasks: dict[int, dict[str, int]] = {}
        # Global queue counter
        self._global_queue_counter = 0
        self._active_task_count = 0
        self._task_sequence = 0
        # Task cancellation registry: task_id -> {"event": asyncio.Event, "processes": list[Process]}
        self._cancel_registry: dict[str, dict] = {}
        self._maintenance_active = False
        self._auxiliary_jobs = 0
        self._lock = asyncio.Lock()
        # Duplicate detection: set of dedupe keys for active tasks
        self._active_dedupe_keys: set[str] = set()

    @staticmethod
    def _is_privileged_user(user_id: int) -> bool:
        """Owner/admins may queue unlimited tasks; global concurrency still applies."""
        return user_id == Config.OWNER_ID or user_id in Config.ADMIN_ID

    @staticmethod
    def build_dedupe_key(user_id: int, video_id: str, quality: str, codec: str, container: str, mode: str) -> str:
        """Build a unique key for duplicate task detection."""
        return f"{user_id}:{video_id}:{quality}:{codec}:{container}:{mode}"

    async def is_duplicate(self, dedupe_key: str) -> bool:
        """Check if a task with this dedupe key is already active."""
        async with self._lock:
            return dedupe_key in self._active_dedupe_keys

    async def register_dedupe_key(self, dedupe_key: str):
        """Register a dedupe key for an active task."""
        async with self._lock:
            self._active_dedupe_keys.add(dedupe_key)

    async def release_dedupe_key(self, dedupe_key: str):
        """Release a dedupe key when task completes, fails, or is cancelled."""
        async with self._lock:
            self._active_dedupe_keys.discard(dedupe_key)

    async def can_add_task(self, user_id: int, is_playlist: bool = False) -> tuple[bool, str]:
        """Validates if user is within queue boundaries."""
        async with self._lock:
            if self._is_privileged_user(user_id):
                return True, ""
            user_data = self._user_tasks.get(user_id, {"playlists": 0, "singles": 0})
            if is_playlist:
                if user_data["playlists"] >= Config.MAX_PLAYLIST_QUEUE_PER_USER:
                    return False, f"⚠️ You can only have {Config.MAX_PLAYLIST_QUEUE_PER_USER} active playlist in queue."
            else:
                if user_data["singles"] >= Config.MAX_SINGLE_QUEUE_PER_USER:
                    return False, f"⚠️ You can only have up to {Config.MAX_SINGLE_QUEUE_PER_USER} active single video tasks in queue."
            return True, ""

    async def register_task(self, task_id: str, user_id: int, is_playlist: bool = False) -> int:
        """Registers a task and returns its global queue position."""
        async with self._lock:
            if user_id not in self._user_tasks:
                self._user_tasks[user_id] = {"playlists": 0, "singles": 0}

            if is_playlist:
                self._user_tasks[user_id]["playlists"] += 1
            else:
                self._user_tasks[user_id]["singles"] += 1

            self._global_queue_counter += 1
            self._task_sequence += 1
            self._active_task_count += 1
            queue_pos = self._active_task_count

            self._cancel_registry[task_id] = {
                "event": asyncio.Event(),
                "processes": [],
                "user_id": user_id,
                "is_playlist": is_playlist,
                "sequence": self._task_sequence,
                "phase": "queued",
            }
            logger.info(f"Registered task {task_id} for user {user_id} (Global Pos #{queue_pos})")
            return queue_pos

    async def try_register_task(
        self,
        task_id: str,
        user_id: int,
        is_playlist: bool = False,
        dedupe_key: str | None = None,
    ) -> tuple[bool, str, int]:
        """Atomically validates and registers a task.

        Keeping the limit check and mutation under one lock prevents two rapid
        callback clicks from both passing the per-user limit.
        """
        async with self._lock:
            if self._maintenance_active:
                return False, "⚠️ Network maintenance/speedtest running. Try again later.", 0
            # Duplicate detection
            if dedupe_key and dedupe_key in self._active_dedupe_keys:
                return False, "⚠️ Same video+quality is already in queue. Wait for the previous one to complete.", 0
            user_data = self._user_tasks.get(user_id, {"playlists": 0, "singles": 0})
            privileged = self._is_privileged_user(user_id)
            if not privileged and is_playlist and user_data["playlists"] >= Config.MAX_PLAYLIST_QUEUE_PER_USER:
                return False, f"⚠️ You can only have {Config.MAX_PLAYLIST_QUEUE_PER_USER} active playlist in queue.", 0
            if not privileged and not is_playlist and user_data["singles"] >= Config.MAX_SINGLE_QUEUE_PER_USER:
                return False, f"⚠️ You can only have up to {Config.MAX_SINGLE_QUEUE_PER_USER} active single video tasks in queue.", 0

            stored = self._user_tasks.setdefault(user_id, {"playlists": 0, "singles": 0})
            stored["playlists" if is_playlist else "singles"] += 1
            self._global_queue_counter += 1
            self._task_sequence += 1
            self._active_task_count += 1
            self._cancel_registry[task_id] = {
                "event": asyncio.Event(),
                "processes": [],
                "user_id": user_id,
                "is_playlist": is_playlist,
                "sequence": self._task_sequence,
                "dedupe_key": dedupe_key,
                "phase": "queued",
            }
            if dedupe_key:
                self._active_dedupe_keys.add(dedupe_key)
            position = self._live_position_unlocked(task_id)
            logger.info(f"Registered task {task_id} for user {user_id} (Global Pos #{position})")
            return True, "", position

    def _live_position_unlocked(self, task_id: str) -> int:
        entry = self._cancel_registry.get(task_id)
        if not entry:
            return 0
        sequence = entry.get("sequence", 0)
        return 1 + sum(
            1
            for other in self._cancel_registry.values()
            if other.get("sequence", 0) < sequence
        )

    async def get_live_position(self, task_id: str) -> int:
        async with self._lock:
            return self._live_position_unlocked(task_id)

    async def has_active_tasks(self) -> bool:
        async with self._lock:
            return bool(self._cancel_registry)

    async def get_active_tasks(self) -> list[dict]:
        """Returns list of all active tasks with their info."""
        async with self._lock:
            tasks = []
            for task_id, entry in sorted(self._cancel_registry.items(), key=lambda x: x[1].get("sequence", 0)):
                tasks.append({
                    "task_id": task_id,
                    "user_id": entry["user_id"],
                    "is_playlist": entry.get("is_playlist", False),
                    "is_cancelled": entry["event"].is_set(),
                    "phase": entry.get("phase", "queued"),
                })
            return tasks

    async def set_task_phase(self, task_id: str, phase: str) -> None:
        """Update the phase of a task: queued / downloading / processing / uploading."""
        async with self._lock:
            if task_id in self._cancel_registry:
                self._cancel_registry[task_id]["phase"] = phase

    async def get_phase_counts(self) -> dict[str, int]:
        """Returns counts of tasks in each phase (excluding cancelled)."""
        async with self._lock:
            counts = {"downloading": 0, "uploading": 0, "queued": 0, "processing": 0}
            for entry in self._cancel_registry.values():
                if entry["event"].is_set():
                    continue
                phase = entry.get("phase", "queued")
                if phase in counts:
                    counts[phase] += 1
            return counts

    async def get_user_tasks(self, user_id: int) -> list[dict]:
        """Returns active tasks for a specific user (excluding cancelled)."""
        async with self._lock:
            tasks = []
            for task_id, entry in sorted(self._cancel_registry.items(), key=lambda x: x[1].get("sequence", 0)):
                if entry["user_id"] == user_id and not entry["event"].is_set():
                    tasks.append({
                        "task_id": task_id,
                        "is_playlist": entry.get("is_playlist", False),
                        "phase": entry.get("phase", "queued"),
                        "sequence": entry.get("sequence", 0),
                    })
            return tasks

    async def try_begin_maintenance(self) -> bool:
        """Atomically reserve network bandwidth only when no task is active."""
        async with self._lock:
            if self._maintenance_active or self._cancel_registry or self._auxiliary_jobs:
                return False
            self._maintenance_active = True
            return True

    async def end_maintenance(self) -> None:
        async with self._lock:
            self._maintenance_active = False

    async def is_maintenance_active(self) -> bool:
        async with self._lock:
            return self._maintenance_active

    async def try_begin_auxiliary(self) -> bool:
        """Register a short network-heavy job unless maintenance is active."""
        async with self._lock:
            if self._maintenance_active:
                return False
            self._auxiliary_jobs += 1
            return True

    async def end_auxiliary(self) -> None:
        async with self._lock:
            self._auxiliary_jobs = max(0, self._auxiliary_jobs - 1)

    def register_process(self, task_id: str, process: asyncio.subprocess.Process):
        """Attaches an active subprocess (yt-dlp/ffmpeg) to allow instantaneous killing on cancel."""
        if task_id in self._cancel_registry:
            self._cancel_registry[task_id]["processes"].append(process)

    def is_cancelled(self, task_id: str) -> bool:
        if task_id in self._cancel_registry:
            return self._cancel_registry[task_id]["event"].is_set()
        return False

    async def cancel_task(self, task_id: str) -> bool:
        """Signals cancellation and terminates all associated subprocesses."""
        if task_id in self._cancel_registry:
            entry = self._cancel_registry[task_id]
            entry["event"].set()
            dedupe_key = entry.get("dedupe_key")
            if dedupe_key:
                self._active_dedupe_keys.discard(dedupe_key)
            for proc in entry["processes"]:
                try:
                    proc.kill()
                    logger.info(f"Terminated subprocess for cancelled task {task_id}")
                except Exception:
                    pass
            return True
        return False

    async def complete_task(self, task_id: str):
        """Frees up user and global queue slots."""
        async with self._lock:
            if task_id in self._cancel_registry:
                entry = self._cancel_registry.pop(task_id)
                user_id = entry["user_id"]
                is_playlist = entry["is_playlist"]
                dedupe_key = entry.get("dedupe_key")
                if dedupe_key:
                    self._active_dedupe_keys.discard(dedupe_key)

                if user_id in self._user_tasks:
                    if is_playlist:
                        self._user_tasks[user_id]["playlists"] = max(0, self._user_tasks[user_id]["playlists"] - 1)
                    else:
                        self._user_tasks[user_id]["singles"] = max(0, self._user_tasks[user_id]["singles"] - 1)

                    if self._user_tasks[user_id]["playlists"] == 0 and self._user_tasks[user_id]["singles"] == 0:
                        del self._user_tasks[user_id]

                self._active_task_count = max(0, self._active_task_count - 1)
                logger.info(f"Completed task {task_id} for user {user_id}")

    async def cancel_user_tasks(self, user_id: int) -> int:
        """Cancel all tasks for a specific user. Returns count of cancelled tasks."""
        cancelled_count = 0
        task_ids = []
        async with self._lock:
            for tid, entry in self._cancel_registry.items():
                if entry["user_id"] == user_id and not entry["event"].is_set():
                    task_ids.append(tid)
        for tid in task_ids:
            await self.cancel_task(tid)
            cancelled_count += 1
        return cancelled_count

    async def cancel_all_tasks(self) -> int:
        """Cancel ALL tasks for ALL users. Returns count of cancelled tasks."""
        cancelled_count = 0
        task_ids = []
        async with self._lock:
            for tid, entry in self._cancel_registry.items():
                if not entry["event"].is_set():
                    task_ids.append(tid)
        for tid in task_ids:
            await self.cancel_task(tid)
            cancelled_count += 1
        return cancelled_count


# Global queue manager instance
queue_manager = QueueManager()
