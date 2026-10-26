import os
import re
import json
import sqlite3
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent

if os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp/hiresmart.db")
    UPLOAD_DIR = Path("/tmp/uploads")
else:
    DB_PATH = BASE_DIR / "hiresmart.db"
    UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-for-local-development")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

SKILLS = [
    "python", "java", "javascript", "typescript", "c++", "c#", "sql", "html", "css",
    "react", "node.js", "flask", "django", "mysql", "sqlite", "mongodb", "pandas",
    "numpy", "scikit-learn", "machine learning", "deep learning", "artificial intelligence",
    "tensorflow", "pytorch", "data analysis", "power bi", "excel", "git", "rest api",
    "communication", "problem solving", "data structures", "oop", "nlp", "opencv"
]

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('candidate','recruiter'))
    );
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY, phone TEXT DEFAULT '', location TEXT DEFAULT '',
        education TEXT DEFAULT '', skills TEXT DEFAULT '', summary TEXT DEFAULT '',
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recruiter_id INTEGER NOT NULL,
        title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
        description TEXT NOT NULL, required_skills TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(recruiter_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Applied',
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_id, candidate_id),
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS interviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL,
        job_id INTEGER, questions_json TEXT NOT NULL, answers_json TEXT DEFAULT '[]',
        question_count INTEGER NOT NULL, duration_minutes INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP,
        FOREIGN KEY(candidate_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
    );
    """)
    # Safe schema upgrades for databases created by earlier versions.
    app_cols = {r[1] for r in db.execute("PRAGMA table_info(applications)").fetchall()}
    if "status" not in app_cols:
        db.execute("ALTER TABLE applications ADD COLUMN status TEXT NOT NULL DEFAULT 'Applied'")
    if "updated_at" not in app_cols:
        db.execute("ALTER TABLE applications ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    # Provide demo jobs only when a recruiter exists and no jobs exist.
    if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0:
        recruiter = db.execute("SELECT id FROM users WHERE role='recruiter' ORDER BY id LIMIT 1").fetchone()
        if recruiter:
            db.executemany(
                "INSERT INTO jobs (recruiter_id,title,company,location,description,required_skills) VALUES (?,?,?,?,?,?)",
                [
                    (recruiter[0], "Python Developer Intern", "Demo Tech", "Pune", "Assist with Python web applications and testing.", "Python, Flask, SQL, Git"),
                    (recruiter[0], "Junior Data Analyst", "Demo Analytics", "Hybrid", "Clean datasets, create reports, and support analysis.", "Python, SQL, Excel, Pandas"),
                    (recruiter[0], "Frontend Developer Intern", "Demo Digital", "Remote", "Build and improve responsive web pages.", "HTML, CSS, JavaScript, React")
                ])
    db.commit()
    db.close()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_resume_text(path):
    try:
        if path.suffix.lower() == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if path.suffix.lower() == ".docx":
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs)
    except Exception:
        return ""
    return ""

def detect_skills(text):
    normalized = re.sub(r"[^a-z0-9+#.\s-]", " ", (text or "").lower())
    return [s for s in SKILLS if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", normalized)]

def job_skills(text):
    return [s.strip().lower() for s in (text or "").split(",") if s.strip()]

def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have permission to open that page.", "danger")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def generate_interview_questions(job, skills, count, duration):
    """Uses Gemini when GEMINI_API_KEY is configured; otherwise provides reliable demo questions."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    if api_key:
        try:
            import urllib.request
            prompt = (
                f"Create exactly {count} concise interview questions for a student candidate. "
                f"Role: {job['title'] if job else 'Entry-level general role'}. "
                f"Required skills: {job['required_skills'] if job else ', '.join(skills) or 'general fresher skills'}. "
                f"Candidate skills: {', '.join(skills) or 'not provided'}. "
                f"Total interview time is {duration} minutes. Include technical and behavioral questions. "
                "Return ONLY a JSON array of strings, no markdown."
            )
            body = json.dumps({"contents":[{"parts":[{"text":prompt}]}]}).encode()
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                data=body, headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode())
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            match = re.search(r"\[.*\]", raw, re.S)
            parsed = json.loads(match.group(0)) if match else []
            questions = [str(q).strip() for q in parsed if str(q).strip()]
            if len(questions) >= count:
                return questions[:count], "Gemini"
        except Exception:
            pass
    role = job["title"] if job else "the role you are applying for"
    pool = [
        f"Tell us about yourself and why you are interested in {role}.",
        "Describe a project you worked on and your contribution.",
        "Which technical skill are you most confident in, and why?",
        "Explain how you would debug a problem you have never seen before.",
        "Tell us about a time you learned a new tool or concept quickly.",
        "How do you approach working in a team when opinions differ?",
        "Describe a mistake you made and what you learned from it.",
        "How would you explain a technical concept to a non-technical person?",
        "What steps do you take to check the quality of your work?",
        "How do you prioritize tasks when you have a deadline?",
        "What would you like to learn in your first three months?",
        "Give an example of how you solved a difficult problem.",
        "How would you handle feedback on your work?",
        "What interests you about this company or role?",
        "What questions would you ask the hiring team?"
    ]
    if count <= len(pool):
        return pool[:count], "built-in fallback"
    return pool + [f"Tell us more about your experience with {role} (question {i})." for i in range(len(pool)+1, count+1)], "built-in fallback"

@app.route("/")
def index():
    jobs = get_db().execute("SELECT jobs.*, users.name AS recruiter_name FROM jobs JOIN users ON users.id=jobs.recruiter_id ORDER BY jobs.id DESC LIMIT 6").fetchall()
    return render_template("index.html", jobs=jobs)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "candidate")
        if not name or not email or len(password) < 6 or role not in ("candidate", "recruiter"):
            flash("Enter all details. Password must be at least 6 characters.", "danger")
            return render_template("register.html")
        try:
            db = get_db()
            cur = db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                             (name, email, generate_password_hash(password), role))
            if role == "candidate":
                db.execute("INSERT INTO profiles(user_id) VALUES(?)", (cur.lastrowid,))
            db.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("This email is already registered.", "danger")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"], session["name"], session["role"] = user["id"], user["name"], user["role"]
            flash("Welcome back, " + user["name"] + "!", "success")
            return redirect(url_for("dashboard"))
        flash("Incorrect email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required()
def dashboard():
    return redirect(url_for("recruiter_dashboard" if session["role"] == "recruiter" else "candidate_dashboard"))

@app.route("/candidate")
@login_required("candidate")
def candidate_dashboard():
    db = get_db()
    applications = db.execute("""SELECT applications.id, applications.status, applications.applied_at, applications.updated_at,
        jobs.title, jobs.company, jobs.location FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.candidate_id=? ORDER BY applications.id DESC""", (session["user_id"],)).fetchall()
    profile = db.execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    if not profile:
        db.execute("INSERT OR IGNORE INTO profiles(user_id) VALUES(?)", (session["user_id"],))
        db.commit()
        profile = db.execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    return render_template("candidate_dashboard.html", applications=applications, resume=session.get("resume_filename"),
                           skills=session.get("resume_skills", []), profile=profile)

@app.route("/profile", methods=["GET", "POST"])
@login_required("candidate")
def profile():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    prof = db.execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    if not prof:
        db.execute("INSERT OR IGNORE INTO profiles(user_id) VALUES(?)", (session["user_id"],))
        db.commit()
        prof = db.execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        if not name or not email:
            flash("Name and email are required.", "danger")
            return render_template("profile.html", user=user, profile=prof)
        try:
            db.execute("UPDATE users SET name=?, email=? WHERE id=?", (name, email, session["user_id"]))
            db.execute("""UPDATE profiles SET phone=?, location=?, education=?, skills=?, summary=? WHERE user_id=?""",
                (request.form.get("phone","").strip(), request.form.get("location","").strip(),
                 request.form.get("education","").strip(), request.form.get("skills","").strip(),
                 request.form.get("summary","").strip(), session["user_id"]))
            db.commit()
            session["name"] = name
            flash("Profile updated successfully.", "success")
            return redirect(url_for("view_profile"))
        except sqlite3.IntegrityError:
            flash("That email address is already in use.", "danger")
    return render_template("profile.html", user=user, profile=prof)

@app.route("/profile/view")
@login_required("candidate")
def view_profile():
    db = get_db()
    user = db.execute("SELECT name,email FROM users WHERE id=?", (session["user_id"],)).fetchone()
    prof = db.execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    return render_template("view_profile.html", user=user, profile=prof)

@app.route("/resume", methods=["GET", "POST"])
@login_required("candidate")
def resume():
    if request.method == "POST":
        file = request.files.get("resume")
        if not file or not file.filename:
            flash("Choose a resume file first.", "danger")
            return redirect(url_for("resume"))
        if not allowed_file(file.filename):
            flash("Allowed formats: PDF, DOCX, TXT.", "danger")
            return redirect(url_for("resume"))
        filename = secure_filename(file.filename)
        unique_name = f"{session['user_id']}_{filename}"
        path = UPLOAD_DIR / unique_name
        file.save(path)
        text_content = extract_resume_text(path)
        if not text_content.strip():
            flash("We could not read text from this file. Try a text-based PDF, DOCX, or TXT file.", "warning")
            return redirect(url_for("resume"))
        skills = detect_skills(text_content)
        session["resume_filename"], session["resume_skills"] = filename, skills
        session["resume_text"] = text_content[:20000]
        flash(f"Resume analyzed. Found {len(skills)} skills.", "success")
        return redirect(url_for("skills_analysis"))
    return render_template("resume.html", skills=SKILLS)

@app.route("/skills")
@login_required("candidate")
def skills_analysis():
    if not session.get("resume_filename"):
        flash("Upload your resume to analyze your skills.", "info")
        return redirect(url_for("resume"))
    return render_template("skills.html", skills=session.get("resume_skills", []), resume_filename=session.get("resume_filename"))

@app.route("/jobs")
def jobs():
    db = get_db()
    rows = db.execute("SELECT jobs.*, users.name AS recruiter_name FROM jobs JOIN users ON users.id=jobs.recruiter_id ORDER BY jobs.id DESC").fetchall()
    candidate_skills = session.get("resume_skills", []) if session.get("role") == "candidate" else []
    applied_ids = set()
    if session.get("role") == "candidate":
        applied_ids = {r["job_id"] for r in db.execute("SELECT job_id FROM applications WHERE candidate_id=?", (session["user_id"],)).fetchall()}
    job_list = []
    for row in rows:
        required = job_skills(row["required_skills"])
        matches = sorted(set(candidate_skills).intersection(required))
        score = round(len(matches) / len(set(required)) * 100) if required else 0
        job_list.append({"job": row, "required": required, "matches": matches, "score": score, "applied": row["id"] in applied_ids})
    return render_template("jobs.html", jobs=job_list)

@app.route("/apply/<int:job_id>", methods=["POST"])
@login_required("candidate")
def apply(job_id):
    db = get_db()
    if not db.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
        flash("This job no longer exists.", "danger")
        return redirect(url_for("jobs"))
    try:
        db.execute("INSERT INTO applications(job_id,candidate_id) VALUES(?,?)", (job_id, session["user_id"]))
        db.commit()
        flash("Application submitted successfully. You can track updates in your dashboard.", "success")
    except sqlite3.IntegrityError:
        flash("You have already applied for this job.", "info")
    return redirect(url_for("jobs"))

@app.route("/interview/setup", methods=["GET", "POST"])
@login_required("candidate")
def interview_setup():
    jobs = get_db().execute("SELECT id,title,company FROM jobs ORDER BY title").fetchall()
    if request.method == "POST":
        try:
            count = int(request.form.get("question_count", "10"))
            duration = int(request.form.get("duration", "10"))
            job_id = request.form.get("job_id") or None
            if count not in (10, 20) or duration not in (10, 15):
                raise ValueError()
            job = get_db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
            skills = session.get("resume_skills", [])
            questions, source = generate_interview_questions(job, skills, count, duration)
            cur = get_db().execute("INSERT INTO interviews(candidate_id,job_id,questions_json,question_count,duration_minutes) VALUES(?,?,?,?,?)",
                (session["user_id"], int(job_id) if job_id else None, json.dumps(questions), count, duration))
            get_db().commit()
            return redirect(url_for("take_interview", interview_id=cur.lastrowid))
        except (ValueError, TypeError):
            flash("Choose 10 or 20 questions and a 10 or 15 minute duration.", "danger")
    return render_template("interview_setup.html", jobs=jobs)

@app.route("/interview/<int:interview_id>", methods=["GET", "POST"])
@login_required("candidate")
def take_interview(interview_id):
    db = get_db()
    interview = db.execute("SELECT * FROM interviews WHERE id=? AND candidate_id=?", (interview_id, session["user_id"])).fetchone()
    if not interview:
        flash("Interview session not found.", "danger")
        return redirect(url_for("interview_setup"))
    questions = json.loads(interview["questions_json"])
    if request.method == "POST":
        answers = [request.form.get(f"answer_{i}", "").strip() for i in range(len(questions))]
        db.execute("UPDATE interviews SET answers_json=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                   (json.dumps(answers), interview_id))
        db.commit()
        flash("Interview answers saved successfully.", "success")
        return redirect(url_for("interview_result", interview_id=interview_id))
    return render_template("interview.html", interview=interview, questions=questions)

@app.route("/interview/<int:interview_id>/result")
@login_required("candidate")
def interview_result(interview_id):
    interview = get_db().execute("SELECT * FROM interviews WHERE id=? AND candidate_id=?", (interview_id, session["user_id"])).fetchone()
    if not interview:
        flash("Interview result not found.", "danger")
        return redirect(url_for("interview_setup"))
    questions, answers = json.loads(interview["questions_json"]), json.loads(interview["answers_json"] or "[]")
    answered = sum(1 for a in answers if a.strip())
    return render_template("interview_result.html", interview=interview, questions=questions, answers=answers, answered=answered)

@app.route("/recruiter")
@login_required("recruiter")
def recruiter_dashboard():
    db = get_db()
    jobs = db.execute("SELECT * FROM jobs WHERE recruiter_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    applicants = db.execute("""SELECT applications.id AS application_id, applications.status, applications.applied_at,
        users.id AS candidate_id, users.name AS candidate_name, users.email, jobs.title AS job_title,
        jobs.company, jobs.id AS job_id FROM applications JOIN users ON users.id=applications.candidate_id
        JOIN jobs ON jobs.id=applications.job_id WHERE jobs.recruiter_id=? ORDER BY applications.id DESC""",
        (session["user_id"],)).fetchall()
    return render_template("recruiter_dashboard.html", jobs=jobs, applicants=applicants)

@app.route("/jobs/new", methods=["GET", "POST"])
@login_required("recruiter")
def new_job():
    if request.method == "POST":
        fields = [request.form.get(k, "").strip() for k in ("title","company","location","description","required_skills")]
        if not all(fields):
            flash("Please fill in every field.", "danger")
        else:
            get_db().execute("INSERT INTO jobs(recruiter_id,title,company,location,description,required_skills) VALUES(?,?,?,?,?,?)",
                (session["user_id"], *fields))
            get_db().commit()
            flash("Job posted successfully.", "success")
            return redirect(url_for("recruiter_dashboard"))
    return render_template("new_job.html")

@app.route("/application/<int:application_id>/<decision>", methods=["POST"])
@login_required("recruiter")
def update_application(application_id, decision):
    statuses = {"select": "Selected", "reject": "Rejected", "review": "Under Review"}
    if decision not in statuses:
        flash("Invalid application action.", "danger")
        return redirect(url_for("recruiter_dashboard"))
    db = get_db()
    application = db.execute("""SELECT applications.id FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=? AND jobs.recruiter_id=?""", (application_id, session["user_id"])).fetchone()
    if not application:
        flash("Application not found or not assigned to your jobs.", "danger")
    else:
        db.execute("UPDATE applications SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (statuses[decision], application_id))
        db.commit()
        flash("Candidate status updated to " + statuses[decision] + ".", "success")
    return redirect(url_for("recruiter_dashboard"))

@app.errorhandler(413)
def too_large(error):
    flash("File is too large. Maximum size is 8 MB.", "danger")
    return redirect(url_for("resume"))

@app.route("/health")
def health():
    return {"status": "ok", "app": "HireSmartAI"}

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)

