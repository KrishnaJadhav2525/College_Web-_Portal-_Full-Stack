    # CSD Website - Complete Flask Backend Added

This archive now contains a more complete Flask backend (app.py and db.py) implementing student signup/login, blog submission (approval workflow), contact form handling, admin panel endpoints (blogs, contacts, faculty, events, notifications, gallery, research), and local JSON database fallback.

Run locally:

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env to set SECRET_KEY and other vars
python run.py
```

Notes:
- Email sending is not fully configured; configure MAIL_USERNAME and MAIL_PASSWORD and integrate Flask-Mail if you want real emails.
- By default the app uses local database.json (created automatically).
