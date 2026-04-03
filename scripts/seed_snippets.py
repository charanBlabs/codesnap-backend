"""
Seed script: Import snippets from your Excel backup into Neon DB
Run: DATABASE_URL=... python scripts/seed_snippets.py
"""

import asyncio
import asyncpg
import os
import sys
import pandas as pd
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL")

LANGUAGE_HINTS = {
    "css": "css", "style": "css", "html": "html",
    "php": "php", "mysql": "sql", "jquery": "javascript",
    "javascript": "javascript", "js": "javascript",
    "ftp": "php", "widget": "php", "script": "javascript",
}

def detect_language(title: str, code: str) -> str:
    text = (title + " " + code[:200]).lower()
    for hint, lang in LANGUAGE_HINTS.items():
        if hint in text:
            return lang
    if "<?php" in code:
        return "php"
    if "<script" in code or "$(document" in code:
        return "javascript"
    if "{" in code and ":" in code and ";" in code:
        return "css"
    return "php"

def detect_tags(title: str, code: str) -> list:
    text = (title + " " + code[:500]).lower()
    tags = []
    if "css" in text or "style" in text or "background" in text: tags.append("CSS")
    if "<?php" in code or "php" in text: tags.append("PHP")
    if "<script" in code or "jquery" in text or "$(document" in code: tags.append("JavaScript")
    if "select" in text.lower() and "from" in text.lower(): tags.append("SQL")
    if "<div" in code or "<a " in code or "html" in text: tags.append("HTML")
    if "ftp" in text: tags.append("FTP")
    return list(dict.fromkeys(tags))  # deduplicate preserving order

async def seed():
    if not DATABASE_URL:
        print("ERROR: Set DATABASE_URL environment variable")
        sys.exit(1)

    # Find the Excel file
    excel_path = Path(__file__).parent.parent.parent / "Codes_Backup__1_.xlsx"
    if not excel_path.exists():
        # Try current directory
        excel_path = Path("Codes_Backup__1_.xlsx")
    if not excel_path.exists():
        print(f"ERROR: Cannot find Codes_Backup__1_.xlsx")
        print("Place the Excel file next to this script or in the project root.")
        sys.exit(1)

    print(f"Reading: {excel_path}")
    df = pd.read_excel(excel_path)
    df.columns = ["title", "code", "working_pages"] + [f"_col{i}" for i in range(len(df.columns)-3)]
    df = df[["title", "code", "working_pages"]]
    df = df.fillna("")

    conn = await asyncpg.connect(DATABASE_URL)

    # Get or create system user for seeded data
    admin = await conn.fetchrow("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if not admin:
        print("ERROR: No admin user found. Create one first with create_user.py")
        await conn.close()
        sys.exit(1)

    admin_id = admin["id"]
    count = 0

    for _, row in df.iterrows():
        title = str(row["title"]).strip()
        code = str(row["code"]).strip()
        working_pages = str(row["working_pages"]).strip() if row["working_pages"] else None

        if not title or title == "nan" or not code or code == "nan":
            continue

        language = detect_language(title, code)
        tags = detect_tags(title, code)
        description = f"Imported from Excel backup. Working pages: {working_pages}" if working_pages else "Imported from Excel backup."

        await conn.execute("""
            INSERT INTO snippets (title, description, code, language, tags, working_pages, created_by, updated_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
            ON CONFLICT DO NOTHING
        """, title, description, code, language, tags, working_pages, admin_id)
        count += 1
        print(f"  ✓ [{language}] {title[:60]}")

    await conn.close()
    print(f"\n✅ Seeded {count} snippets successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
