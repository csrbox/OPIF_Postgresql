from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from database import get_connection
import os, secrets, json

load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

app = FastAPI(title="Questionnaire API", description="Questionnaire Backend API - PostgreSQL", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
admin_sessions = set()

class ResponseData(BaseModel):
    name: str
    answers: dict

class AdminLogin(BaseModel):
    password: str

class AddQuestion(BaseModel):
    question: str
    question_type: str
    required: bool
    options: list[str] = Field(default_factory=list)

def verify_admin(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required")
    token = authorization.replace("Bearer ", "", 1)
    if token not in admin_sessions:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")
    return token

def qdict(row):
    return {"question_id": f"Q{row[0]}", "question": row[1], "type": row[2], "required": bool(row[3]), "options": row[4].split("|") if row[4] else []}

def validate_question(q: AddQuestion):
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if q.question_type not in ["TEXT", "MCQ", "YES_NO"]:
        raise HTTPException(status_code=400, detail="Invalid question type")
    if q.question_type == "MCQ":
        opts = [x.strip() for x in q.options if x.strip()]
        if len(opts) < 2: raise HTTPException(status_code=400, detail="MCQ must have at least 2 options")
        if len(opts) > 4: raise HTTPException(status_code=400, detail="Maximum 4 options allowed")
        return opts
    return ["Yes", "No"] if q.question_type == "YES_NO" else []

@app.get("/")
def home():
    return {"message": "Questionnaire Backend is running", "database": "PostgreSQL"}

@app.get("/test-db")
def test_db():
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT current_database();")
        return {"success": True, "database": cur.fetchone()[0]}
    finally:
        cur.close(); conn.close()

@app.get("/questions")
def get_public_questions():
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, question, type, required, options FROM questions ORDER BY id")
        return {"success": True, "data": [qdict(r) for r in cur.fetchall()]}
    finally:
        cur.close(); conn.close()

@app.post("/responses")
def submit_response(data: ResponseData):
    if not data.name.strip(): raise HTTPException(status_code=400, detail="Name is required")
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM responses")
        sid = f"SUB-{cur.fetchone()[0]:03d}"
        cur.execute("INSERT INTO responses (submission_id, name, answers) VALUES (%s,%s,%s)", (sid, data.name.strip(), json.dumps(data.answers)))
        conn.commit()
        return {"success": True, "message": "Submission stored successfully", "submission_id": sid}
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close(); conn.close()

@app.post("/admin/login")
def admin_login(data: AdminLogin):
    if not ADMIN_PASSWORD: raise HTTPException(status_code=500, detail="ADMIN_PASSWORD is not configured")
    if data.password != ADMIN_PASSWORD: raise HTTPException(status_code=401, detail="Invalid password")
    token = secrets.token_urlsafe(32); admin_sessions.add(token)
    return {"success": True, "token": token}

@app.get("/admin/questions")
def admin_get_questions(authorization: str | None = Header(default=None)):
    verify_admin(authorization)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, question, type, required, options FROM questions ORDER BY id")
        return {"success": True, "data": [qdict(r) for r in cur.fetchall()]}
    finally:
        cur.close(); conn.close()

@app.post("/admin/questions")
def add_question(data: AddQuestion, authorization: str | None = Header(default=None)):
    verify_admin(authorization); options = validate_question(data)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO questions (question,type,required,options) VALUES (%s,%s,%s,%s) RETURNING id", (data.question.strip(), data.question_type, data.required, "|".join(options) if options else None))
        qid = cur.fetchone()[0]; conn.commit()
        return {"success": True, "message": "Question added successfully", "question_id": f"Q{qid}"}
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close(); conn.close()

@app.put("/admin/questions/{question_id}")
def update_question(question_id: str, data: AddQuestion, authorization: str | None = Header(default=None)):
    verify_admin(authorization)
    qid = question_id.strip().upper()
    if not qid.startswith("Q") or not qid[1:].isdigit(): raise HTTPException(status_code=400, detail="Invalid question ID")
    options = validate_question(data); dbid = int(qid[1:])
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE questions SET question=%s,type=%s,required=%s,options=%s WHERE id=%s", (data.question.strip(), data.question_type, data.required, "|".join(options) if options else None, dbid))
        if cur.rowcount == 0: raise HTTPException(status_code=404, detail="Question not found")
        conn.commit(); return {"success": True, "message": "Question updated successfully", "question_id": qid}
    except HTTPException: conn.rollback(); raise
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: cur.close(); conn.close()

@app.delete("/admin/questions/{question_id}")
def delete_question(question_id: str, authorization: str | None = Header(default=None)):
    verify_admin(authorization)
    qid = question_id.strip().upper()
    if not qid.startswith("Q") or not qid[1:].isdigit(): raise HTTPException(status_code=400, detail="Invalid question ID")
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM questions WHERE id=%s", (int(qid[1:]),))
        if cur.rowcount == 0: raise HTTPException(status_code=404, detail="Question not found")
        conn.commit(); return {"success": True, "message": "Question deleted successfully", "question_id": qid}
    except HTTPException: conn.rollback(); raise
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: cur.close(); conn.close()

@app.get("/admin/responses")
def admin_get_responses(authorization: str | None = Header(default=None)):
    verify_admin(authorization)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, submission_id, submitted_at, name, answers FROM responses ORDER BY id DESC")
        data=[]
        for r in cur.fetchall():
            try: answers=json.loads(r[4]) if r[4] else {}
            except Exception: answers=r[4]
            data.append({"id":r[0],"submission_id":r[1],"submitted_at":r[2].isoformat() if r[2] else None,"name":r[3],"answers":answers})
        return {"success": True, "data": data}
    finally:
        cur.close(); conn.close()
