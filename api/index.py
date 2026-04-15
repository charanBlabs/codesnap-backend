"""
CodeSnap API — Python FastAPI Backend
Deployed as Vercel Serverless Function
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import os
import asyncpg
import bcrypt
import jwt
from uuid import UUID

# ─── Config ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="CodeSnap API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

# ─── DB Pool ─────────────────────────────────────────────────────────────────

_pool = None

async def get_db():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        yield conn

# ─── Schemas ─────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    name: str
    password: str
    role: str = "member"

class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: datetime

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserOut

class SnippetCreate(BaseModel):
    title: str
    description: Optional[str] = None
    code: str
    language: str = "php"
    tags: List[str] = []
    working_pages: Optional[str] = None

class SnippetUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    language: Optional[str] = None
    tags: Optional[List[str]] = None
    working_pages: Optional[str] = None

class SnippetOut(BaseModel):
    id: str
    title: str
    description: Optional[str]
    code: str
    language: str
    tags: List[str]
    working_pages: Optional[str]
    created_by: Optional[str]
    created_by_name: Optional[str]
    updated_by: Optional[str]
    updated_by_name: Optional[str]
    created_at: datetime
    updated_at: datetime

class TagCreate(BaseModel):
    name: str
    color: str = "#6366f1"

class TagOut(BaseModel):
    id: str
    name: str
    color: str

# ─── Auth Helpers ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = await db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if user is None:
        raise credentials_exception
    return dict(user)

async def get_optional_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)):
    """Get current user if token is provided, otherwise return None (public access)"""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except jwt.PyJWTError:
        return None

    user = await db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if user is None:
        return None
    return dict(user)

async def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

# ─── Auth Routes ─────────────────────────────────────────────────────────────

@app.post("/api/auth/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    user = await db.fetchrow("SELECT * FROM users WHERE email = $1", form_data.username)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = create_access_token({"sub": str(user["id"])})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "created_at": user["created_at"],
        }
    }

@app.get("/api/auth/me", response_model=UserOut)
async def get_me(current_user: dict = Depends(get_current_user)):
    return {**current_user, "id": str(current_user["id"])}

# ─── Snippet Routes ───────────────────────────────────────────────────────────

@app.get("/api/snippets", response_model=List[SnippetOut])
async def list_snippets(
    search: Optional[str] = None,
    tag: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db=Depends(get_db),
    current_user: Optional[dict] = Depends(get_optional_user)
):
    query = """
        SELECT 
            s.id, s.title, s.description, s.code, s.language, s.tags,
            s.working_pages, s.created_at, s.updated_at,
            s.created_by, cu.name as created_by_name,
            s.updated_by, uu.name as updated_by_name
        FROM snippets s
        LEFT JOIN users cu ON s.created_by = cu.id
        LEFT JOIN users uu ON s.updated_by = uu.id
        WHERE 1=1
    """
    params = []
    idx = 1

    if search:
        query += f" AND (s.title ILIKE ${idx} OR s.description ILIKE ${idx} OR s.code ILIKE ${idx})"
        params.append(f"%{search}%")
        idx += 1

    if tag:
        query += f" AND ${idx} = ANY(s.tags)"
        params.append(tag)
        idx += 1

    if language:
        query += f" AND s.language = ${idx}"
        params.append(language)
        idx += 1

    query += f" ORDER BY s.updated_at DESC LIMIT ${idx} OFFSET ${idx+1}"
    params.extend([limit, offset])

    rows = await db.fetch(query, *params)
    return [
        {**dict(r), "id": str(r["id"]),
         "created_by": str(r["created_by"]) if r["created_by"] else None,
         "updated_by": str(r["updated_by"]) if r["updated_by"] else None}
        for r in rows
    ]

@app.get("/api/snippets/{snippet_id}", response_model=SnippetOut)
async def get_snippet(snippet_id: str, db=Depends(get_db), current_user: Optional[dict] = Depends(get_optional_user)):
    row = await db.fetchrow("""
        SELECT s.*, cu.name as created_by_name, uu.name as updated_by_name
        FROM snippets s
        LEFT JOIN users cu ON s.created_by = cu.id
        LEFT JOIN users uu ON s.updated_by = uu.id
        WHERE s.id = $1
    """, snippet_id)
    if not row:
        raise HTTPException(status_code=404, detail="Snippet not found")
    return {**dict(row), "id": str(row["id"])}

@app.post("/api/snippets", response_model=SnippetOut, status_code=201)
async def create_snippet(data: SnippetCreate, db=Depends(get_db), current_user: dict = Depends(get_current_user)):
    row = await db.fetchrow("""
        INSERT INTO snippets (title, description, code, language, tags, working_pages, created_by, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
        RETURNING *
    """, data.title, data.description, data.code, data.language,
        data.tags, data.working_pages, current_user["id"])

    return {**dict(row), "id": str(row["id"]),
            "created_by_name": current_user["name"], "updated_by_name": current_user["name"],
            "created_by": str(row["created_by"]), "updated_by": str(row["updated_by"])}

@app.put("/api/snippets/{snippet_id}", response_model=SnippetOut)
async def update_snippet(snippet_id: str, data: SnippetUpdate, db=Depends(get_db), current_user: dict = Depends(get_current_user)):
    existing = await db.fetchrow("SELECT * FROM snippets WHERE id = $1", snippet_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Snippet not found")

    updates = {k: v for k, v in data.dict().items() if v is not None}
    updates["updated_by"] = current_user["id"]
    updates["updated_at"] = datetime.utcnow()

    set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
    values = list(updates.values())

    row = await db.fetchrow(
        f"UPDATE snippets SET {set_clause} WHERE id = $1 RETURNING *",
        snippet_id, *values
    )
    return {**dict(row), "id": str(row["id"]), "updated_by_name": current_user["name"]}

@app.delete("/api/snippets/{snippet_id}", status_code=204)
async def delete_snippet(snippet_id: str, db=Depends(get_db), current_user: dict = Depends(get_current_user)):
    existing = await db.fetchrow("SELECT * FROM snippets WHERE id = $1", snippet_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if str(existing["created_by"]) != str(current_user["id"]) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="You can only delete your own snippets")
    await db.execute("DELETE FROM snippets WHERE id = $1", snippet_id)

# ─── Tag Routes ───────────────────────────────────────────────────────────────

@app.get("/api/tags", response_model=List[TagOut])
async def list_tags(db=Depends(get_db), current_user: Optional[dict] = Depends(get_optional_user)):
    rows = await db.fetch("SELECT * FROM tags ORDER BY name")
    return [{"id": str(r["id"]), "name": r["name"], "color": r["color"]} for r in rows]

@app.post("/api/tags", response_model=TagOut, status_code=201)
async def create_tag(data: TagCreate, db=Depends(get_db), current_user: dict = Depends(require_admin)):
    row = await db.fetchrow(
        "INSERT INTO tags (name, color) VALUES ($1, $2) ON CONFLICT (name) DO UPDATE SET color=$2 RETURNING *",
        data.name, data.color
    )
    return {"id": str(row["id"]), "name": row["name"], "color": row["color"]}

# ─── User Management (Admin) ──────────────────────────────────────────────────

@app.get("/api/users", response_model=List[UserOut])
async def list_users(db=Depends(get_db), current_user: dict = Depends(require_admin)):
    rows = await db.fetch("SELECT * FROM users ORDER BY created_at DESC")
    return [{"id": str(r["id"]), "email": r["email"], "name": r["name"], "role": r["role"], "created_at": r["created_at"]} for r in rows]

@app.post("/api/users", response_model=UserOut, status_code=201)
async def create_user(data: UserCreate, db=Depends(get_db), current_user: dict = Depends(require_admin)):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", data.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = hash_password(data.password)
    row = await db.fetchrow(
        "INSERT INTO users (email, name, password_hash, role) VALUES ($1, $2, $3, $4) RETURNING *",
        data.email, data.name, hashed, data.role
    )
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"], "created_at": row["created_at"]}

@app.delete("/api/users/{user_id}", status_code=204)
async def delete_user(user_id: str, db=Depends(get_db), current_user: dict = Depends(require_admin)):
    if str(current_user["id"]) == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    await db.execute("DELETE FROM users WHERE id = $1", user_id)

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "CodeSnap API"}
