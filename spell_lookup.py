#!/usr/bin/env python3
import os
import sys
import asyncio
import re
import html
import json
import requests
import urllib.parse
from datetime import datetime

# ↳ bump this when you cut a new release
__version__ = "1.0.1"

# ↳ point this at a raw text file containing only your latest version string
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/YourUser/YourRepo/main/VERSION.txt"

try:
    import aiohttp
except ImportError:
    print("Error: aiohttp module not found. Please install with `pip install aiohttp requests aiohttp`.", file=sys.stderr)
    sys.exit(1)

# ANSI true-color helper
def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"

# Basic ANSI
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"

# Class colors
CLASS_COLORS = {
    "Death Knight": rgb(196,  30,  58),
    "Demon Hunter": rgb(163,  48, 201),
    "Druid":        rgb(255, 124,  10),
    "Evoker":       rgb( 51, 147, 127),
    "Hunter":       rgb(170, 211, 114),
    "Mage":         rgb( 63, 199, 235),
    "Monk":         rgb(  0, 255, 152),
    "Paladin":      rgb(244, 140, 186),
    "Priest":       rgb(255, 255, 255),
    "Rogue":        rgb(255, 244, 104),
    "Shaman":       rgb(  0, 112, 221),
    "Warlock":      rgb(135, 136, 238),
    "Warrior":      rgb(198, 155, 109),
}

# Wowhead class ID → class name
CLASS_ID_MAP = {
    "1":"Warrior","2":"Paladin","3":"Hunter","4":"Rogue","5":"Priest",
    "6":"Death Knight","7":"Shaman","8":"Mage","9":"Warlock","10":"Monk",
    "11":"Druid","12":"Demon Hunter","13":"Evoker",
}

DB2_BASE     = "https://wago.tools/db2"
FILES_BASE   = "https://wago.tools/files"
SEM          = asyncio.Semaphore(16)
TOTAL_STAGES = 6

def check_for_updates():
    try:
        resp = requests.get(REMOTE_VERSION_URL, timeout=2)
        resp.raise_for_status()
        latest = resp.text.strip()
    except Exception:
        return
    if latest != __version__:
        print(f"{RED}Update available!{RESET} You have {__version__}, latest is {latest}.")
        print(f"→ Download from your repo or run your update command.\n")

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_banner():
    clear_screen()
    width = 60
    border = CYAN + "+" + "-"*(width-2) + "+" + RESET
    content = f"Made by Tyrallis (v{__version__})"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line1 = CYAN + "|" + RESET + content.center(width-2) + CYAN + "|" + RESET
    line2 = CYAN + "|" + RESET + timestamp.center(width-2) + CYAN + "|" + RESET
    print(border)
    print(line1)
    print(line2)
    print(border)
    print()

def print_progress(stage: int, label: str):
    bar_width = 30
    pct = stage / TOTAL_STAGES
    filled = int(bar_width * pct)
    empty  = bar_width - filled
    bar = GREEN + "█"*filled + RESET + "░"*empty
    sys.stdout.write(f"\r{CYAN}{label.ljust(20)}{RESET} {bar} {pct*100:5.1f}%\033[K")
    sys.stdout.flush()
    if stage == TOTAL_STAGES:
        sys.stdout.write("\n")

async def fetch_db2(session: aiohttp.ClientSession, table: str, filters: dict):
    qs = urllib.parse.urlencode(filters)
    url = f"{DB2_BASE}/{table}?{qs}"
    async with SEM:
        async with session.get(url) as resp:
            resp.raise_for_status()
            text = await resp.text()
    m = re.search(r'<div[^>]+data-page=(?:["\'])(.*?)(?:["\'])', text, re.DOTALL)
    if not m:
        return []
    blob = html.unescape(m.group(1))
    page = json.loads(blob)
    return page.get("props", {}).get("data", {}).get("data", []) or []

async def fetch_spell_ids_by_name(session: aiohttp.ClientSession, name: str):
    rows = await fetch_db2(session, "SpellName", {
        "filter[Name_lang]": f"exact:{name}",
        "page": "1"
    })
    return [r["ID"] for r in rows if "ID" in r]

async def fetch_files(session: aiohttp.ClientSession, search_id: int):
    url = f"{FILES_BASE}?search={search_id}"
    async with SEM:
        async with session.get(url) as resp:
            resp.raise_for_status()
            text = await resp.text()
    m = re.search(r'<div[^>]+data-page=(?:["\'])(.*?)(?:["\'])', text, re.DOTALL)
    if not m:
        return []
    blob = html.unescape(m.group(1))
    page = json.loads(blob)
    file_list = page.get("props", {}).get("files", {}).get("data", []) or []
    return [e.get("filename") for e in file_list if str(e.get("fdid"))==str(search_id)]

def fetch_spell_class_wowhead(spell_id: int) -> str:
    url = f"https://www.wowhead.com/spell={spell_id}"
    resp = requests.get(url); resp.raise_for_status()
    m = re.search(r"\[class=(\d+)\]", resp.text)
    return CLASS_ID_MAP.get(m.group(1), "Unknown") if m else "Unknown"

def make_slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9 ]","",s)
    return re.sub(r"\s+","-",s.strip())

def print_wowhead_link(spell_id: int, spell_name: str):
    slug = make_slug(spell_name)
    url = f"https://www.wowhead.com/spell={spell_id}/{slug}"
    link = f"\033]8;;{url}\033\\{spell_name}\033]8;;\033\\"
    print(f"\n{YELLOW}Wowhead:{RESET} {link}")

async def process_spell(session: aiohttp.ClientSession, spell_id: int):
    rows = await fetch_db2(session, "SpellName", {"filter[ID]":f"exact:{spell_id}","page":"1"})
    spell_name = str(spell_id)
    if rows:
        for k,v in rows[0].items():
            if "name" in k.lower():
                spell_name = v; break

    spell_class   = fetch_spell_class_wowhead(spell_id)
    color         = CLASS_COLORS.get(spell_class,"")
    colored_class = f"{color}{spell_class}{RESET}"

    print_progress(0, spell_name)

    visuals    = await fetch_db2(session, "SpellXSpellVisual", {"filter[SpellID]":f"exact:{spell_id}"})
    visual_ids = {r.get("SpellVisualID") for r in visuals}
    print_progress(1, "SpellVisualIDs")

    rows2 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEvent", {"filter[SpellVisualID]":f"exact:{vid}"})
        for vid in visual_ids
    ])
    kit_ids = {r.get("SpellVisualKitID") for sub in rows2 for r in sub}
    print_progress(2, "SpellVisualKitIDs")

    rows3 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualKitEffect", {
            "filter[ParentSpellVisualKitID]":f"exact:{kid}","filter[EffectType]":"exact:2"
        }) for kid in kit_ids
    ])
    attach_ids = {r.get("Effect") for sub in rows3 for r in sub}
    print_progress(3, "ModelAttachIDs")

    rows4 = await asyncio.gather(*[
        fetch_db2(session, "spellvisualkitmodelattach", {"filter[ID]":f"exact:{aid}"})
        for aid in attach_ids
    ])
    name_ids = {r.get("SpellVisualEffectNameID") for sub in rows4 for r in sub}
    print_progress(4, "EffectNameIDs")

    rows5 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEffectName", {"filter[ID]":f"exact:{nid}"})
        for nid in name_ids
    ])
    data_ids = {r.get("ModelFileDataID") for sub in rows5 for r in sub}
    print_progress(5, "ModelFileDataIDs")

    filenames=[]
    for did in data_ids:
        filenames.extend(await fetch_files(session,did))
    filenames=sorted(set(filenames))
    if not filenames: return

    print_progress(6,"Filenames")
    print(f"\n{YELLOW}Found files for {spell_name} [{colored_class}]{RESET}")
    for f in filenames: print(f"{GREEN}- {f}{RESET}")
    print_wowhead_link(spell_id, spell_name)
    print()

async def search_and_process(name_or_id: str):
    async with aiohttp.ClientSession() as session:
        if name_or_id.isdigit():
            await process_spell(session,int(name_or_id))
        else:
            ids=await fetch_spell_ids_by_name(session,name_or_id)
            if not ids:
                print(f"{YELLOW}No spells found matching \"{name_or_id}\".{RESET}")
                return
            print(f"{YELLOW}Found {len(ids)} spells matching \"{name_or_id}\":{RESET}\n")
            for sid in ids: await process_spell(session,sid)

def interactive_loop():
    check_for_updates()
    while True:
        print_banner()
        inp=input("Enter a SpellID or exact spell name: ").strip()
        if not inp: continue
        asyncio.run(search_and_process(inp))
        input("Press Enter for new search")

if __name__=="__main__":
    interactive_loop()
