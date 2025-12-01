# main.py
import os
import json
import threading
import time
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from firebase_admin import credentials, initialize_app, messaging
from sqlalchemy import create_engine, Column, String, Integer, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

# Load env for local use
load_dotenv()

# ------------------ ENV ------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./medibuddy.db")
SERVICE_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "medibuddy.json")
PORT = int(os.getenv("PORT", 8000))

# ------------------ DB ------------------
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String)
    medicine = Column(String)
    timestamp = Column(Integer)
    day = Column(Integer)
    taken = Column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    phone = Column(String, primary_key=True)
    name = Column(String, default="")
    email = Column(String, default="")
    token = Column(String, default="")


Base.metadata.create_all(engine)

# ------------------ FIREBASE ------------------
try:
    cred = credentials.Certificate(SERVICE_FILE)
    initialize_app(cred)
except Exception as e:
    print("Firebase init error:", e)

# ------------------ FASTAPI ------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # FRONTEND runs on Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------- Ensure uploads directory exists before mounting StaticFiles -------
BASE_DIR = Path(__file__).resolve().parent
uploads_dir = BASE_DIR / "uploads"
uploads_dir.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")
# ------------------------------------------------------------------------

# ===================== MODELS =====================

class ChatRequest(BaseModel):
    message: str
    phone: str = ""


class ReminderCreate(BaseModel):
    phone: str
    medicine: str
    timestamp: int
    day: int = 1


class Profile(BaseModel):
    phone: str
    name: str
    email: str


class TokenModel(BaseModel):
    phone: str
    token: str


class Review(BaseModel):
    name: str
    text: str
    rating: int
    phone: str | None = None

# -------------------------------------------------------
#                      ENDPOINTS
# -------------------------------------------------------

@app.post("/api/token/register")
def register_token(data: TokenModel):
    db = SessionLocal()
    user = db.query(User).filter_by(phone=data.phone).first()
    if not user:
        user = User(phone=data.phone, token=data.token)
        db.add(user)
    else:
        user.token = data.token
    db.commit()
    return {"ok": True}


@app.post("/api/profile")
def save_profile(p: Profile):
    db = SessionLocal()
    user = db.query(User).filter_by(phone=p.phone).first()
    if not user:
        user = User(phone=p.phone, name=p.name, email=p.email)
        db.add(user)
    else:
        user.name = p.name
        user.email = p.email
    db.commit()
    return {"ok": True}


@app.get("/api/reminders")
def get_reminders():
    db = SessionLocal()
    items = db.query(Reminder).all()
    return [dict(
        id=i.id,
        phone=i.phone,
        medicine=i.medicine,
        timestamp=i.timestamp,
        day=i.day,
        taken=i.taken
    ) for i in items]


@app.post("/api/reminders")
def create_reminder(r: ReminderCreate):
    db = SessionLocal()
    rem = Reminder(
        phone=r.phone,
        medicine=r.medicine,
        timestamp=r.timestamp,
        day=r.day,
    )
    db.add(rem)
    db.commit()
    db.refresh(rem)
    return {"ok": True, "id": rem.id}


@app.post("/api/chat")
def chat(req: ChatRequest):
    bot_reply = f"Your message: {req.message}\nThis is a demo reply."
    return {"reply": bot_reply}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    # ensure directory exists (redundant but safe)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    file_path = uploads_dir / file.filename
    with open(file_path, "wb") as f:
        f.write(await file.read())
    # return path relative to mounted static URL
    return {"ok": True, "path": f"/uploads/{file.filename}"}


reviews_db = "reviews.json"


@app.get("/api/reviews")
def get_reviews():
    if not os.path.exists(reviews_db):
        return []
    with open(reviews_db) as f:
        return json.load(f)


@app.post("/api/reviews")
def save_review(r: Review):
    all_reviews = []
    if os.path.exists(reviews_db):
        all_reviews = json.load(open(reviews_db))

    all_reviews.append({
        "name": r.name,
        "text": r.text,
        "rating": r.rating,
        "phone": r.phone,
        "created_at": int(time.time() * 1000)
    })
    json.dump(all_reviews, open(reviews_db, "w"))
    return {"ok": True}


# -------------------------------------------------------
#                  REMINDER WORKER
# -------------------------------------------------------

def worker():
    while True:
        try:
            db = SessionLocal()
            now = int(time.time() * 1000)

            due = db.query(Reminder).filter(
                Reminder.timestamp <= now,
                Reminder.taken == False
            ).all()

            for r in due:
                user = db.query(User).filter_by(phone=r.phone).first()
                if user and user.token:
                    try:
                        messaging.send(messaging.Message(
                            notification=messaging.Notification(
                                title="Medicine Reminder",
                                body=f"Time to take {r.medicine}"
                            ),
                            token=user.token
                        ))
                    except:
                        pass

                r.taken = True
                db.commit()

        except Exception as e:
            print("Worker error:", e)

        time.sleep(10)


threading.Thread(target=worker, daemon=True).start()
