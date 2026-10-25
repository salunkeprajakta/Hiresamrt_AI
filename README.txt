HireSmartAI - Local Flask Project
=================================

FEATURES
- Candidate and recruiter registration/login
- Resume upload: PDF, DOCX, TXT
- Rule-based skill detection from extracted resume text
- Job listings with matched skills and a match percentage
- Working Apply Now button for logged-in candidates
- Recruiter can post jobs and view applicants
- SQLite database created automatically

REQUIREMENTS
- Python 3.10 or newer recommended
- Internet connection for first-time package installation

RUN ON WINDOWS / VS CODE
1. Extract this ZIP into a new folder.
2. Open the extracted HireSmartAI folder in VS Code.
3. Open Terminal > New Terminal.
4. (Optional but recommended) create a virtual environment:
   python -m venv .venv
   .venv\Scripts\activate
5. Install dependencies:
   python -m pip install -r requirements.txt
6. Start the app:
   python app.py
7. Open the local URL shown in the terminal, normally:
   http://127.0.0.1:5000

FIRST USE
1. Click Register.
2. Create a Candidate account to upload a resume and apply for jobs.
3. To post jobs, register a separate account and choose Recruiter.
4. Candidate: upload a text-based PDF, DOCX, or TXT resume.
5. Open Skills Analysis, then Jobs, then click Apply Now.

IMPORTANT
- This is a local student project prototype, not a production hiring system.
- Skills analysis is keyword-based, not a trained AI model.
- Scanned/image-only PDFs may not contain extractable text and need OCR.
- Data is stored in hiresmart.db in this folder; uploads are in uploads/.
- Keep the database and uploads folder when restarting the app.
- Do not deploy with the default SECRET_KEY or real candidate data.


ADDITIONAL FEATURES
- Candidate profile: create, view, and update profile
- Candidate dashboard application status notifications (Applied, Under Review, Selected, Rejected)
- Recruiter can view applicants and Select / Reject / mark Under Review
- Timed interview practice: choose 10 or 20 questions and 10 or 15 minutes
- Gemini question generation when configured; built-in fallback questions otherwise
- Answers are saved to a practice result page

OPTIONAL GEMINI SETUP (for AI-generated interview questions)
Windows PowerShell:
  $env:GEMINI_API_KEY="your_api_key_here"
  $env:GEMINI_MODEL="gemini-2.5-flash"
  python app.py

Set the key only in your local terminal/environment; do not share it or commit it to source control. Without a key, interview practice still works using built-in questions. Follow-up questions are not dynamically asked after each answer in this version; the question set is generated at interview start.
