# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import asyncio
import html
import math
import os
import re
import shutil
import time
from collections import OrderedDict
from importlib.metadata import PackageNotFoundError, version

import psutil
from pyrogram import StopTransmission, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

try:
    from pyrogram.enums import ButtonStyle as _ButtonStyle
except ImportError:
    _ButtonStyle = None

from queue_manager import queue_manager
from rate_limiter import rate_limiter


BOT_STARTED_AT = time.time()
PROGRESS_EDIT_INTERVAL = 8.0

# Cached disk stats — refreshed at most once every 30 seconds to avoid
# expensive filesystem calls on every progress update.
_disk_cache_ts = 0.0
_disk_cache_free = 0
_DISK_CACHE_TTL = 30.0


def _distribution_version(*names: str) -> str:
    for name in names:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "unknown"


YTDLP_ENGINE = f"yt-dlp v{_distribution_version('yt-dlp')}"
_wzgram_version = _distribution_version("wzgram")
TELEGRAM_ENGINE = (
    f"WZGram v{_wzgram_version}"
    if _wzgram_version != "unknown"
    else f"Pyrogram v{_distribution_version('pyrogram')}"
)


def make_bar(percent: float, length: int = 12) -> str:
    value = max(0.0, min(100.0, float(percent or 0)))
    filled = min(length, int(value * length / 100))
    return "❨" + ("⬢" * filled) + ("⬡" * (length - filled)) + "❩"


def format_bytes(size: float) -> str:
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        size = 0
    if math.isnan(size) or size < 0:
        size = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return "0 B"


def format_time(seconds: float) -> str:
    try:
        seconds = float(seconds or 0)
    except (TypeError, ValueError):
        return "N/A"
    if seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "N/A"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m{seconds}s"
    return f"{minutes}m{seconds}s"


def parse_size_text(value: str) -> float:
    text = str(value or "").strip().replace("~", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)", text, re.I)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).upper().replace("IB", "B")
    power = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}.get(unit, 0)
    return number * (1024 ** power)


class ProgressDashboardManager:
    """Owns one live progress message for every user with active DM tasks."""

    def __init__(self):
        self._sessions: dict[int, dict] = {}
        self._lock = asyncio.Lock()

    async def register(self, tracker: "ProgressTracker") -> None:
        if tracker.user_id is None:
            return
        old_message = None
        async with self._lock:
            session = self._sessions.get(tracker.user_id)
            if session is None:
                session = {
                    "message": tracker.message,
                    "tasks": OrderedDict(),
                    "worker": None,
                    "dirty": False,
                    "last_edit": 0.0,
                }
                self._sessions[tracker.user_id] = session
            else:
                # Keep the dashboard beside the newest request. All existing
                # trackers are rebound before the old message is removed.
                old_message = session["message"]
                session["message"] = tracker.message
                for existing in session["tasks"].values():
                    existing.message = tracker.message
            tracker.dashboard = self
            session["tasks"][tracker.task_id] = tracker
            session["dirty"] = True
        if old_message and old_message.id != tracker.message.id:
            try:
                await old_message.delete()
            except Exception:
                pass
        self.request_refresh(tracker.user_id, force=True)

    async def update(self, tracker: "ProgressTracker", card: str, force: bool = False) -> None:
        async with self._lock:
            session = self._sessions.get(tracker.user_id)
            if not session or session["tasks"].get(tracker.task_id) is not tracker:
                return
            tracker._latest_card = card
            session["dirty"] = True
        self.request_refresh(tracker.user_id, force=force)

    def request_refresh(self, user_id: int | None, force: bool = False) -> None:
        if user_id is None:
            return
        session = self._sessions.get(user_id)
        if not session:
            return
        session["dirty"] = True
        worker = session.get("worker")
        if not worker or worker.done():
            session["worker"] = asyncio.create_task(self._worker(user_id, force))

    async def _worker(self, user_id: int, force: bool) -> None:
        try:
            while True:
                async with self._lock:
                    session = self._sessions.get(user_id)
                    if not session or not session["dirty"]:
                        return
                    delay = 0.0 if force else max(
                        0.0, PROGRESS_EDIT_INTERVAL - (time.time() - session["last_edit"])
                    )
                    session["dirty"] = False
                if delay:
                    await asyncio.sleep(delay)

                async with self._lock:
                    session = self._sessions.get(user_id)
                    if not session:
                        return
                    message = session["message"]
                    trackers = list(session["tasks"].values())
                if not trackers:
                    return

                blocks = []
                all_queued = True
                for index, tracker in enumerate(trackers, 1):
                    position = await queue_manager.get_live_position(tracker.task_id)
                    card = tracker._latest_card or tracker._queued_card(position)
                    if not tracker._queued:
                        all_queued = False
                    cancel_line = f"\n╰ Cancel » /c{html.escape(tracker.task_id)}"
                    blocks.append(
                        f"<b>❲{index}❳ Task ID:</b> <code>{html.escape(tracker.task_id)}</code> "
                        f"| <b>Global:</b> <code>#{position or '-'}</code>\n{card}{cancel_line}"
                    )

                # Skip auto-edit while ALL trackers are queued to avoid
                # unnecessary message edits and Telegram FloodWait.
                if all_queued and not force:
                    async with self._lock:
                        current = self._sessions.get(user_id)
                        if current:
                            current["dirty"] = False
                        return
                dl_total = sum(parse_size_text(t.last_download_speed) for t in trackers)
                ul_total = sum(parse_size_text(t.last_upload_speed) for t in trackers)
                stats_owner = trackers[0]
                old_dl, old_ul = stats_owner.last_download_speed, stats_owner.last_upload_speed
                stats_owner.last_download_speed = f"{format_bytes(dl_total)}/s"
                stats_owner.last_upload_speed = f"{format_bytes(ul_total)}/s"
                footer = stats_owner._stats()
                stats_owner.last_download_speed, stats_owner.last_upload_speed = old_dl, old_ul
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="q_refresh"),
                ]])
                try:
                    wait_status = await rate_limiter.wait_if_needed()
                    edit_text = "\n\n".join(blocks) + "\n\n" + footer
                    if wait_status:
                        edit_text = f"{wait_status}\n\n{edit_text}"
                    await message.edit_text(
                        edit_text,
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                    rate_limiter.record_send()
                except asyncio.CancelledError:
                    return
                except Exception:
                    pass
                async with self._lock:
                    current = self._sessions.get(user_id)
                    if not current:
                        return
                    current["last_edit"] = time.time()
                    if not current["dirty"]:
                        return
                force = False
        finally:
            restart = False
            async with self._lock:
                session = self._sessions.get(user_id)
                if session and session.get("worker") is asyncio.current_task():
                    session["worker"] = None
                    restart = bool(session.get("dirty"))
            if restart:
                self.request_refresh(user_id)

    async def remove(self, tracker: "ProgressTracker") -> None:
        message = None
        worker = None
        async with self._lock:
            session = self._sessions.get(tracker.user_id)
            if not session or session["tasks"].get(tracker.task_id) is not tracker:
                return
            session["tasks"].pop(tracker.task_id, None)
            if not session["tasks"]:
                message = session["message"]
                worker = session.get("worker")
                self._sessions.pop(tracker.user_id, None)
            else:
                session["dirty"] = True
        if worker and not worker.done() and worker is not asyncio.current_task():
            worker.cancel()
        if message:
            try:
                await message.delete()
            except Exception:
                pass
        else:
            self.request_refresh(tracker.user_id, force=True)

    def refresh_all(self) -> None:
        for user_id in list(self._sessions):
            self.request_refresh(user_id, force=True)


progress_dashboard = ProgressDashboardManager()


class ProgressTracker:
    def __init__(
        self,
        message: Message,
        task_id: str,
        title: str = "Media",
        task_by: str = "User",
        user_id: int | None = None,
        expected_download_bytes: int = 0,
    ):
        self.message = message
        self.task_id = task_id
        self.title = str(title or "Media")[:80]
        self.task_by = str(task_by or "User")[:48]
        self.user_id = user_id
        self.expected_download_bytes = max(0, int(expected_download_bytes or 0))
        self.workflow_started = time.time()
        self.phase_started = self.workflow_started
        self.last_edit_time = 0.0
        self.last_download_speed = "0 B/s"
        self.last_upload_speed = "0 B/s"
        self.part_index = 1
        self.total_parts = 1
        self.playlist_index = 0
        self.playlist_total = 0
        self._edit_task: asyncio.Task | None = None
        self._processing_task: asyncio.Task | None = None
        self._download_streams: dict[str, float] = {}
        self._generation = 0
        self._closed = False
        self.dashboard: ProgressDashboardManager | None = None
        self._latest_card = ""
        self._queued = True

    def reset_download_attempt(self) -> None:
        self._download_streams.clear()
        self.phase_started = time.time()

    def begin_upload(self, part_index: int, total_parts: int) -> None:
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        self._processing_task = None
        self.part_index = max(1, int(part_index))
        self.total_parts = max(1, int(total_parts))
        self.phase_started = time.time()
        self.last_edit_time = 0.0
        self._generation += 1
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()

    def _task_by_html(self) -> str:
        name = html.escape(self.task_by)
        if self.user_id:
            return f'<a href="tg://user?id={int(self.user_id)}">{name}</a>'
        return name

    def _stats(self) -> str:
        global _disk_cache_ts, _disk_cache_free
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            now = time.time()
            if now - _disk_cache_ts > _DISK_CACHE_TTL:
                _disk_cache_free = shutil.disk_usage(os.getcwd()).free
                _disk_cache_ts = now
            free = _disk_cache_free
        except Exception:
            cpu, ram, free = 0.0, 0.0, 0
        uptime = format_time(time.time() - BOT_STARTED_AT)
        return (
            "⌬ <b>BOT STATS</b>\n"
            "▬▬▬▬▬✘▬▬▬▬▬\n"
            f"╭ <b>CPU »</b> {cpu:.1f}% | <b>FREE »</b> {format_bytes(free)}\n"
            f"├ <b>RAM »</b> {ram:.1f}% | <b>UP »</b> {uptime}\n"
            f"╰ <b>DL »</b> {html.escape(self.last_download_speed)} | "
            f"<b>UL »</b> {html.escape(self.last_upload_speed)}"
        )

    def _render(
        self,
        *,
        status: str,
        percent: float,
        done: str,
        total: str,
        speed: str,
        eta: str,
        engine: str,
        count_done: int,
    ) -> str:
        overall = format_time(time.time() - self.workflow_started)
        phase = format_time(time.time() - self.phase_started)
        if self.playlist_total > 0 and self.playlist_index > 0:
            count_line = f"┊ <b>Count »</b> <code>( {self.playlist_index} / {self.playlist_total} )</code>\n"
        else:
            count_line = f"┊ <b>Count »</b> <code>( {count_done} / {self.total_parts} )</code>\n"
        return (
            f"<b>{html.escape(self.title)}</b>\n\n"
            f"╭ <b>Task By {self._task_by_html()}</b>\n"
            f"┊ <code>{make_bar(percent)}</code> <i>{percent:.2f}%</i>\n"
            f"┊ <b>Status »</b> <code>{html.escape(status)}</code> » <code>{html.escape(speed)}</code>\n"
            f"┊ <b>Done »</b> <i>{html.escape(done)} / {html.escape(total)}</i>\n"
            f"{count_line}"
            f"┊ <b>ETA »</b> <i>⏳ {html.escape(eta)}</i>\n"
            f"┊ <b>Past »</b> <i>{overall} ({phase})</i>\n"
            f"┊ <b>Engine »</b> <i>{html.escape(engine)}</i>\n"
            "┊ <b>Mode »</b> <code>#YTDLP</code>"
        )

    def _queued_card(self, position: int = 0) -> str:
        if self.playlist_total > 0 and self.playlist_index > 0:
            count_line = f"┊ <b>Count »</b> <code>( {self.playlist_index} / {self.playlist_total} )</code>\n"
        else:
            count_line = ""
        return (
            f"<b>{html.escape(self.title)}</b>\n\n"
            f"╭ <b>Task By {self._task_by_html()}</b>\n"
            "┊ <code>❨⬡⬡⬡⬡⬡⬡⬡⬡⬡⬡⬡⬡❩</code> <i>0.00%</i>\n"
            f"┊ <b>Status »</b> <code>Queued</code> » <code>Global #{position or '-'}</code>\n"
            "┊ <b>Done »</b> <i>0 B / Unknown</i>\n"
            f"{count_line}"
            "┊ <b>ETA »</b> <i>⏳ N/A</i>\n"
            f"┊ <b>Engine »</b> <i>{html.escape(YTDLP_ENGINE)}</i>\n"
            "┊ <b>Mode »</b> <code>#YTDLP</code>"
        )

    async def _edit(self, text: str, generation: int) -> None:
        try:
            if self._closed or generation != self._generation:
                return
            if self.dashboard:
                await self.dashboard.update(self, text)
            else:
                wait_status = await rate_limiter.wait_if_needed()
                edit_text = text + "\n\n" + self._stats()
                if wait_status:
                    edit_text = f"{wait_status}\n\n{edit_text}"
                await self.message.edit_text(
                    edit_text,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                rate_limiter.record_send()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _can_queue_edit(self, force: bool = False) -> bool:
        now = time.time()
        if self._closed:
            return False
        if not force and now - self.last_edit_time < PROGRESS_EDIT_INTERVAL:
            return False
        return not (self._edit_task and not self._edit_task.done())

    def _queue_edit(self, text: str, force: bool = False) -> None:
        now = time.time()
        if not self._can_queue_edit(force):
            return
        self.last_edit_time = now
        self._edit_task = asyncio.create_task(self._edit(text, self._generation))

    async def show_stage(self, status: str, detail: str = "Working") -> None:
        self.phase_started = time.time()
        text = self._render(
            status=status,
            percent=0.0,
            done=detail,
            total=detail,
            speed="Local process",
            eta="N/A",
            engine="FFmpeg" if status in {"Splitting", "Merging"} else YTDLP_ENGINE,
            count_done=0,
        )
        if self.dashboard:
            await self.dashboard.update(self, text, force=True)
        else:
            await self._edit(text, self._generation)

    async def show_terminal(self, text: str) -> None:
        """Preserve a failure/cancel notice outside a disappearing dashboard."""
        if self.dashboard:
            self._latest_card = text
            await self.dashboard.update(self, text)
        else:
            try:
                wait_status = await rate_limiter.wait_if_needed()
                edit_text = text
                if wait_status:
                    edit_text = f"{wait_status}\n\n{edit_text}"
                await self.message.edit_text(edit_text, parse_mode=enums.ParseMode.HTML)
                rate_limiter.record_send()
            except Exception:
                pass

    async def update_download_progress(
        self,
        percent: float,
        total_size_str: str,
        speed_str: str,
        eta_str: str,
        *,
        downloaded_bytes: float | None = None,
        total_bytes: float | None = None,
        stream_key: str | None = None,
    ):
        bounded_percent = max(0.0, min(100.0, float(percent or 0)))
        component_total = max(0.0, float(total_bytes or 0)) or parse_size_text(total_size_str)
        component_done = (
            max(0.0, float(downloaded_bytes or 0))
            if downloaded_bytes is not None
            else component_total * bounded_percent / 100
        )
        if stream_key:
            previous = self._download_streams.get(stream_key, 0.0)
            self._download_streams[stream_key] = max(previous, component_done)
            aggregate_done = sum(self._download_streams.values())
        else:
            aggregate_done = component_done

        expected = float(self.expected_download_bytes or 0)
        display_total = expected or component_total
        if display_total > 0:
            aggregate_done = min(aggregate_done, display_total)
            display_percent = aggregate_done * 100 / display_total
        else:
            display_percent = bounded_percent

        numeric_speed = parse_size_text(speed_str)
        self.last_download_speed = (
            f"{format_bytes(numeric_speed)}/s" if numeric_speed else str(speed_str or "0 B/s")
        )
        if expected and numeric_speed > 0:
            eta_display = format_time(max(0.0, expected - aggregate_done) / numeric_speed)
        else:
            eta_display = str(eta_str or "N/A")

        force = bounded_percent >= 100
        if not self._can_queue_edit(force):
            return
        done = (
            format_bytes(aggregate_done)
            if display_total
            else f"{display_percent:.2f}%"
        )
        text = self._render(
            status="Download",
            percent=display_percent,
            done=done,
            total=format_bytes(display_total) if display_total else str(total_size_str or "Unknown"),
            speed=self.last_download_speed,
            eta=eta_display,
            engine=YTDLP_ENGINE,
            count_done=self.playlist_index if self.playlist_total > 0 else 0,
        )
        self._queue_edit(text, force=force)

    async def begin_processing_stage(self, status: str, engine: str) -> None:
        if self._processing_task and not self._processing_task.done():
            return
        self.phase_started = time.time()
        self.last_edit_time = 0.0
        self._generation += 1
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()

        async def ticker() -> None:
            try:
                while not self._closed:
                    total = float(self.expected_download_bytes or 0)
                    text = self._render(
                        status=status,
                        percent=100.0,
                        done=format_bytes(total) if total else "Downloaded",
                        total=format_bytes(total) if total else "Downloaded",
                        speed="Local remux" if status == "Merging" else "Local process",
                        eta="N/A",
                        engine=engine,
                        count_done=self.playlist_index if self.playlist_total > 0 else 0,
                    )
                    self._queue_edit(text, force=True)
                    await asyncio.sleep(PROGRESS_EDIT_INTERVAL)
            except asyncio.CancelledError:
                pass

        self._processing_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)

    async def end_processing_stage(self) -> None:
        task = self._processing_task
        self._processing_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def pyrogram_upload_progress(self, current: int, total: int):
        if queue_manager.is_cancelled(self.task_id):
            raise StopTransmission

        elapsed = max(0.1, time.time() - self.phase_started)
        speed_value = current / elapsed
        self.last_upload_speed = f"{format_bytes(speed_value)}/s"
        percent = (current / total) * 100 if total > 0 else 0
        eta = (total - current) / speed_value if speed_value > 0 else 0
        force = current >= total
        if not self._can_queue_edit(force):
            return
        text = self._render(
            status="Upload",
            percent=percent,
            done=format_bytes(current),
            total=format_bytes(total),
            speed=self.last_upload_speed,
            eta=format_time(eta),
            engine=TELEGRAM_ENGINE,
            count_done=self.playlist_index if self.playlist_total > 0 else (self.part_index if current >= total else self.part_index - 1),
        )
        self._queue_edit(text, force=force)

    async def close(self) -> None:
        self._closed = True
        await self.end_processing_stage()
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass
