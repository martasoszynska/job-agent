"""
Orchestrates one full run: fetch -> dedupe against state -> score new ones ->
write output -> update state.

Run manually with: python agent/main.py
"""

import json
from pathlib import Path
from datetime import date

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from sources import fetch_all
from score import score_listing

HERE = Path(__file__).parent
PROFILE_PATH = HERE / "profile.json"
STATE_PATH = HERE / "state.json"
OUTPUT_PATH = HERE.parent / "This_Weeks_Picks.xlsx"


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def dedupe(listings, state):
    seen_urls = {item["url"] for item in state["seen_links"]}
    new_listings = [l for l in listings if l["url"] not in seen_urls]
    return new_listings


def _migrate_to_dated_schema(ws, state):
    """
    One-time migration for a workbook written before the Date Found column
    existed (old schema: Title, Organization/Source, URL, Match Score, Notes,
    Legal Work Flag). Backfills each existing row's date from state.json,
    matched by URL, rather than losing that history.
    """
    date_by_url = {item["url"]: item.get("date_first_seen", "") for item in state["seen_links"]}
    ws.insert_cols(1)
    ws.cell(row=1, column=1, value="Date Found")
    for row in range(2, ws.max_row + 1):
        url = ws.cell(row=row, column=4).value  # URL shifted from column C to D
        ws.cell(row=row, column=1, value=date_by_url.get(url, ""))
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
    for col, width in zip("ABCDEFG", [12, 30, 20, 45, 12, 55, 30]):
        ws.column_dimensions[col].width = width


def write_excel(scored_listings, state):
    """
    Appends to the existing workbook rather than overwriting it, so the file
    is a running history of every listing ever found rather than just this
    run's new ones. Safe from duplicates because scored_listings only ever
    contains listings dedupe() found weren't already in state.json.
    """
    headers = ["Date Found", "Title", "Organization/Source", "URL", "Match Score", "Notes", "Legal Work Flag"]
    if OUTPUT_PATH.exists():
        wb = openpyxl.load_workbook(OUTPUT_PATH)
        ws = wb.active
        if ws.cell(row=1, column=1).value != "Date Found":
            _migrate_to_dated_schema(ws, state)
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "All Picks"
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        for col, width in zip("ABCDEFG", [12, 30, 20, 45, 12, 55, 30]):
            ws.column_dimensions[col].width = width

    today = str(date.today())
    for listing, score in scored_listings:
        ws.append([
            today,
            listing.get("title"),
            listing.get("source"),
            listing.get("url"),
            score.get("match_score"),
            score.get("notes"),
            score.get("legal_work_flag"),
        ])
    wb.save(OUTPUT_PATH)


def main():
    profile = load_json(PROFILE_PATH)
    state = load_json(STATE_PATH)

    print("Fetching listings from all sources...")
    all_listings = fetch_all(profile)
    print(f"Found {len(all_listings)} total listings across sources.")

    new_listings = dedupe(all_listings, state)
    print(f"{len(new_listings)} are new since last run.")

    scored = []
    for listing in new_listings:
        score = score_listing(listing, profile)
        scored.append((listing, score))
        state["seen_links"].append({
            "url": listing["url"],
            "title": listing["title"],
            "organization": listing.get("source", ""),
            "date_first_seen": str(date.today()),
        })

    write_excel(scored, state)
    save_json(STATE_PATH, state)
    print(f"Appended {len(scored)} new scored listings to {OUTPUT_PATH}")
    print("Updated state.json - remember this needs to be committed back to the repo.")


if __name__ == "__main__":
    main()
