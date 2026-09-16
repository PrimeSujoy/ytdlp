"""Process lifecycle utilities. Never reset or discard local Git changes."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import psutil


async def git_command(repo, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=repo, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 120)
    except BaseException:
        proc.kill()
        await proc.communicate()
        raise
    if proc.returncode:
        # Git stderr may contain remote credentials. Keep it out of Telegram.
        raise RuntimeError("Git command failed; check repository access/upstream on the server.")
    return stdout.decode(errors="replace").strip()


async def check_update(repo):
    if not (Path(repo) / ".git").exists():
        raise RuntimeError("This is a ZIP source folder, not a Git checkout. /update needs a cloned repository with an upstream branch.")
    if await git_command(repo, "status", "--porcelain"):
        raise RuntimeError("Local changes/untracked files exist. Commit or move them before /update; nothing was overwritten.")
    await git_command(repo, "rev-parse", "--abbrev-ref", "@{upstream}")


def stop_children():
    children = psutil.Process().children(recursive=True)
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(children, timeout=5)


def replace_bot(repo):
    args = [sys.executable, "-u", str(Path(repo) / "main.py")]
    if os.name != "nt":
        os.execv(sys.executable, args)
    # Hidden Windows child with durable logs, using the same Python/PATH/env.
    with open(Path(repo) / "bot-restart.out.log", "ab") as out, open(Path(repo) / "bot-restart.err.log", "ab") as err:
        subprocess.Popen(args, cwd=repo, env=os.environ.copy(), stdout=out,
                         stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    os._exit(0)
