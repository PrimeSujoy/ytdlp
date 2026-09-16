# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot
import os
import re
import glob
import logging
import asyncio
from config import Config

logger = logging.getLogger("CookieManager")


def sanitize_cookie_file(file_path: str) -> bool:
    """Strips #HttpOnly_ prefixes from Cookie-Editor exports so yt-dlp reads login session."""
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        cleaned_lines = []
        has_header = False
        for line in lines:
            if "# Netscape HTTP Cookie File" in line:
                has_header = True
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            cleaned_lines.append(line)

        if not has_header:
            cleaned_lines.insert(0, "# Netscape HTTP Cookie File\n")

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)
        return True
    except Exception as e:
        logger.error(f"Error sanitizing cookie file {file_path}: {e}")
        return False


class CookieManager:
    def __init__(self, cookies_dir: str):
        self.cookies_dir = cookies_dir
        os.makedirs(self.cookies_dir, exist_ok=True)
        self._cookie_files: list[str] = []
        self._current_index = 0
        self._health_status: dict[str, str] = {}
        self._resolver_cookie: str | None = None
        self.refresh_pool()

    def refresh_pool(self):
        """Scans the cookies directory and auto-sanitizes all found files."""
        pattern = os.path.join(self.cookies_dir, "*.txt")
        self._cookie_files = sorted(glob.glob(pattern))
        for path in self._cookie_files:
            sanitize_cookie_file(path)
            fname = os.path.basename(path)
            if fname not in self._health_status:
                self._health_status[fname] = "valid"
        # Clear resolver if file no longer exists
        if self._resolver_cookie:
            rpath = os.path.join(self.cookies_dir, self._resolver_cookie)
            if not os.path.exists(rpath):
                self._resolver_cookie = None
        logger.info(f"Loaded {len(self._cookie_files)} cookie files in pool")

    def get_all_cookies(self) -> list[dict]:
        """Returns list of all cookies with their basename and health status."""
        result = []
        for path in self._cookie_files:
            fname = os.path.basename(path)
            status = self._health_status.get(fname, "valid")
            result.append({
                "filename": fname,
                "path": path,
                "status": status,
            })
        return result

    def get_active_cookie(self) -> str | None:
        """Returns the current active cookie file path or None if pool is empty."""
        if not self._cookie_files:
            self.refresh_pool()
        if not self._cookie_files:
            return None
        return self._cookie_files[self._current_index % len(self._cookie_files)]

    def get_cookie_candidates(self, include_anonymous: bool = True) -> list[str | None]:
        """Returns every download cookie once (excluding resolver), starting with the currently active cookie."""
        download_pool = self.get_download_pool()
        if not download_pool:
            self.refresh_pool()
            download_pool = self.get_download_pool()
        candidates: list[str | None] = []
        if download_pool:
            start = self._current_index % len(download_pool)
            candidates.extend(download_pool[start:] + download_pool[:start])
        if include_anonymous or not candidates:
            candidates.append(None)
        return candidates

    def set_resolver_cookie(self, filename: str | None) -> bool:
        """Set or clear the resolver cookie. Resolver cookie is used only for
        metadata/format extraction, never for actual downloads."""
        if filename is None:
            self._resolver_cookie = None
            logger.info("Resolver cookie cleared")
            return True
        path = os.path.join(self.cookies_dir, filename)
        if not os.path.exists(path):
            return False
        self._resolver_cookie = filename
        logger.info(f"Resolver cookie set to: {filename}")
        return True

    def get_resolver_cookie(self) -> str | None:
        """Returns the resolver cookie filename or None."""
        if self._resolver_cookie:
            path = os.path.join(self.cookies_dir, self._resolver_cookie)
            if os.path.exists(path):
                return self._resolver_cookie
            self._resolver_cookie = None
        return None

    def get_resolver_cookie_path(self) -> str | None:
        """Returns the full path to the resolver cookie file or None."""
        filename = self.get_resolver_cookie()
        if filename:
            return os.path.join(self.cookies_dir, filename)
        return None

    def get_download_pool(self) -> list[str]:
        """Returns cookie files for downloading, excluding the resolver cookie."""
        pool = [f for f in self._cookie_files
                if os.path.basename(f) != self._resolver_cookie]
        return pool

    def claim_cookie(self) -> str | None:
        """Atomically claim the current cookie and advance to the next one.
        Used for round-robin distribution across concurrent tasks.
        Resolver cookie is NEVER claimed for downloads."""
        download_pool = self.get_download_pool()
        if not download_pool:
            self.refresh_pool()
            download_pool = self.get_download_pool()
        if not download_pool:
            return None
        cookie = download_pool[self._current_index % len(download_pool)]
        self._current_index = (self._current_index + 1) % len(download_pool)
        logger.info(f"Claimed cookie: {os.path.basename(cookie)} (next index: {self._current_index})")
        return cookie

    def rotate_cookie(self) -> str | None:
        """Switches to the next cookie in the download pool upon rate limit/failure."""
        download_pool = self.get_download_pool()
        if not download_pool:
            return None
        self._current_index = (self._current_index + 1) % len(download_pool)
        idx = self._current_index % len(download_pool)
        active = download_pool[idx]
        logger.info(f"Rotated cookie to {os.path.basename(active)}")
        return active

    async def validate_cookie(self, filename: str) -> str:
        """Runs a validation probe on the cookie against YouTube."""
        path = os.path.join(self.cookies_dir, filename)
        if not os.path.exists(path):
            return "not_found"

        sanitize_cookie_file(path)
        cmd = [
            "python", "-m", "yt_dlp",
            "--cookies", path,
            "--skip-download",
            "--no-warnings",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
            if proc.returncode == 0:
                status = "valid"
            else:
                err_text = stderr.decode(errors="ignore") if stderr else ""
                if "Sign in" in err_text or "blocked" in err_text:
                    status = "expired"
                else:
                    status = "degraded"
        except asyncio.TimeoutError:
            logger.warning(f"Probe timed out for {filename}")
            status = "degraded"
        except Exception as e:
            logger.warning(f"Probe exception for {filename}: {e}")
            status = "degraded"

        self._health_status[filename] = status
        return status

    def add_cookie_file(self, content_bytes: bytes) -> tuple[bool, str]:
        """Saves a new cookie file sequentially (e.g. cookies1.txt, cookies2.txt)."""
        idx = 1
        while True:
            candidate = f"cookies{idx}.txt"
            path = os.path.join(self.cookies_dir, candidate)
            if not os.path.exists(path):
                break
            idx += 1

        try:
            with open(path, "wb") as f:
                f.write(content_bytes)
            sanitize_cookie_file(path)
            self.refresh_pool()
            self._health_status[candidate] = "valid"
            # Save to MongoDB for cross-device persistence
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    from database import db
                    loop.create_task(db.save_cookie(candidate, content_bytes))
                else:
                    pass  # Will be saved on next startup
            except Exception:
                pass
            return True, candidate
        except Exception as e:
            logger.error(f"Failed to add cookie file {candidate}: {e}")
            return False, str(e)

    def delete_cookie_file(self, filename: str) -> tuple[bool, str]:
        """Deletes a cookie file from disk and memory."""
        path = os.path.join(self.cookies_dir, filename)
        if not os.path.exists(path):
            return False, "File does not exist."
        try:
            os.remove(path)
            if filename in self._health_status:
                del self._health_status[filename]
            # Clear resolver if deleted cookie was the resolver
            if self._resolver_cookie == filename:
                self._resolver_cookie = None
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        from database import db
                        loop.create_task(db.set_resolver_cookie(None))
                except Exception:
                    pass
            self.refresh_pool()
            # Delete from MongoDB
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    from database import db
                    loop.create_task(db.delete_cookie_from_db(filename))
            except Exception:
                pass
            return True, f"Cookie {filename} deleted successfully."
        except Exception as e:
            logger.error(f"Failed to delete {filename}: {e}")
            return False, str(e)


# Global cookie manager
cookie_manager = CookieManager(Config.COOKIES_DIR)
