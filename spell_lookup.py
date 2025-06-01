#!/usr/bin/env python3
import os
import sys
import asyncio
import re
import html
import json
import requests
import urllib.parse
import time
from datetime import datetime

# ↳ bump this when you cut a new release
__version__ = "1.1"
# ↳ raw URLs for version check and script update
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/Tyrallis/SpellLookup/main/VERSION.txt"
RAW_SCRIPT_URL     = "https://raw.githubusercontent.com/Tyrallis/SpellLookup/main/spell_lookup.py"

# The build string to use specifically for SpellChainEffect lookups
CHAIN_EFFECT_BUILD = "10.2.7.54717"

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
SEM          = None  # will be set per-search
TOTAL_STAGES = 7     # 0..6 for model, 7 for chain textures

# Shared state for multiple‐bar rendering
multi_labels = []         # list of spell_name per index
multi_progress = []       # list of current stage (0..7) per index
multi_done_event = None   # asyncio.Event to signal completion
BAR_WIDTH = 30

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_banner():
    clear_screen()
    width = 60
    border = CYAN + "+" + "-"*(width-2) + "+" + RESET

    # line 1: byline + version
    content = f"Made by Tyrallis (v{__version__})"
    line1 = CYAN + "|" + RESET + content.center(width-2) + CYAN + "|" + RESET

    # line 2: timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line2 = CYAN + "|" + RESET + timestamp.center(width-2) + CYAN + "|" + RESET

    # line 3: update notice
    try:
        resp = requests.get(REMOTE_VERSION_URL, timeout=1)
        resp.raise_for_status()
        latest = resp.text.strip()
        upd = f"Update available! {__version__} → {latest}" if latest != __version__ else ""
    except Exception:
        upd = ""
    line3 = CYAN + "|" + RESET + upd.center(width-2) + CYAN + "|" + RESET

    print(border)
    print(line1)
    print(line2)
    print(line3)
    print(border)
    print()

def prompt_update():
    """
    If a newer version exists, ask the user to download & install automatically.
    """
    try:
        resp = requests.get(REMOTE_VERSION_URL, timeout=2)
        resp.raise_for_status()
        latest = resp.text.strip()
    except Exception:
        return

    if latest == __version__:
        return

    print(f"{RED}Update available!{RESET} You have {__version__}, latest is {latest}.")
    choice = input("Download and install now? [Y/n] ").strip().lower()
    if choice in ("", "y", "yes"):
        try:
            r2 = requests.get(RAW_SCRIPT_URL, timeout=5)
            r2.raise_for_status()
        except Exception as e:
            print(f"{RED}Failed to download update:{RESET} {e}")
            return

        script_path = os.path.realpath(__file__)
        try:
            with open(script_path, "wb") as f:
                f.write(r2.content)
        except Exception as e:
            print(f"{RED}Error writing update to disk:{RESET} {e}")
            return

        print(f"{GREEN}Updated to {latest}! Please re-run the script.{RESET}")
        sys.exit(0)
    else:
        print("Continuing with current version...\n")

def print_progress(stage: int, label: str):
    """
    Single‐bar smooth animation (used only for single‐ID calls).
    """
    bar_width = BAR_WIDTH
    target_pct = stage / TOTAL_STAGES
    current_pct = getattr(print_progress, "_current_pct", 0.0)

    steps = 20  # Number of frames for smooth animation
    for i in range(steps + 1):
        pct = current_pct + (target_pct - current_pct) * (i / steps)
        filled = int(bar_width * pct)
        empty = bar_width - filled
        bar = GREEN + "█" * filled + RESET + "░" * empty
        sys.stdout.write(f"\r{CYAN}{label.ljust(20)}{RESET} {bar} {pct*100:5.1f}%\033[K")
        sys.stdout.flush()
        time.sleep(0.01)

    # Save the latest value so the next animation starts from here
    print_progress._current_pct = target_pct

    if stage == TOTAL_STAGES:
        sys.stdout.write("\n")
        print_progress._current_pct = 0.0  # Reset for next lookup

def render_multi_bars():
    """
    Render all progress bars for spells in multi_labels/multi_progress.
    """
    lines = []
    for idx, label in enumerate(multi_labels):
        stage = multi_progress[idx]
        pct = stage / TOTAL_STAGES
        filled = int(BAR_WIDTH * pct)
        empty = BAR_WIDTH - filled
        bar = GREEN + "█" * filled + RESET + "░" * empty
        lines.append(f"{CYAN}{label.ljust(20)}{RESET} {bar} {pct*100:5.1f}%")
    return "\n".join(lines)

async def multi_bar_renderer():
    """
    Continuously update the multiple progress bars on screen until done.
    """
    while not multi_done_event.is_set():
        # Move cursor up by number of bars
        sys.stdout.write(f"\033[{len(multi_labels)}A")
        sys.stdout.write(render_multi_bars() + "\n")
        sys.stdout.flush()
        await asyncio.sleep(0.05)

    # Final update to 100%
    sys.stdout.write(f"\033[{len(multi_labels)}A")
    sys.stdout.write(render_multi_bars() + "\n")
    sys.stdout.flush()

async def fetch_db2(session: aiohttp.ClientSession, table: str, filters: dict, use_chain_build: bool=False):
    """
    Fetch data from wago.tools/db2 for the given table and filters.
    If use_chain_build=True, always include build=CHAIN_EFFECT_BUILD in the query string.
    """
    if use_chain_build:
        filters["build"] = CHAIN_EFFECT_BUILD

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
    return [e.get("filename") for e in file_list if str(e.get("fdid")) == str(search_id)]

def fetch_spell_class_wowhead(spell_id: int) -> str:
    url = f"https://www.wowhead.com/spell={spell_id}"
    resp = requests.get(url); resp.raise_for_status()
    m = re.search(r"\[class=(\d+)\]", resp.text)
    return CLASS_ID_MAP.get(m.group(1), "Unknown") if m else "Unknown"

def make_slug(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9 ]", "", slug)
    return re.sub(r"\s+", "-", slug.strip())

def print_wowhead_link(spell_id: int, spell_name: str):
    slug = make_slug(spell_name)
    url = f"https://www.wowhead.com/spell={spell_id}/{slug}"
    link = f"\033]8;;{url}\033\\{spell_name}\033]8;;\033\\"
    print(f"\n{YELLOW}Wowhead:{RESET} {link}")

# ──────────────────────────────────────────────────────────────────────────────
# ADDITION: SpellChainEffect texture lookup
# ──────────────────────────────────────────────────────────────────────────────

async def lookup_chain_effect_textures(session: aiohttp.ClientSession, visual_ids: set):
    chain_texture_files = []

    # 1) SpellVisualEvent → find entries with TargetType == 4, collect their SpellVisualKitID
    kit_ids = set()
    for vid in visual_ids:
        sv_events = await fetch_db2(
            session,
            "SpellVisualEvent",
            {"filter[SpellVisualID]": f"exact:{vid}", "page": "1"},
            use_chain_build=True
        )
        for ev in sv_events:
            if ev.get("TargetType") == 4:
                kit_ids.add(ev.get("SpellVisualKitID", 0))

    # 2) SpellVisualKitEffect → filter by EffectType == 1 (SpellProceduralEffectID), collect "Effect" (proc IDs)
    proc_ids = set()
    for kid in kit_ids:
        sv_ke = await fetch_db2(
            session,
            "SpellVisualKitEffect",
            {
                "filter[ParentSpellVisualKitID]": f"exact:{kid}",
                "filter[EffectType]": "exact:1",
                "page": "1"
            },
            use_chain_build=True
        )
        for row in sv_ke:
            pid = row.get("Effect")
            if pid:
                proc_ids.add(pid)

    # 3) SpellProceduralEffect → for each proc ID, get Value_0 (chain effect ID)
    chain_ids = set()
    for pid in proc_ids:
        proc_rows = await fetch_db2(
            session,
            "SpellProceduralEffect",
            {"filter[ID]": f"exact:{pid}", "page": "1"},
            use_chain_build=True
        )
        for prow in proc_rows:
            v0 = prow.get("Value_0")
            if v0:
                chain_ids.add(v0)

    # 4) SpellChainEffects → collect TextureFileDataID_0/1/2
    texture_ids = set()
    for cid in chain_ids:
        chains = await fetch_db2(
            session,
            "SpellChainEffects",
            {"filter[ID]": f"exact:{cid}", "page": "1"},
            use_chain_build=True
        )
        for crow in chains:
            for key in ("TextureFileDataID_0", "TextureFileDataID_1", "TextureFileDataID_2"):
                tid = crow.get(key)
                if tid:
                    texture_ids.add(tid)

    # 5) fetch_files for each texture FileDataID
    for tid in texture_ids:
        files = await fetch_files(session, tid)
        if files:
            chain_texture_files.extend(files)

    return sorted(set(chain_texture_files))

# ──────────────────────────────────────────────────────────────────────────────

async def process_spell_single(session: aiohttp.ClientSession, spell_id: int):
    """
    Original single-ID pipeline ( prints progress via print_progress, then outputs results).
    """
    # SpellName lookup
    spell_rows = await fetch_db2(
        session,
        "SpellName",
        {"filter[ID]": f"exact:{spell_id}", "page": "1"}
    )
    spell_name = str(spell_id)
    if spell_rows:
        for k, v in spell_rows[0].items():
            if "name" in k.lower():
                spell_name = v
                break

    spell_class   = fetch_spell_class_wowhead(spell_id)
    color         = CLASS_COLORS.get(spell_class, "")
    colored_class = f"{color}{spell_class}{RESET}"

    # Stage 0: initial progress
    print_progress(0, spell_name)

    # Stage 1: SpellXSpellVisual → SpellVisualID(s)
    visuals = await fetch_db2(
        session,
        "SpellXSpellVisual",
        {"filter[SpellID]": f"exact:{spell_id}", "page": "1"}
    )
    visual_ids = {r.get("SpellVisualID") for r in visuals}
    print_progress(1, "SpellVisualIDs")

    # Stage 2: SpellVisualEvent → collect SpellVisualKitIDs
    rows2 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEvent", {"filter[SpellVisualID]": f"exact:{vid}", "page": "1"})
        for vid in visual_ids
    ])
    kit_ids = {r.get("SpellVisualKitID") for sub in rows2 for r in sub}
    print_progress(2, "SpellVisualKitIDs")

    # Stage 3: SpellVisualKitEffect (Type=2) → collect ModelAttachIDs
    rows3 = await asyncio.gather(*[
        fetch_db2(
            session,
            "SpellVisualKitEffect",
            {
                "filter[ParentSpellVisualKitID]": f"exact:{kid}",
                "filter[EffectType]": "exact:2",
                "page": "1"
            }
        )
        for kid in kit_ids
    ])
    attach_ids = {r.get("Effect") for sub in rows3 for r in sub}
    print_progress(3, "ModelAttachIDs")

    # Stage 4: SpellVisualKitModelAttach → SpellVisualEffectNameID(s)
    rows4 = await asyncio.gather(*[
        fetch_db2(session, "spellvisualkitmodelattach", {"filter[ID]": f"exact:{aid}", "page": "1"})
        for aid in attach_ids
    ])
    name_ids = {r.get("SpellVisualEffectNameID") for sub in rows4 for r in sub}
    print_progress(4, "EffectNameIDs")

    # Stage 5: SpellVisualEffectName → ModelFileDataID(s)
    rows5 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEffectName", {"filter[ID]": f"exact:{nid}", "page": "1"})
        for nid in name_ids
    ])
    data_ids = {r.get("ModelFileDataID") for sub in rows5 for r in sub}
    print_progress(5, "ModelFileDataIDs")

    # Stage 6: ModelFileData → .m2 filenames
    filenames = []
    for did in data_ids:
        filenames.extend(await fetch_files(session, did))
    filenames = sorted(set(filenames))
    print_progress(6, "Filenames")

    if filenames:
        print(f"\n{YELLOW}Found files for {spell_name} [{colored_class}]{RESET}")
        for f in filenames:
            print(f"{GREEN}- {f}{RESET}")
    else:
        print(f"\n{RED}No model (.m2) files found for {spell_name} [{colored_class}]{RESET}")

    # Stage 7: SpellChainEffect textures
    print_progress(7, "ChainTextures")
    chain_files = await lookup_chain_effect_textures(session, visual_ids)
    if chain_files:
        print(f"\n{YELLOW}Found ChainEffect textures for {spell_name} [{colored_class}]{RESET}")
        for cf in chain_files:
            print(f"{GREEN}- {cf}{RESET}")
    else:
        print(f"\n{YELLOW}No ChainEffect textures found for {spell_name} [{colored_class}]{RESET}")

    print_wowhead_link(spell_id, spell_name)
    print()

async def process_spell_indexed(session: aiohttp.ClientSession, spell_id: int, idx: int, result_m2: set, result_chain: set):
    """
    Exactly the same pipeline as before, but updates multi_progress[idx]
    and fills result_m2/result_chain sets instead of printing immediately.
    """
    # SpellName lookup
    spell_rows = await fetch_db2(
        session,
        "SpellName",
        {"filter[ID]": f"exact:{spell_id}", "page": "1"}
    )
    spell_name = str(spell_id)
    if spell_rows:
        for k, v in spell_rows[0].items():
            if "name" in k.lower():
                spell_name = v
                break

    # Stage 0
    multi_progress[idx] = 0

    # Stage 1: SpellXSpellVisual → SpellVisualID(s)
    visuals = await fetch_db2(
        session,
        "SpellXSpellVisual",
        {"filter[SpellID]": f"exact:{spell_id}", "page": "1"}
    )
    visual_ids = {r.get("SpellVisualID") for r in visuals}
    multi_progress[idx] = 1

    # Stage 2: SpellVisualEvent → collect SpellVisualKitIDs
    rows2 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEvent", {"filter[SpellVisualID]": f"exact:{vid}", "page": "1"})
        for vid in visual_ids
    ])
    kit_ids = {r.get("SpellVisualKitID") for sub in rows2 for r in sub}
    multi_progress[idx] = 2

    # Stage 3: SpellVisualKitEffect (Type=2) → collect ModelAttachIDs
    rows3 = await asyncio.gather(*[
        fetch_db2(
            session,
            "SpellVisualKitEffect",
            {
                "filter[ParentSpellVisualKitID]": f"exact:{kid}",
                "filter[EffectType]": "exact:2",
                "page": "1"
            }
        )
        for kid in kit_ids
    ])
    attach_ids = {r.get("Effect") for sub in rows3 for r in sub}
    multi_progress[idx] = 3

    # Stage 4: SpellVisualKitModelAttach → SpellVisualEffectNameID(s)
    rows4 = await asyncio.gather(*[
        fetch_db2(session, "spellvisualkitmodelattach", {"filter[ID]": f"exact:{aid}", "page": "1"})
        for aid in attach_ids
    ])
    name_ids = {r.get("SpellVisualEffectNameID") for sub in rows4 for r in sub}
    multi_progress[idx] = 4

    # Stage 5: SpellVisualEffectName → ModelFileDataID(s)
    rows5 = await asyncio.gather(*[
        fetch_db2(session, "SpellVisualEffectName", {"filter[ID]": f"exact:{nid}", "page": "1"})
        for nid in name_ids
    ])
    data_ids = {r.get("ModelFileDataID") for sub in rows5 for r in sub}
    multi_progress[idx] = 5

    # Stage 6: ModelFileData → .m2 filenames
    for did in data_ids:
        m2s = await fetch_files(session, did)
        for m2 in m2s:
            result_m2.add(m2)
    multi_progress[idx] = 6

    # Stage 7: SpellChainEffect textures
    chain_files = await lookup_chain_effect_textures(session, visual_ids)
    for cf in chain_files:
        result_chain.add(cf)
    multi_progress[idx] = 7

async def search_and_process(name_or_id: str):
    global SEM

    # Create a new semaphore for this run, bound to the current event loop
    SEM = asyncio.Semaphore(16)

    async with aiohttp.ClientSession() as session:
        # If user typed digits, treat as a single SpellID:
        if name_or_id.isdigit():
            await process_spell_single(session, int(name_or_id))
            return

        # Otherwise, look up all SpellIDs matching that exact name:
        ids = await fetch_spell_ids_by_name(session, name_or_id)
        if not ids:
            print(f"{YELLOW}No spells found matching \"{name_or_id}\".{RESET}")
            return

        # Fetch spell names for each ID, to label bars:
        names = []
        for sid in ids:
            rows = await fetch_db2(session, "SpellName", {"filter[ID]": f"exact:{sid}", "page": "1"})
            if rows:
                for k, v in rows[0].items():
                    if "name" in k.lower():
                        names.append(v)
                        break
                else:
                    names.append(str(sid))
            else:
                names.append(str(sid))

        # Prepare shared state
        global multi_labels, multi_progress, multi_done_event
        multi_labels = names.copy()
        multi_progress = [0] * len(ids)
        multi_done_event = asyncio.Event()

        # Print placeholders for each bar (one per ID)
        for label in multi_labels:
            bar = " " * BAR_WIDTH
            print(f"{CYAN}{label.ljust(20)}{RESET} [{bar}]   0.0%")

        # Start the multi‐bar renderer
        renderer_task = asyncio.create_task(multi_bar_renderer())

        # Prepare aggregate result sets
        aggregate_m2 = set()
        aggregate_chain = set()

        # Launch one task per ID
        tasks = []
        for idx, sid in enumerate(ids):
            tasks.append(
                asyncio.create_task(
                    process_spell_indexed(session, sid, idx, aggregate_m2, aggregate_chain)
                )
            )

        # Wait until all ID‐tasks finish
        await asyncio.gather(*tasks)

        # Signal renderer to do a final update, then exit
        multi_done_event.set()
        await renderer_task

        # After that, print de‐duplicated results:
        if aggregate_m2:
            print(f"\n{YELLOW}Found files for \"{name_or_id}\" (all IDs combined):{RESET}")
            for f in sorted(aggregate_m2):
                print(f"{GREEN}- {f}{RESET}")
        else:
            print(f"\n{RED}No model (.m2) files found for \"{name_or_id}\"{RESET}")

        if aggregate_chain:
            print(f"\n{YELLOW}Found ChainEffect textures for \"{name_or_id}\" (all IDs combined):{RESET}")
            for cf in sorted(aggregate_chain):
                print(f"{GREEN}- {cf}{RESET}")
        else:
            print(f"\n{YELLOW}No ChainEffect textures found for \"{name_or_id}\"{RESET}")

        # Finally, print a Wowhead link using the first ID:
        first_id = ids[0]
        print_wowhead_link(first_id, name_or_id)
        print()

def interactive_loop():
    while True:
        print_banner()
        prompt_update()
        inp = input("Enter a SpellID or exact spell name: ").strip()
        if not inp:
            continue
        asyncio.run(search_and_process(inp))
        input("Press Enter for new search")

if __name__ == "__main__":
    interactive_loop()
