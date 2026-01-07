<<<<<<< HEAD
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
=======
This project is a full-stack college website designed to provide a centralized web platform for managing and displaying academic and administrative information. The backend is developed using Flask (2.2.5), which handles routing, server-side logic, and communication with the database. The frontend is built using HTML and CSS, focusing on a clean and responsive user interface.

MongoDB is used as the primary database for storing application data such as user information, content records, and form submissions. Database operations are performed using PyMongo (4.4.0), and MongoDB Compass is used for visual database management and monitoring during development.

The application also integrates Flask-Mail (0.9.1) to support email-based features such as contact form submissions and system notifications. Sensitive configuration details, including database credentials and email settings, are securely managed using environment variables with python-dotenv (1.0.0). Werkzeug (2.2.3) is used internally for request handling and security-related utilities.

Overall, this project demonstrates practical full-stack web development concepts, including backend–frontend integration, database connectivity, form handling, and secure configuration management. It is intended for academic use and serves as a foundational project showcasing structured backend development using Flask.
>>>>>>> 6478a2ee6435de514e6771297ad378254b4b040a
