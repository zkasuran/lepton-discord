"""Force-sync the bot's slash commands to a guild (instant) or globally (slow).

Discord global command changes can take up to an hour to appear and are easy to
leave stale (a partial set that never updates). Guild-scoped commands update
instantly. This pushes the current command tree (every command defined in
src/bot/client.py) straight to Discord over the HTTP API, without running the
gateway, so a missing or stale command is fixed in seconds.

Usage:
  .venv/bin/python scripts/sync_commands.py <guild_id>   # instant, one guild
  .venv/bin/python scripts/sync_commands.py --global      # all guilds, up to ~1h
  GUILD_ID=... .venv/bin/python scripts/sync_commands.py   # guild id from env
"""

from __future__ import annotations

import os
import sys

import httpx

from src.bot.client import bot
from src.payments.config import DISCORD_APP_ID, DISCORD_BOT_TOKEN

API = "https://discord.com/api/v10"


def _payloads() -> list[dict]:
    """Command payloads generated from the live tree, so they match the code."""
    tree = bot.tree
    return [cmd.to_dict(tree) for cmd in tree.get_commands()]


def main() -> None:
    if not DISCORD_BOT_TOKEN or not DISCORD_APP_ID:
        raise SystemExit("DISCORD_BOT_TOKEN and DISCORD_APP_ID must be set in .env.")

    args = sys.argv[1:]
    payloads = _payloads()
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

    if args and args[0] == "--global":
        url = f"{API}/applications/{DISCORD_APP_ID}/commands"
        scope = "global (can take up to ~1h to appear in clients)"
    else:
        gid = args[0] if args else os.getenv("GUILD_ID", "")
        if not gid:
            raise SystemExit("Provide a guild id, or --global, or set GUILD_ID.")
        url = f"{API}/applications/{DISCORD_APP_ID}/guilds/{gid}/commands"
        scope = f"guild {gid} (instant)"

    print(f"Pushing {len(payloads)} commands: {sorted(c['name'] for c in payloads)}")
    resp = httpx.put(url, headers=headers, json=payloads, timeout=30)
    if resp.status_code >= 400:
        raise SystemExit(f"Discord rejected the sync ({resp.status_code}): {resp.text[:500]}")
    names = sorted(c["name"] for c in resp.json())
    print(f"Synced {len(names)} commands to {scope}:")
    print("  " + ", ".join(names))


if __name__ == "__main__":
    main()
