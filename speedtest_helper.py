# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import asyncio
import html
import importlib
import time
from io import BytesIO
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Callable


class SpeedtestError(RuntimeError):
    """Base error for a speed test that could not produce a result."""


class SpeedtestDependencyError(SpeedtestError):
    """Raised when the optional speedtest-cli package is unavailable."""


class SpeedtestBusyError(SpeedtestError):
    """Raised when another speed test is already running."""


class SpeedtestCooldownError(SpeedtestError):
    """Raised when a speed test was requested before the cooldown elapsed."""

    def __init__(self, retry_after: float):
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(f"Try again in {self.retry_after:.0f} seconds.")


@dataclass(frozen=True)
class SpeedtestReport:
    """Display-ready speed test output and optional Ookla share image URL."""

    text: str
    photo_url: str | None


def _format_bytes(size: object) -> str:
    try:
        value = max(0.0, float(size or 0))
    except (TypeError, ValueError):
        value = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return "0.00 B"


def _format_speed(bits_per_second: object) -> str:
    """Convert speedtest bits/second to a readable bytes/second value."""
    try:
        value = max(0.0, float(bits_per_second or 0)) / 8
    except (TypeError, ValueError):
        value = 0.0
    for unit in ("B/s", "KB/s", "MB/s", "GB/s", "TB/s"):
        if value < 1024.0 or unit == "TB/s":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return "0.00 B/s"


def _safe_field(value: object, fallback: str = "N/A") -> str:
    rendered = str(value if value not in (None, "") else fallback)
    return html.escape(rendered, quote=True)


def _safe_photo_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower().startswith(("https://", "http://")):
        return value
    return None


def _build_report(result: dict, share_url: object = None) -> SpeedtestReport:
    """Render the VideoCover-style speedtest report using escaped API fields."""
    server = result.get("server") if isinstance(result.get("server"), dict) else {}
    client = result.get("client") if isinstance(result.get("client"), dict) else {}

    try:
        ping_ms = max(0.0, float(result.get("ping") or 0))
    except (TypeError, ValueError):
        ping_ms = 0.0

    country = _safe_field(server.get("country"))
    country_code = _safe_field(server.get("cc"), "")
    country_label = f"{country}, {country_code}" if country_code else country
    text = (
        "╭─《 🚀 <b>SPEEDTEST INFO</b> 》\n"
        f"├ <b>Upload:</b> <code>{_format_speed(result.get('upload'))}</code>\n"
        f"├ <b>Download:</b> <code>{_format_speed(result.get('download'))}</code>\n"
        f"├ <b>Ping:</b> <code>{ping_ms:.3f} ms</code>\n"
        f"├ <b>Time:</b> <code>{_safe_field(result.get('timestamp'))}</code>\n"
        f"├ <b>Data Sent:</b> <code>{_format_bytes(result.get('bytes_sent'))}</code>\n"
        f"╰ <b>Data Received:</b> <code>{_format_bytes(result.get('bytes_received'))}</code>\n\n"
        "╭─《 🌐 <b>SPEEDTEST SERVER</b> 》\n"
        f"├ <b>Name:</b> <code>{_safe_field(server.get('name'))}</code>\n"
        f"├ <b>Country:</b> <code>{country_label}</code>\n"
        f"├ <b>Sponsor:</b> <code>{_safe_field(server.get('sponsor'))}</code>\n"
        f"├ <b>Latency:</b> <code>{_safe_field(server.get('latency'))} ms</code>\n"
        f"├ <b>Latitude:</b> <code>{_safe_field(server.get('lat'))}</code>\n"
        f"╰ <b>Longitude:</b> <code>{_safe_field(server.get('lon'))}</code>\n\n"
        "╭─《 👤 <b>CLIENT DETAILS</b> 》\n"
        f"├ <b>IP Address:</b> <code>{_safe_field(client.get('ip'))}</code>\n"
        f"├ <b>Latitude:</b> <code>{_safe_field(client.get('lat'))}</code>\n"
        f"├ <b>Longitude:</b> <code>{_safe_field(client.get('lon'))}</code>\n"
        f"├ <b>Country:</b> <code>{_safe_field(client.get('country'))}</code>\n"
        f"├ <b>ISP:</b> <code>{_safe_field(client.get('isp'))}</code>\n"
        f"├ <b>ISP Rating:</b> <code>{_safe_field(client.get('isprating'))}</code>\n"
        "╰ <b>Powered by Nexon Bots</b>"
    )
    return SpeedtestReport(
        text=text,
        photo_url=_safe_photo_url(share_url) or _safe_photo_url(result.get("share")),
    )


def _download_speedtest_image_sync(image_url: str) -> BytesIO:
    """Download an Ookla share image so Telegram receives an uploaded file."""
    request = Request(
        image_url,
        headers={"User-Agent": "Mozilla/5.0 NexTubeBot/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        image_data = response.read(10 * 1024 * 1024 + 1)
    if not image_data:
        raise SpeedtestError("Speed test image response was empty.")
    if len(image_data) > 10 * 1024 * 1024:
        raise SpeedtestError("Speed test image exceeded 10 MB.")
    image = BytesIO(image_data)
    image.name = "speedtest.png"
    image.seek(0)
    return image


async def download_speedtest_image(image_url: str) -> BytesIO:
    return await asyncio.to_thread(_download_speedtest_image_sync, image_url)


def _run_speedtest_sync() -> SpeedtestReport:
    """Run speedtest-cli synchronously. Call this only through asyncio.to_thread."""
    try:
        speedtest_module = importlib.import_module("speedtest")
    except (ImportError, ModuleNotFoundError) as exc:
        raise SpeedtestDependencyError(
            "speedtest-cli is not installed in the bot environment."
        ) from exc

    try:
        tester = speedtest_module.Speedtest(timeout=10, secure=True)
        tester.get_best_server()
        tester.download()
        # Avoid speedtest-cli's upload buffer pre-allocation on low-memory hosts.
        tester.upload(pre_allocate=False)

        share_url = None
        try:
            share_url = tester.results.share()
        except Exception:
            # A share image is optional; the formatted text remains useful.
            pass
        result = tester.results.dict()
        if not isinstance(result, dict):
            raise TypeError("speedtest-cli returned an invalid result")
        return _build_report(result, share_url)
    except SpeedtestError:
        raise
    except Exception as exc:
        raise SpeedtestError(str(exc) or "Speed test failed.") from exc


class SpeedtestService:
    """Serialize expensive tests and enforce a monotonic start cooldown."""

    def __init__(
        self,
        cooldown_seconds: float = 600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_started_at: float | None = None

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def cooldown_remaining(self) -> float:
        if self._last_started_at is None:
            return 0.0
        elapsed = self._clock() - self._last_started_at
        return max(0.0, self.cooldown_seconds - elapsed)

    async def run(self) -> SpeedtestReport:
        # There is no await between these checks and lock acquisition, so two
        # event-loop tasks cannot both pass this gate.
        if self._lock.locked():
            raise SpeedtestBusyError("Another speed test is already running.")
        retry_after = self.cooldown_remaining()
        if retry_after > 0:
            raise SpeedtestCooldownError(retry_after)

        await self._lock.acquire()
        try:
            report = await asyncio.to_thread(_run_speedtest_sync)
            self._last_started_at = self._clock()
            return report
        finally:
            self._lock.release()


speedtest_service = SpeedtestService()
