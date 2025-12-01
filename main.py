# main.py
import os
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------
# Ensure folders exist
# ---------------------
os.makedirs("uploads", exist_ok=True)
os.makedirs("data", exist_ok=True)

# ---------------------
# Simple JSON storage helpers
# ---------------------
def _load_json(filename, default):
    path = os.path.join("data", filename)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(filename, obj):
    path = os.path.join("data", filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# initialize basic data files if missing
_load_json("reminders.json", [])
_load_json("reviews.json", [])
_load_json("profiles.json", {})
_load_json("tokens.json", {})

# ---------------------
# Firebase admin init (safe for Render)
# ---------------------
firebase_admin = None
messaging = None
try:
    import firebase_admin
    from firebase_admin import credentials, messaging as fcm_messaging

    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    cred = None
    if sa_json:
        # sa_json may be the raw JSON string or a file path
        try:
            sa = json.loads(sa_json)
            cred = credentials.Certificate(sa)
        except Exception:
            # maybe it's a path on disk
            if os.path.exists(sa_json):
                cred = credentials.Certificate(sa_json)

    # fallback: if medibuddy.json exists (local dev)
    if not cred and os.path.exists("medibuddy.json"):
        cred = credentials.Certificate("medibuddy.json")

    if cred:
        try:
            firebase_admin.initialize_app(cred)
            firebase_admin = firebase_admin
            messaging = fcm_messaging
            print("Firebase admin initialized")
        except Exception as e:
            # already initialized or other issue
            print("Firebase admin init warning:", str(e))
    else:
        print("Firebase admin not initialized — no service account found (okay for dev).")
except Exception as e:
    print("Firebase admin library not installed or failed to load:", str(e))

# ---------------------
# FastAPI app
# ---------------------
app = FastAPI(title="MediBuddy Backend (simple)")

# Allow CORS from your frontend (you can tighten this later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # set to your domain in production (e.g. https://frontend-medibuddy.vercel.app)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Expose uploads as static files
# (uploads dir already created above to avoid startup error)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ---------------------
# Helpers
# ---------------------
def _new_id():
    return uuid.uuid4().hex

# ---------------------
# API endpoints
# ---------------------

@app.get("/api/reminders")
async def list_reminders():
    data = _load_json("reminders.json", [])
    # ensure numeric timestamps
    return JSONResponse(data)

@app.post("/api/reminders")
async def create_reminder(payload: Dict[str, Any]):
    data = _load_json("reminders.json", [])
    # Ensure required fields exist
    reminder = {
        "id": _new_id(),
        "medicine": payload.get("medicine"),
        "timestamp": int(payload.get("timestamp") or 0),
        "phone": payload.get("phone"),
        "day": payload.get("day", 1),
        "created_at": int(datetime.utcnow().timestamp() * 1000),
        "taken": False
    }
    data.append(reminder)
    _save_json("reminders.json", data)
    return JSONResponse({"ok": True, "id": reminder["id"]})

@app.post("/api/chat")
async def chat_endpoint(payload: Dict[str, Any]):
    # Minimal placeholder chat logic. Replace with real AI logic / backend later.
    message = payload.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    # naive reply — echo + safe suggestion
    reply = f"I got your message: \"{message}\". MediMitra suggests to check medicine label and consult a doctor for medical questions."
    return JSONResponse({"reply": reply})

@app.get("/api/reviews")
async def get_reviews():
    data = _load_json("reviews.json", [])
    return JSONResponse(data)

@app.post("/api/reviews")
async def post_review(payload: Dict[str, Any]):
    data = _load_json("reviews.json", [])
    review = {
        "id": _new_id(),
        "name": payload.get("name") or "Anonymous",
        "text": payload.get("text") or "",
        "rating": int(payload.get("rating") or 0),
        "phone": payload.get("phone"),
        "created_at": int(datetime.utcnow().timestamp() * 1000),
    }
    data.append(review)
    _save_json("reviews.json", data)
    return JSONResponse({"ok": True, "id": review["id"]})

@app.post("/api/profile")
async def save_profile(payload: Dict[str, Any]):
    profiles = _load_json("profiles.json", {})
    phone = payload.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    profiles[phone] = {
        "name": payload.get("name"),
        "email": payload.get("email"),
        "notes": payload.get("notes") or profiles.get(phone, {}).get("notes"),
        "updated_at": int(datetime.utcnow().timestamp() * 1000)
    }
    _save_json("profiles.json", profiles)
    return JSONResponse({"ok": True})

@app.post("/api/token/register")
async def register_token(payload: Dict[str, Any]):
    tokens = _load_json("tokens.json", {})
    phone = payload.get("phone")
    token = payload.get("token")
    if not phone or not token:
        raise HTTPException(status_code=400, detail="phone and token required")
    tokens[phone] = token
    _save_json("tokens.json", tokens)

    # Try to send a test notification (best-effort)
    if messaging:
        try:
            message = {
                "token": token,
                "notification": {"title": "MediBuddy", "body": "Notifications enabled for this device."},
            }
            # send may raise if invalid token or permission issues
            messaging.send(message)
        except Exception as e:
            print("FCM send warning:", str(e))

    return JSONResponse({"ok": True})

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    # save uploaded file to uploads/ and return its public path
    filename = file.filename or f"upload-{_new_id()}"
    safe_name = f"{_new_id()}-{filename.replace(' ', '_')}"
    save_path = os.path.join("uploads", safe_name)
    try:
        with open(save_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to save: {e}")
    public_url = f"/uploads/{safe_name}"
    return JSONResponse({"ok": True, "path": public_url, "filename": safe_name})

# ---------------------
# Root / health
# ---------------------
@app.get("/")
async def root():
    return {"status": "ok", "time": int(datetime.utcnow().timestamp() * 1000)}
