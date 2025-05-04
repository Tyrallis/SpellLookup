#!/usr/bin/env python3
import os
import sys
import asyncio
import re
import html
import json
import requests
import urllib.parse
import tempfile
import subprocess
from datetime import datetime

# ↳ bump this when you cut a new release locally
__version__ = "1.0.2d"
# ↳ raw URLs for version check and script update
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/Tyrallis/SpellLookup/main/VERSION.txt"
RAW_SCRIPT_URL     = "https://raw.githubusercontent.com/Tyrallis/SpellLookup/main/spell_lookup.py"

try:
    import aiohttp
except ImportError:
    print("Error: aiohttp module not found. Please install with `pip install aiohttp requests`.", file=sys.stderr)
    sys.exit(1)

# ANSI True-Color helper
def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"

CYAN, GREEN, YELLOW, RED, RESET = "\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"

CLASS_COLORS = {
    "Death Knight": rgb(196,30,58), "Demon Hunter": rgb(163,48,201), "Druid": rgb(255,124,10),
    "Evoker": rgb(51,147,127), "Hunter": rgb(170,211,114), "Mage": rgb(63,199,235),
    "Monk": rgb(0,255,152), "Paladin": rgb(244,140,186), "Priest": rgb(255,255,255),
    "Rogue": rgb(255,244,104), "Shaman": rgb(0,112,221), "Warlock": rgb(135,136,238),
    "Warrior": rgb(198,155,109),
}

CLASS_ID_MAP = {
    "1":"Warrior","2":"Paladin","3":"Hunter","4":"Rogue","5":"Priest",
    "6":"Death Knight","7":"Shaman","8":"Mage","9":"Warlock","10":"Monk",
    "11":"Druid","12":"Demon Hunter","13":"Evoker",
}

DB2_BASE, FILES_BASE = "https://wago.tools/db2", "https://wago.tools/files"
SEM, TOTAL_STAGES = asyncio.Semaphore(16), 6

# Detect if running as PyInstaller-frozen executable
is_frozen = getattr(sys, 'frozen', False)
current_path = sys.executable if is_frozen else os.path.realpath(__file__)

# --- Updater Functionality ---
def fetch_remote_version():
    try:
        r = requests.get(REMOTE_VERSION_URL, timeout=2, headers={"Cache-Control":"no-cache"})
        r.raise_for_status()
        return r.text.strip()
    except:
        return None


def prompt_update():
    latest = fetch_remote_version()
    if not latest or latest == __version__:
        return

    print(f"{RED}Update available!{RESET} You have {__version__}, latest is {latest}.")
    choice = input("Download and install now? [Y/n] ").strip().lower()
    if choice not in ("", "y", "yes"):
        print("Continuing with current version...\n")
        return

    # Download into temp file
    suffix = ".exe" if is_frozen else ".py"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        r2 = requests.get(RAW_SCRIPT_URL, timeout=10, headers={"Cache-Control":"no-cache"})
        r2.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(r2.content)
    except Exception as e:
        print(f"{RED}Failed to download update:{RESET} {e}")
        os.remove(tmp_path)
        return

    if is_frozen:
        # Create batch script to replace the exe after exit
        batch = f"""
@echo off
ping 127.0.0.1 -n 2 >nul
move /Y \"{tmp_path}\" \"{current_path}\"
start "" \"{current_path}\"
"""
        bat_path = os.path.join(tempfile.gettempdir(), 'updater.bat')
        with open(bat_path, 'w') as bat:
            bat.write(batch)
        subprocess.Popen(["cmd", "/c", bat_path], shell=False)
        print(f"{GREEN}Updater launched. Exiting current instance...{RESET}")
        sys.exit(0)
    else:
        # Overwrite script and restart
        try:
            os.replace(tmp_path, current_path)
            print(f"{GREEN}Updated script to {latest}! Restarting...{RESET}")
            os.execv(sys.executable, [sys.executable, current_path] + sys.argv[1:])
        except Exception as e:
            print(f"{RED}Error installing update:{RESET} {e}")
        return

# --- Core CLI Functionality ---
def clear_screen():
    os.system('cls' if os.name=='nt' else 'clear')


def print_banner():
    clear_screen()
    width = 60
    border = CYAN + "+" + "-"*(width-2) + "+" + RESET
    print(border)
    print(CYAN + "|" + RESET + f"Made by Tyrallis (v{__version__})".center(width-2) + CYAN + "|" + RESET)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(CYAN + "|" + RESET + ts.center(width-2) + CYAN + "|" + RESET)
    latest = fetch_remote_version()
    upd = f"Update available! {__version__} → {latest}" if latest and latest != __version__ else ""
    print(CYAN + "|" + RESET + upd.center(width-2) + CYAN + "|" + RESET)
    print(border + "\n")


def print_progress(stage, label):
    w = 30
    pct = stage / TOTAL_STAGES
    filled = int(w * pct)
    bar = GREEN + "█"*filled + RESET + "░"*(w-filled)
    sys.stdout.write(f"\r{CYAN}{label.ljust(20)}{RESET} {bar} {pct*100:5.1f}%\033[K")
    sys.stdout.flush()
    if stage == TOTAL_STAGES:
        print()

async def fetch_db2(session, table, filters):
    qs = urllib.parse.urlencode(filters)
    async with SEM:
        async with session.get(f"{DB2_BASE}/{table}?{qs}") as resp:
            resp.raise_for_status()
            text = await resp.text()
    m = re.search(r'<div[^>]+data-page=(?:["\'])(.*?)(?:["\'])', text, re.DOTALL)
    if not m:
        return []
    data = json.loads(html.unescape(m.group(1)))
    return data.get("props", {}).get("data", {}).get("data", []) or []

async def fetch_spell_ids(name_or_id):
    async with aiohttp.ClientSession() as s:
        if name_or_id.isdigit():
            return [int(name_or_id)]
        rows = await fetch_db2(s, "SpellName", {"filter[Name_lang]": f"exact:{name_or_id}", "page":"1"})
        return [r["ID"] for r in rows if "ID" in r]

async def get_spell_details(session, sid):
    rows = await fetch_db2(session, "SpellName", {"filter[ID]": f"exact:{sid}", "page":"1"})
    name = str(sid)
    if rows:
        for k,v in rows[0].items():
            if "name" in k.lower(): name = v; break
    r = requests.get(f"https://www.wowhead.com/spell={sid}", headers={"Cache-Control":"no-cache"})
    m = re.search(r"\[class=(\d+)\]", r.text)
    cls = CLASS_ID_MAP.get(m.group(1), "Unknown") if m else "Unknown"
    color = CLASS_COLORS.get(cls, "")
    return {"id": sid, "name": name, "class": cls, "color": color}

async def run_cli(query):
    async with aiohttp.ClientSession() as s:
        ids = await fetch_spell_ids(query)
        if not ids:
            print(f"{YELLOW}No spells matching '{query}'{RESET}")
            return
        for i, sid in enumerate(ids, 1):
            print(f"{CYAN}Result {i}/{len(ids)}:{RESET}")
            det = await get_spell_details(s, sid)
            print(f"- ID: {sid}, Name: {det['name']}, Class: {det['color']}{det['class']}{RESET}")
            slug = re.sub(r"[^a-z0-9 ]", "", det['name'].lower())
            slug = re.sub(r"\s+", "-", slug.strip())
            url = f"https://www.wowhead.com/spell={sid}/{slug}"
            link = f"\033]8;;{url}\033\\{det['name']}\033]8;;\033\\"
            print(f"{YELLOW}Link:{RESET} {link}\n")

# --- Interactive Loop ---
def interactive_loop():
    while True:
        print_banner()
        prompt_update()
        inp = input("Enter a SpellID or exact spell name: ").strip()
        if not inp:
            continue
        asyncio.run(run_cli(inp))
        input("Press Enter for new search")

if __name__ == "__main__":
    interactive_loop()
