"""
Create a new user in the database
Usage: DATABASE_URL=... python scripts/create_user.py email name password [role]
"""

import asyncio
import asyncpg
import bcrypt
import os
import sys

async def create_user(email, name, password, role="member"):
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: Set DATABASE_URL environment variable")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        row = await conn.fetchrow(
            "INSERT INTO users (email, name, password_hash, role) VALUES ($1, $2, $3, $4) RETURNING id",
            email, name, hashed, role
        )
        print(f"✅ Created {role}: {name} <{email}> (id: {row['id']})")
    except asyncpg.UniqueViolationError:
        print(f"❌ Email {email} already exists")
    finally:
        await conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python create_user.py email name password [role]")
        sys.exit(1)
    email = sys.argv[1]
    name = sys.argv[2]
    password = sys.argv[3]
    role = sys.argv[4] if len(sys.argv) > 4 else "member"
    asyncio.run(create_user(email, name, password, role))
