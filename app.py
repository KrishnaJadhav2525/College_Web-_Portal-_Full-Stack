
import os, uuid, random
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_from_directory, flash, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from db import Database
from dotenv import load_dotenv
from flask_mail import Mail, Message
from functools import wraps
import datetime as dt


load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY','dev-secret-key')
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ALLOWED_IMG = {'png','jpg','jpeg','gif'}
ALLOWED_DOC = {'pdf','doc','docx'}

# Mail config
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER','smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS','true').lower() in ('1','true','yes')
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL','false').lower() in ('1','true','yes')
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME','')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD','')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])

mail = Mail(app)

USE_MONGODB = os.environ.get('USE_MONGODB','false').lower() == 'true'
MONGO_URI = os.environ.get('MONGO_URI','')
db = Database(use_mongo=USE_MONGODB, mongo_uri=MONGO_URI)

ADMIN_USER = os.environ.get('ADMIN_USER','admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS','admin123')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL','admin@example.com')

def allowed_file(filename, allowed_set):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in allowed_set

def login_required_any(fn):
    """Require either student OR faculty to be logged in."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('student') and not session.get('faculty'):
            # not logged in → send to home (or you can redirect to login modal)
            flash('Please login to access your profile.', 'error')
            return redirect(url_for('home'))
        return fn(*args, **kwargs)
    return wrapper

# ---------- PUBLIC PAGES ----------
# ---------- GLOBAL: expose current user (student/faculty) to all templates ----------

@app.context_processor
def inject_current_user():
    """Make current logged-in student or faculty available in all templates."""
    stu = session.get('student')
    fac = session.get('faculty')

    role = None
    user = None
    if stu:
        role = 'student'
        user = stu
    elif fac:
        role = 'faculty'
        user = fac

    return {
        'current_user': user,
        'current_user_role': role
    }
def _get_user_by_id(role, user_id):
    """Return full user dict (student or faculty) from DB by id."""
    if role == 'student':
        if db.use_mongo:
            return db.db.students.find_one({'id': user_id})
        data = db._read()
        return next((s for s in data.get('students', []) if s.get('id') == user_id), None)
    else:
        if db.use_mongo:
            return db.db.faculty.find_one({'id': user_id})
        data = db._read()
        return next((f for f in data.get('faculty', []) if f.get('id') == user_id), None)

@app.route('/', endpoint='home')
def home():
    return render_template('home.html')

@app.route('/about', endpoint='about')
def about():
    # Public about page includes dynamic faculty list
    faculty_list = db.list_faculty()
    faculty_objs = []
    for f in faculty_list:
        faculty_objs.append(type("FacObj",(object,),f)())

    # Infrastructure images come from gallery with category='infrastructure'
    infra_items = []
    try:
        gal_items = db.list_gallery()
    except Exception:
        gal_items = []
    for g in gal_items:
        if g.get('category') != 'infrastructure':
            continue
        img = g.get('image') or g.get('file') or g.get('path')
        if img and not str(img).startswith('http'):
            img = '/uploads/' + img if not str(img).startswith('/uploads/') else img
        infra_items.append(type("InfraObj",(object,),{'image': img})())

    return render_template('about.html', faculty=faculty_objs, infra=infra_items)

@app.route('/blog', endpoint='blog')
def blog():
    """Public blog listing showing all approved posts with summary info."""
    raw_blogs = db.list_blogs(approved_only=True)
    posts = []

    for b in raw_blogs:
        created_at = b.get('created_at')
        if isinstance(created_at, str):
            try:
                created_at_dt = dt.datetime.fromisoformat(created_at)
            except Exception:
                created_at_dt = None
        else:
            created_at_dt = created_at

        file_path = b.get('file_path')
        file_link = b.get('file_link')
        file_url = file_path or file_link
        file_type = b.get('file_type')

        if not file_type and file_url:
            lower = str(file_url).lower()
            if lower.endswith('.pdf'):
                file_type = 'pdf'
            elif any(lower.endswith(f'.{e}') for e in ['jpg','jpeg','png','gif','webp','svg']):
                file_type = 'image'
            else:
                file_type = 'file'

        likes = b.get('likes') or []
        comments = b.get('comments') or []

        posts.append({
            'id': b.get('id'),
            'title': b.get('title'),
            'content': b.get('content',''),
            'author_name': b.get('author_name') or b.get('student_name') or 'Anonymous',
            'author_type': b.get('author_type') or ('student' if b.get('student_id') else 'faculty'),
            'student_id': b.get('student_id'),
            'file_link': file_link,
            'file_path': file_path,
            'file_url': file_url,
            'file_type': file_type,
            'like_count': len(likes),
            'comment_count': len(comments),
            'created_at': created_at_dt,
        })
    return render_template('blog.html', posts=posts)


@app.route('/blog/<blog_id>', endpoint='blog_detail')
def blog_detail(blog_id):
    """Detail view for a single approved blog post with comments."""
    b = db.get_blog(blog_id)
    if not b or not b.get('approved', False):
        abort(404)

    created_at = b.get('created_at')
    if isinstance(created_at, str):
        try:
            created_at_dt = dt.datetime.fromisoformat(created_at)
        except Exception:
            created_at_dt = None
    else:
        created_at_dt = created_at

    file_path = b.get('file_path')
    file_link = b.get('file_link')
    file_url = file_path or file_link
    file_type = b.get('file_type')
    if not file_type and file_url:
        lower = str(file_url).lower()
        if lower.endswith('.pdf'):
            file_type = 'pdf'
        elif any(lower.endswith(f'.{e}') for e in ['jpg','jpeg','png','gif','webp','svg']):
            file_type = 'image'
        else:
            file_type = 'file'

    likes = b.get('likes') or []
    comments = b.get('comments') or []

    # Determine if current user liked this post
    like_key = None
    user_label = None
    stu = session.get('student')
    fac = session.get('faculty')
    if stu:
        like_key = f"student:{stu.get('student_id')}"
        user_label = f"{stu.get('name')} ({stu.get('student_id')})"
    elif fac:
        like_key = f"faculty:{fac.get('email')}"
        user_label = f"{fac.get('name')} (Faculty)"

    liked = like_key in likes if like_key else False

    # Wrap comments as simple objects
    comment_objs = []
    for c in comments:
        c_dt = c.get('created_at')
        if isinstance(c_dt, str):
            try:
                c_dt = datetime.datetime.fromisoformat(c_dt)
            except Exception:
                c_dt = None
        comment_objs.append(type("CommentObj",(object,),{**c, "created_at": c_dt})())

    post = type("PostObj",(object,),{
        "id": b.get("id"),
        "title": b.get("title"),
        "content": b.get("content",""),
        "author_name": b.get("author_name") or b.get("student_name") or "Anonymous",
        "author_type": b.get("author_type") or ("student" if b.get("student_id") else "faculty"),
        "student_id": b.get("student_id"),
        "file_url": file_url,
        "file_type": file_type,
        "created_at": created_at_dt,
        "like_count": len(likes),
        "comment_count": len(comments),
    })()

    return render_template('blog_detail.html', post=post, comments=comment_objs, liked=liked, user_label=user_label)


@app.route('/api/blog/<blog_id>/like', methods=['POST'])
def api_blog_like(blog_id):
    """Toggle like for current logged-in user on a blog post."""
    stu = session.get('student')
    fac = session.get('faculty')
    if not stu and not fac:
        return jsonify({'success': False, 'message': 'Please login to like posts.'}), 401

    if stu:
        like_key = f"student:{stu.get('student_id')}"
    else:
        like_key = f"faculty:{fac.get('email')}"

    b = db.get_blog(blog_id)
    if not b or not b.get('approved', False):
        return jsonify({'success': False, 'message': 'Post not found.'}), 404

    likes = b.get('likes') or []
    if like_key in likes:
        likes.remove(like_key)
        liked = False
    else:
        likes.append(like_key)
        liked = True

    db.update_blog(blog_id, {'likes': likes})
    return jsonify({'success': True, 'liked': liked, 'like_count': len(likes)})


@app.route('/api/blog/<blog_id>/comment', methods=['POST'])
def api_blog_comment(blog_id):
    """Add a comment from the current user to a blog post."""
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()

    if not text:
        return jsonify({'success': False, 'message': 'Comment text is required.'}), 400

    stu = session.get('student')
    fac = session.get('faculty')
    if not stu and not fac:
        return jsonify({'success': False, 'message': 'Please login to comment.'}), 401

    if stu:
        author_name = stu.get('name')
        author_type = 'student'
    else:
        author_name = fac.get('name')
        author_type = 'faculty'

    b = db.get_blog(blog_id)
    if not b or not b.get('approved', False):
        return jsonify({'success': False, 'message': 'Post not found.'}), 404

    comments = b.get('comments') or []
    comment = {
        'id': str(uuid.uuid4()),
        'author_name': author_name,
        'author_type': author_type,
        'text': text,
        'created_at': dt.datetime.utcnow().isoformat()
    }
    comments.append(comment)
    db.update_blog(blog_id, {'comments': comments})
    return jsonify({'success': True, 'comment': comment})

@app.route('/csa', endpoint='csa')
def csa_page():
    """Public CSA page showing *current* CSA members and past CSA PDFs/events."""
    csa_members = db.list_csa_members() or []
    past_csa = db.list_past_csa() or []
    csa_events = db.list_events() if hasattr(db, 'list_events') else []

    # Build list of current members only (default to current if flag missing)
    current_members = []
    for m in csa_members:
        try:
            is_current = m.get('is_current', True)
        except AttributeError:
            # If mongo returns objects, coerce to dict
            m = dict(m)
            is_current = m.get('is_current', True)
        if is_current:
            # Convert to simple object so template can use dot-notation
            current_members.append(type("MemObj", (object,), dict(m))())

    # Sort by 'order' field if present
    current_members.sort(key=lambda m: getattr(m, 'order', 0))

    return render_template(
        'csa.html',
        current_members=current_members,
        past_csa=past_csa,
        csa_events=csa_events
    )


@app.route('/events', endpoint='events')
def events_page():
    events_raw = db.list_events()
    upcoming, past = [], []
    now = dt.datetime.now()
    for e in events_raw:
        date_val = e.get('date')
        if isinstance(date_val, str):
            dt_obj = None
            for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
                try:
                    dt_obj = dt.datetime.strptime(date_val, fmt)
                    break
                except Exception:
                    continue
            e['date'] = dt_obj
        elif isinstance(date_val, dt.datetime):
            dt_obj = date_val
        else:
            dt_obj = None
            e['date'] = None
        if dt_obj and dt_obj >= now:
            upcoming.append(e)
        else:
            past.append(e)
    upcoming.sort(key=lambda x: x.get('date') or now)
    past.sort(key=lambda x: x.get('date') or now, reverse=True)
    return render_template('events.html', upcoming=upcoming, past=past)

@app.route('/gallery', endpoint='gallery')
def gallery_page():
    all_items = db.list_gallery() or []

    def normalize_img(g):
        im = g.get('image') or g.get('file') or g.get('path')
        if im and not str(im).startswith('http'):
            im = '/uploads/' + im if not str(im).startswith('/uploads/') else im
        return im

    events_slider = []
    events_cards = []
    tour_slider = []
    tour_cards = []

    for g in all_items:
        cat = (g.get('category') or '').strip()
        obj = dict(g)
        obj['image'] = normalize_img(g)

        # -------- GALLERY PAGE: EVENTS --------
        # big slider
        if cat in ('events_gallery_slider',):
            events_slider.append(obj)
        # cards under slider
        elif cat in ('events_gallery_cards',):
            events_cards.append(obj)

        # -------- GALLERY PAGE: INDUSTRIAL TOUR --------
        # big slider
        elif cat in ('industrial_slider', 'industrial_tour_slider', 'industrial_tour'):
            tour_slider.append(obj)
        # cards under slider
        elif cat in ('industrial_cards', 'industrial_tour_cards'):
            tour_cards.append(obj)

    # Wrap as simple objects for template (dot notation)
    wrap = lambda lst: [type("Obj", (object,), x)() for x in lst]

    return render_template(
        'gallery.html',
        events_slider=wrap(events_slider),
        events_cards=wrap(events_cards),
        tour_slider=wrap(tour_slider),
        tour_cards=wrap(tour_cards),
    )



@app.route('/research', endpoint='research')

def research_page():
    """Public research listing used from home-page section and dedicated page."""
    raw = db.list_research()
    papers = []
    for p in raw:
        # Normalize date
        date_val = p.get('date')
        if isinstance(date_val, str):
            try:
                date_dt = dt.datetime.fromisoformat(date_val)
            except Exception:
                date_dt = None
        else:
            date_dt = date_val

        # Build file / link URLs for template
        pdf_path = p.get('pdf_path') or ''
        pdf_link = p.get('pdf_link') or ''
        file_url = None
        if pdf_path:
            # Stored value is like "/uploads/<file>", we want /static/uploads/<file>
            file_url = url_for('static', filename=str(pdf_path).lstrip('/'))

        # Prefer explicit pdf_link, fall back to uploaded file URL
        link_url = pdf_link or file_url

        paper_obj = dict(p)
        paper_obj['date'] = date_dt
        # These two are used by the research.html template
        paper_obj['file'] = file_url
        paper_obj['link'] = link_url
        papers.append(type("PaperObj", (object,), paper_obj)())
    return render_template('research.html', research_papers=papers)

@app.route('/contact', endpoint='contact')
def contact_page():
    return render_template('contact.html')
# ---------- PROFILE PAGES (student + faculty) ----------

# ---------- UNIFIED USER PROFILE (STUDENT + FACULTY) ----------

def _current_user_and_role():
    """Return (role, full_user_dict) or (None, None) if not logged in."""
    stu = session.get("student")
    fac = session.get("faculty")

    if stu:
        email = stu.get("email")
        db_student = db.find_student_by_email(email)
        if not db_student:
            return None, None
        return "student", db_student

    if fac:
        email = fac.get("email")
        faculty_list = db.list_faculty() or []
        db_fac = next((f for f in faculty_list if f.get("email") == email), None)
        if not db_fac:
            return None, None
        return "faculty", db_fac

    return None, None


@app.route("/profile", endpoint="profile")
def profile():
    """Single profile page for both students and faculty."""
    role, user = _current_user_and_role()
    if not role or not user:
        # not logged in or user not found -> back to home
        return redirect(url_for("home"))

    # Get all blogs (approved + pending) to compute stats
    all_blogs = db.list_blogs(approved_only=False) or []

    # My own posts
    if role == "student":
        my_posts = [
            b for b in all_blogs
            if b.get("student_id") == user.get("student_id")
        ]
        like_key = f"student:{user.get('student_id')}"
    else:
        my_posts = [
            b for b in all_blogs
            if (b.get("author_email") or "").lower()
               == (user.get("email") or "").lower()
        ]
        like_key = f"faculty:{user.get('email')}"

    # Likes given + comments made
    like_count = 0
    comment_count = 0
    user_name = user.get("name")

    for b in all_blogs:
        likes = b.get("likes") or []
        comments = b.get("comments") or []

        if like_key in likes:
            like_count += 1

        for c in comments:
            if c.get("author_name") == user_name:
                comment_count += 1

    stats = {
        "posts": len(my_posts),
        "likes": like_count,
        "comments": comment_count,
    }

    # simple object so Jinja can do user.name
    user_obj = type("UserObj", (object,), user)()

    return render_template(
        "profile.html",
        user=user_obj,
        role=role,
        stats=stats,
        my_posts=my_posts,
    )


# Keep old endpoints working for navbar links
@app.route("/student/profile", endpoint="student_profile")
def student_profile():
    return profile()


@app.route("/faculty/profile", endpoint="faculty_profile")
def faculty_profile():
    return profile()


# ---------- EDIT PROFILE (BASIC FIELDS ONLY) ----------

@app.route('/profile/edit', methods=['GET', 'POST'], endpoint='edit_profile')
def edit_profile():
    # 1) Who is logged in?
    user_role = None
    session_user = None
    if session.get('student'):
        user_role = 'student'
        session_user = session['student']
    elif session.get('faculty'):
        user_role = 'faculty'
        session_user = session['faculty']
    else:
        return redirect(url_for('home'))

    user_id = session_user.get('id')
    user = _get_user_by_id(user_role, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('home'))

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        new_password = (request.form.get('password') or '').strip()
        new_password2 = (request.form.get('password2') or '').strip()

        # 🔹 NEW: only for students – read class from form
        student_class = None
        if user_role == 'student':
            student_class = (request.form.get('student_class') or '').strip()

        changes = {
            'name': name,
            'phone': phone,
        }

        # 🔹 If student selected a class, update it in DB
        if user_role == 'student' and student_class:
            changes['class'] = student_class

        # Faculty extra fields
        if user_role == 'faculty':
            changes['designation'] = (request.form.get('designation') or '').strip()
            changes['specialization'] = (request.form.get('specialization') or '').strip()
            changes['experience'] = (request.form.get('experience') or '').strip()

        # Handle password change
        if new_password:
            if new_password != new_password2:
                flash('New password and confirm password do not match.', 'error')
                return redirect(url_for('edit_profile'))
            changes['password_hash'] = generate_password_hash(new_password)

        # Handle avatar upload
        avatar_file = request.files.get('avatar')
        if avatar_file and allowed_file(avatar_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + avatar_file.filename)
            avatar_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            avatar_path = '/uploads/' + filename
            changes['avatar'] = avatar_path

        # Persist to DB
        if user_role == 'student':
            db.update_student(user_id, changes)
        else:
            db.update_faculty(user_id, changes)

        # 🔹 Update session copy so navbar/profile use latest data
        if user_role == 'student':
            session['student']['name'] = name
            session['student']['phone'] = phone
            if student_class:                      # only overwrite if provided
                session['student']['class'] = student_class
            if 'avatar' in changes:
                session['student']['avatar'] = changes['avatar']
        else:
            session['faculty']['name'] = name
            session['faculty']['phone'] = phone
            session['faculty']['designation'] = changes.get('designation')
            if 'avatar' in changes:
                session['faculty']['avatar'] = changes['avatar']

        flash('Profile updated successfully.', 'success')
        return redirect(url_for('profile'))

    # GET → show form
    user_obj = type("UserObj", (object,), user)()
    return render_template('edit_profile.html', user=user_obj, role=user_role)






@app.route('/my-posts', endpoint='my_posts')
@login_required_any
def my_posts_page():
    """Show all blog posts created by the current user."""
    stu = session.get('student')
    fac = session.get('faculty')

    is_student = bool(stu)
    if is_student:
        key_id = stu.get('student_id')
    else:
        key_email = fac.get('email')

    all_blogs = db.list_blogs(approved_only=False)
    my_posts = []
    for b in all_blogs:
        if is_student and b.get('student_id') == key_id:
            my_posts.append(b)
        elif (not is_student and
              b.get('author_type') == 'faculty' and
              b.get('author_email') == key_email):
            my_posts.append(b)

    decorated = []
    for b in my_posts:
        created = b.get('created_at')
        if isinstance(created, str):
            try:
                created_dt = dt.datetime.fromisoformat(created)
            except Exception:
                created_dt = None
        else:
            created_dt = created

        status_val = b.get('status') or ('approved' if b.get('approved') else 'pending')

        decorated.append(type("BlogObj", (object,), {
            '_id': b.get('id'),
            'title': b.get('title'),
            'status': status_val,
            'created_at': created_dt
        })())

    return render_template('my_posts.html', posts=decorated)


@app.route('/my-activity', endpoint='my_activity')
@login_required_any
def my_activity_page():
    """Show posts liked and commented by the current user."""
    stu = session.get('student')
    fac = session.get('faculty')

    is_student = bool(stu)
    if is_student:
        identifier = stu.get('student_id')
        like_key = f"student:{identifier}"
        display_name = stu.get('name')
    else:
        identifier = fac.get('email')
        like_key = f"faculty:{identifier}"
        display_name = fac.get('name')

    all_blogs = db.list_blogs(approved_only=False)

    liked_posts = []
    commented_posts = []
    commented_ids = set()

    for b in all_blogs:
        likes = b.get('likes') or []
        if like_key in likes:
            liked_posts.append(b)

        comments = b.get('comments') or []
        for c in comments:
            if c.get('author_name') == display_name and b.get('id') not in commented_ids:
                commented_posts.append(b)
                commented_ids.add(b.get('id'))
                break

    def wrap_blog_list(lst):
        result = []
        for b in lst:
            created = b.get('created_at')
            if isinstance(created, str):
                try:
                    created_dt = dt.datetime.fromisoformat(created)
                except Exception:
                    created_dt = None
            else:
                created_dt = created

            result.append(type("BlogObj", (object,), {
                '_id': b.get('id'),
                'title': b.get('title'),
                'author_name': b.get('author_name') or b.get('student_name') or 'Anonymous',
                'created_at': created_dt
            })())
        return result

    liked_wrapped = wrap_blog_list(liked_posts)
    commented_wrapped = wrap_blog_list(commented_posts)

    return render_template('my_activity.html',
                           liked_posts=liked_wrapped,
                           commented_posts=commented_wrapped)

# ---------- STUDENT AUTH & BLOG APIs ----------

@app.route('/api/student/signup', methods=['POST'])
def api_student_signup():
    data = request.get_json(silent=True) or request.form
    name = data.get('name', '').strip()
    student_id = data.get('student_id', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')

    # ✅ NEW: get class from request
    student_class = data.get('student_class', '').strip()

    # ✅ UPDATED: class is also required
    if not (name and student_id and email and password and student_class):
        return jsonify({'success': False, 'message': 'All fields are required (including class).'}), 400

    if db.find_student_by_student_id(student_id):
        return jsonify({'success': False, 'message': 'Student ID already registered.'}), 400
    if db.find_student_by_email(email):
        return jsonify({'success': False, 'message': 'Email already registered.'}), 400

    student = {
        'id': str(uuid.uuid4()),
        'name': name,
        'student_id': student_id,
        'email': email,
        'password_hash': generate_password_hash(password),
        'is_active': True,
        'created_at': dt.datetime.utcnow().isoformat(),

        # ✅ NEW: save class in DB
        'class': student_class
    }
    db.add_student(student)

    public_student = {
        'id': student['id'],
        'name': student['name'],
        'student_id': student['student_id'],
        'email': student['email'],

        # ✅ NEW: also return class in response
        'class': student.get('class')
    }
    return jsonify({'success': True, 'message': 'Registration successful!', 'student': public_student})

@app.route('/api/student/login', methods=['POST'])
def api_student_login():
    """Student login with email + password."""
    try:
        data = request.get_json(silent=True) or request.form
        email = (data.get('email') or '').strip()
        password = (data.get('password') or '').strip()

        if not (email and password):
            return jsonify({'success': False, 'message': 'Email and password are required.'}), 400

        student = db.find_student_by_email(email)
        if not student:
            return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401

        if not student.get('is_active', True):
            return jsonify({'success': False, 'message': 'Account is inactive. Contact admin.'}), 403

        # Safely check password hash so bad / old hashes don't crash the app
        stored_hash = student.get('password_hash')
        valid_password = False
        if stored_hash:
            try:
                valid_password = check_password_hash(stored_hash, password)
            except Exception:
                # If hash is broken or in old format, treat as invalid password
                valid_password = False

        if not valid_password:
            return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401

        # Build session object (include avatar if present)
        session['student'] = {
    'id': student['id'],
    'name': student['name'],
    'student_id': student['student_id'],
    'email': student['email'],
    'phone': student.get('phone'),
    'class': student.get('class'),   # 👈 this must exist
    'avatar': student.get('avatar') or student.get('profile_image'),
}


        return jsonify({'success': True, 'message': 'Login successful', 'student': session['student']})

    except Exception as e:
        # Log server-side, but always return JSON to the browser
        app.logger.exception("Student login failed: %s", e)
        return jsonify({'success': False, 'message': 'Server error while logging in. Please try again.'}), 500



@app.route('/api/student/request-otp', methods=['POST'])
def api_student_request_otp():
    """Start email OTP login for students (secure, time-limited)."""
    data = request.get_json(silent=True) or request.form
    email = (data.get('email') or '').strip()

    if not email:
        return jsonify({'success': False, 'message': 'Email is required.'}), 400

    student = db.find_student_by_email(email)
    if not student:
        return jsonify({'success': False, 'message': 'No student account found for this email.'}), 404
    if not student.get('is_active', True):
        return jsonify({'success': False, 'message': 'Account is inactive. Contact admin.'}), 403

    all_data = db._read()
    for s in all_data.get('students', []):
        if s.get('email') == email:
            otp_code = f"{random.randint(0, 999999):06d}"
            expires_at = dt.datetime.utcnow() + dt.datetime.timedelta(minutes=10)
            s['otp_code'] = otp_code
            s['otp_expires_at'] = expires_at.isoformat()
            db._write(all_data)

            otp_debug = otp_code
            # Try to send email if mail configured
            try:
                if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD') and email:
                    msg = Message(subject="Your student login OTP", recipients=[email])
                    msg.body = f"Your OTP is: {otp_code}\nIt will expire in 10 minutes."
                    mail.send(msg)
                    otp_debug = None
            except Exception as e:
                app.logger.error("Failed to send student OTP email: %s", e)

            return jsonify({
                'success': True,
                'message': 'OTP sent to your email. Please verify.',
                'otp_debug': otp_debug
            })

    return jsonify({'success': False, 'message': 'Unexpected error.'}), 500


@app.route('/api/student/verify-otp', methods=['POST'])
def api_student_verify_otp():
    """Verify student email OTP and create a logged-in session."""
    data = request.get_json(silent=True) or request.form
    email = (data.get('email') or '').strip()
    otp = (data.get('otp') or '').strip()

    if not (email and otp):
        return jsonify({'success': False, 'message': 'Email and OTP are required.'}), 400

    all_data = db._read()
    target = None
    for s in all_data.get('students', []):
        if s.get('email') == email:
            target = s
            break

    if not target:
        return jsonify({'success': False, 'message': 'Student not found.'}), 404

    stored_otp = target.get('otp_code')
    expires_at = target.get('otp_expires_at')

    if not stored_otp or stored_otp != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP.'}), 401

    # Check expiry
    if expires_at:
        try:
            exp_dt = dt.datetime.fromisoformat(expires_at)
            if exp_dt < dt.datetime.utcnow():
                return jsonify({'success': False, 'message': 'OTP has expired.'}), 400
        except Exception:
            pass

    # Create student session
        student_session = {
        'id': target.get('id'),
        'name': target.get('name'),
        'student_id': target.get('student_id'),
        'email': target.get('email'),
        'phone': target.get('phone'),
        'avatar': target.get('avatar') or target.get('profile_image'),
    }

    session['student'] = student_session

    # Clear OTP to prevent reuse
    target['otp_code'] = None
    target['otp_expires_at'] = None
    db._write(all_data)

    return jsonify({'success': True, 'message': 'Login successful.', 'student': student_session})
@app.route('/api/student/logout', methods=['POST'])
def api_student_logout():
    session.pop('student', None)
    return jsonify({'success': True})

@app.route('/api/student/check-session')
def api_student_check_session():
    stu = session.get('student')
    if stu:
        return jsonify({'logged_in': True, 'student': stu})
    return jsonify({'logged_in': False})

@app.route('/api/blog/post', methods=['POST'])
def api_blog_post():
    """Submit a blog post from either a logged-in student or faculty member."""
    stu = session.get('student')
    fac = session.get('faculty')

    if not stu and not fac:
        return jsonify({'success': False, 'message': 'Please login (student or faculty) to submit a blog.'}), 401

    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    file_link = (request.form.get('file_link') or '').strip()
    if not (title and content):
        return jsonify({'success': False, 'message': 'Title and content are required.'}), 400

    # Determine author information
    if stu:
        author_type = 'student'
        author_name = stu.get('name')
        student_id = stu.get('student_id')
        author_email = stu.get('email')
    else:
        author_type = 'faculty'
        author_name = fac.get('name')
        student_id = None
        author_email = fac.get('email')

    # Handle optional file upload
    file_path = None
    upload_file = request.files.get('file')
    file_type = None

    if upload_file and allowed_file(upload_file.filename, ALLOWED_IMG | ALLOWED_DOC):
        filename = secure_filename(str(uuid.uuid4()) + "-" + upload_file.filename)
        upload_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        file_path = '/uploads/' + filename
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            file_type = 'pdf'
        elif ext in ALLOWED_IMG:
            file_type = 'image'
        else:
            file_type = 'file'

    # If only an external link is provided, try to guess its type
    if not file_type and file_link:
        lower = file_link.lower()
        if lower.endswith('.pdf'):
            file_type = 'pdf'
        elif any(lower.endswith(f'.{e}') for e in ['jpg','jpeg','png','gif','webp','svg']):
            file_type = 'image'
        else:
            file_type = 'link'

    blog = {
        'id': str(uuid.uuid4()),
        'title': title,
        'content': content,
        'author_name': author_name,
        'author_type': author_type,
        'student_id': student_id,
        'author_class': user.get('class'),
        'author_email': author_email,
        'file_link': file_link or None,
        'file_path': file_path,
        'file_type': file_type,
        'status': 'pending',
        'approved': False,
        'likes': [],
        'comments': [],
        'created_at': dt.datetime.utcnow().isoformat()
    }
    db.add_blog(blog)

    # ---------- NEW: notify admin by email about pending blog ----------
    try:
        if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD') and ADMIN_EMAIL:
            msg = Message(
                subject=f"New blog post pending approval: {title}",
                recipients=[ADMIN_EMAIL]
            )
            msg.body = (
                f"A new blog post has been submitted and is pending approval.\n\n"
                f"Title: {title}\n"
                f"Author: {author_name} ({author_type})\n"
                f"Author email: {author_email or 'N/A'}\n\n"
                f"Preview:\n{content[:300]}...\n\n"
                "Log in to the admin panel to review and approve:\n"
                "URL: /admin/blogs?status=pending"
            )
            mail.send(msg)
    except Exception as e:
        app.logger.error("Failed to send blog notification email: %s", e)
    # ---------------------------------------------------------------

    return jsonify({'success': True, 'message': 'Blog submitted for approval.'})


@app.route('/api/blogs')
def api_blogs():
    blogs = db.list_blogs(approved_only=True)
    return jsonify(blogs)

# ---------- FACULTY LOGIN WITH EMAIL + PASSWORD + OTP ----------

@app.route('/api/faculty/login', methods=['POST'])
def api_faculty_login():
    """Faculty login with email + password (works with JSON or MongoDB)."""
    data = request.get_json(silent=True) or request.form
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()

    if not (email and password):
        return jsonify({'success': False, 'message': 'Email and password are required.'}), 400

    faculty = None
    json_data = None
    faculty_list = None

    # --- Look up faculty by email ---
    if getattr(db, "use_mongo", False):
        # Using MongoDB backend
        faculty = db.db.faculty.find_one({'email': email})
    else:
        # Using JSON file backend (database.json)
        json_data = db._read()
        faculty_list = json_data.get('faculty', [])
        for f in faculty_list:
            if f.get('email') == email:
                faculty = f
                break

    if not faculty:
        return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401

    # Treat missing is_active as True
    if not faculty.get('is_active', True):
        return jsonify({'success': False, 'message': 'Account is inactive. Contact admin.'}), 403

    stored_hash = faculty.get('password_hash')

    # --- First-time login: set password if no hash stored yet ---
    if not stored_hash:
        new_hash = generate_password_hash(password)
        if getattr(db, "use_mongo", False):
            db.db.faculty.update_one({'id': faculty.get('id')}, {'$set': {'password_hash': new_hash}})
        else:
            for f in faculty_list:
                if f.get('email') == email:
                    f['password_hash'] = new_hash
                    break
            json_data['faculty'] = faculty_list
            db._write(json_data)
        stored_hash = new_hash

    # --- Verify password ---
    if not check_password_hash(stored_hash, password):
        return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401

    # --- Build session object (used in navbar + profile) ---
    faculty_session = {
        'id': faculty.get('id'),
        'name': faculty.get('name'),
        'email': faculty.get('email'),
        'designation': faculty.get('designation'),
        'phone': faculty.get('phone'),
        'avatar': faculty.get('avatar') or faculty.get('profile_image'),
    }
    session['faculty'] = faculty_session
    session.pop('student', None)  # ensure not both at once

    return jsonify({'success': True, 'message': 'Faculty login successful.', 'faculty': faculty_session})



@app.route('/api/faculty/signup', methods=['POST'])
def api_faculty_signup():
    """Create a faculty account with email + password."""
    data = request.get_json(silent=True) or request.form
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    designation = (data.get('designation') or '').strip()
    password = (data.get('password') or '').strip()

    if not (name and email and password):
        return jsonify({'success': False, 'message': 'Name, email and password are required.'}), 400

    # --- Check for existing email ---
    if getattr(db, "use_mongo", False):
        existing = db.db.faculty.find_one({'email': email})
        if existing:
            return jsonify({'success': False, 'message': 'A faculty account already exists for this email.'}), 400
    else:
        all_data = db._read()
        faculty_list = all_data.get('faculty', [])
        if any(f.get('email') == email for f in faculty_list):
            return jsonify({'success': False, 'message': 'A faculty account already exists for this email.'}), 400

    new_fac = {
        'id': str(uuid.uuid4()),
        'name': name,
        'email': email,
        'designation': designation,
        'password_hash': generate_password_hash(password),
        'is_active': True,
        'created_at': dt.datetime.utcnow().isoformat()
    }

    # Use Database helper so it goes to MongoDB or JSON automatically
    db.add_faculty(new_fac)

    return jsonify({'success': True, 'message': 'Faculty account created. You can now login.'})

@app.route('/api/faculty/logout', methods=['POST'])
def api_faculty_logout():
    session.pop('faculty', None)
    return jsonify({'success': True})

@app.route('/api/faculty/check-session')
def api_faculty_check_session():
    fac = session.get('faculty')
    if fac:
        return jsonify({'logged_in': True, 'faculty': fac})
    return jsonify({'logged_in': False})

# ---------- CONTACT API ----------

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    subject = (data.get('subject') or '').strip()
    message = (data.get('message') or '').strip()
    if not (name and email and subject and message):
        return jsonify({'success': False, 'message': 'All fields are required.'}), 400

    contact = {
        'id': str(uuid.uuid4()),
        'name': name,
        'email': email,
        'subject': subject,
        'message': message,
        'read': False,
        'created_at': dt.datetime.utcnow().isoformat()
    }
    db.add_contact(contact)

    # Send email to admin and thank-you email to student (if mail is configured)
    try:
        if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
            # 1) Email to admin
            if ADMIN_EMAIL:
                msg = Message(subject=f"Contact: {subject}", recipients=[ADMIN_EMAIL])
                msg.body = f"From: {name} <{email}>\n\n{message}"
                mail.send(msg)

            # 2) Thank-you email to student
            if email:
                thanks = Message(
                    subject="Thank you for contacting Computer Science Department",
                    recipients=[email]
                )
                thanks.body = (
                    f"Dear {name},\n\n"
                    "Thank you for reaching out to the Computer Science Department. "
                    "We have received your message and will get back to you as soon as possible.\n\n"
                    f"Subject: {subject}\n"
                    f"Your message:\n{message}\n\n"
                    "Regards,\n"
                    "Department of Computer Science"
                )
                mail.send(thanks)
    except Exception as e:
        app.logger.error("Failed to send contact emails: %s", e)

    return jsonify({'success': True, 'message': 'Message sent successfully.'})
# ---------- PUBLIC DATA APIs FOR HOME PAGE ----------

@app.route('/api/notifications')
def api_notifications():
    """
    Returns rich announcements for:
    - Top ticker (board='ticker' or 'both')
    - Main board (board='board' or 'both')
    Each item may have a URL (file or external link).
    """
    items = db.list_notifications()
    result = []

    for n in items:
        # Only show active
        if not n.get('is_active', True):
            continue

        # Date to display
        date_val = n.get('date') or n.get('created_at')
        date_str = 'Notification'
        if isinstance(date_val, str):
            try:
                dt = dt.datetime.fromisoformat(date_val)
                date_str = dt.strftime('%d %b %Y')
            except Exception:
                pass
        elif isinstance(date_val, dt.datetime):
            date_str = date_val.strftime('%d %b %Y')

        title = n.get('title') or ''
        message = n.get('message') or n.get('text') or ''
        category = n.get('category') or 'general'
        board = n.get('board') or 'both'   # 'ticker', 'board', 'both'
        link_url = n.get('link_url') or ''
        file_path = n.get('file_path') or n.get('file') or ''

        file_url = file_path or ''
        url = link_url or file_url  # priority: external link > file

        result.append({
            'id': n.get('id'),
            'title': title,
            'message': message,
            'category': category,
            'board': board,
            'date': date_str,
            'url': url,
        })

    return jsonify(result)

@app.route('/api/gallery')
def api_gallery():
    """Return gallery items; optional ?category=events / industrial_tour / infrastructure / general."""
    category = request.args.get('category')
    items = db.list_gallery()
    result = []
    for g in items:
        if category and g.get('category') != category:
            continue
        img = g.get('image') or g.get('file') or g.get('path')
        if img and not img.startswith('http'):
            img = '/uploads/' + img if not img.startswith('/uploads/') else img
        result.append({
            'id': g.get('id'),
            'title': g.get('title'),
            'category': g.get('category'),
            'description': g.get('description'),
            'image': img or ''
        })
    return jsonify(result)

    events = db.list_events()
    return jsonify(events)

@app.route('/api/faculty')
def api_faculty():
    return jsonify(db.list_faculty())

# ---------- UPLOADS ----------

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ---------- ADMIN (complete admin section) ----------

def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return fn(*args, **kwargs)
    return wrapper

def _to_dt(value):
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(value, fmt)
            except Exception:
                continue
        try:
            return dt.datetime.fromisoformat(value)
        except Exception:
            return None
    return None

def _wrap_list_with_id(items):
    wrapped = []
    for d in items:
        wrapped.append(type("Obj", (object,), {**d, "_id": d.get("id")})())
    return wrapped

# ---- Admin Auth ----

@app.route('/admin/login', methods=['GET'])
def admin_login():
    if session.get('admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/login.html')


@app.route('/admin/login', methods=['POST'], endpoint='admin_login_post')
def admin_login_post():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == ADMIN_USER and password == ADMIN_PASS:
        session['admin'] = True
        return redirect(url_for('admin_dashboard'))
    flash('Invalid username or password', 'error')
    return redirect(url_for('admin_login'))


@app.route('/admin/logout', endpoint='admin_logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))


# ---- Dashboard ----

@app.route('/admin', endpoint='admin_dashboard')
@admin_required
def admin_dashboard():
    students = db.list_students()
    blogs_pending = db.list_blogs(approved_only=False, status='pending')
    contacts = db.list_contacts()
    events = db.list_events()
    faculty = db.list_faculty()

    # stats object used in dashboard.html
    stats = type("Stats", (object,), {
        "total_students": len(students),
        "pending_blogs": len(blogs_pending),
        "total_contacts": len(contacts),
        "total_events": len(events),
        "total_faculty": len(faculty),
    })()

    # decorate recent contacts with datetime
    contacts_sorted = sorted(contacts, key=lambda c: _to_dt(c.get("created_at")) or dt.datetime.min, reverse=True)
    recent_contacts = []
    for c in contacts_sorted[:5]:
        dt_val = _to_dt(c.get("created_at"))
        recent_contacts.append(type("ContactObj", (object,), {**c, "created_at": dt_val})())

    # pending blogs list for dashboard card
    pending_objs = []
    for b in blogs_pending[:5]:
        created = _to_dt(b.get("created_at"))
        pending_objs.append(type("BlogObj", (object,), {
            "title": b.get("title"),
            "author_name": b.get("author_name") or b.get("student_name") or "Anonymous",
            "created_at": created
        })())

    return render_template(
        'admin/dashboard.html',
        stats=stats,
        recent_contacts=recent_contacts,
        pending_blogs=pending_objs
    )


# ---- Blogs Management ----

@app.route('/admin/blogs', endpoint='admin_blogs')
@admin_required
def admin_blogs():
    status = request.args.get('status', 'all')
    if status == 'pending':
        blogs = db.list_blogs(approved_only=False, status='pending')
    elif status == 'approved':
        blogs = db.list_blogs(approved_only=False, status='approved')
    elif status == 'rejected':
        blogs = db.list_blogs(approved_only=False, status='rejected')
    else:
        blogs = db.list_blogs(approved_only=False)

    decorated = []
    for b in blogs:
        status_val = b.get('status')
        if not status_val:
            status_val = 'approved' if b.get('approved') else 'pending'
        created = _to_dt(b.get('created_at'))
        decorated.append(type('BlogObj', (object,),{
            '_id': b.get('id'),
            'title': b.get('title'),
            'student_id': b.get('student_id'),
            'author_name': b.get('author_name'),
            'status': status_val,
            'created_at': created
        })())
    return render_template('admin/blogs.html', blogs=decorated, current_filter=status)


@app.route('/admin/blogs/approve/<blog_id>', methods=['POST'], endpoint='approve_blog')
@admin_required
def approve_blog(blog_id):
    # Update blog status
    db.update_blog(blog_id, {
        'status': 'approved',
        'approved': True,
        'approved_at': dt.datetime.utcnow().isoformat()
    })

    # Fetch blog to get author email
    blog = db.get_blog(blog_id)
    if blog:
        author_email = blog.get('author_email')
        title = blog.get('title')
        author_name = blog.get('author_name') or "Author"

        try:
            if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD') and author_email:
                msg = Message(
                    subject="Your blog has been approved",
                    recipients=[author_email]
                )
                msg.body = (
                    f"Dear {author_name},\n\n"
                    f"Your blog post titled \"{title}\" has been approved by the Department of Computer Science.\n\n"
                    "It is now visible on the website.\n\n"
                    "Regards,\n"
                    "Department of Computer Science"
                )
                mail.send(msg)
        except Exception as e:
            app.logger.error("Failed to send blog approval email: %s", e)

    return redirect(url_for('admin_blogs'))



@app.route('/admin/blogs/reject/<blog_id>', methods=['POST'], endpoint='reject_blog')
@admin_required
def reject_blog(blog_id):
    db.update_blog(blog_id, {'status': 'rejected', 'approved': False})
    return redirect(url_for('admin_blogs'))


@app.route('/admin/blogs/delete/<blog_id>', methods=['POST'], endpoint='delete_blog')
@admin_required
def delete_blog(blog_id):
    db.delete_blog(blog_id)
    return redirect(url_for('admin_blogs'))


# ---- Contacts Management ----

@app.route('/admin/contacts', endpoint='admin_contacts')
@admin_required
def admin_contacts():
    contacts = db.list_contacts()
    decorated = []
    for c in contacts:
        ca_dt = _to_dt(c.get('created_at'))
        decorated.append(type("ContactObj", (object,), {**c, "_id": c.get("id"), "created_at": ca_dt})())
    return render_template('admin/contacts.html', contacts=decorated)


@app.route('/admin/contacts/mark-read/<contact_id>', methods=['POST'], endpoint='mark_contact_read')
@admin_required
def mark_contact_read(contact_id):
    db.update_contact(contact_id, {'read': True})
    return redirect(url_for('admin_contacts'))


@app.route('/admin/contacts/delete/<contact_id>', methods=['POST'], endpoint='delete_contact')
@admin_required
def delete_contact(contact_id):
    db.delete_contact(contact_id)
    return redirect(url_for('admin_contacts'))


# ---- Students Management ----

@app.route('/admin/students', endpoint='admin_students')
@admin_required
def admin_students():
    students = db.list_students()
    decorated = []
    for s in students:
        ca_dt = _to_dt(s.get('created_at'))
        decorated.append(type('Stu',(object,),{
            '_id': s.get('id'),
            'name': s.get('name'),
            'student_id': s.get('student_id'),
            'email': s.get('email'),
            'is_active': s.get('is_active', True),
            'created_at': ca_dt
        })())
    return render_template('admin/students.html', students=decorated)


@app.route('/admin/students/toggle/<student_id>', methods=['POST'], endpoint='toggle_student')
@admin_required
def toggle_student(student_id):
    students = db.list_students()
    target = next((s for s in students if s.get('id') == student_id), None)
    if target:
        db.update_student(student_id, {'is_active': not target.get('is_active', True)})
    return redirect(url_for('admin_students'))


@app.route('/admin/students/delete/<student_id>', methods=['POST'], endpoint='delete_student')
@admin_required
def delete_student(student_id):
    db.delete_student(student_id)
    return redirect(url_for('admin_students'))


# ---- Faculty Management ----

@app.route('/admin/faculty', endpoint='admin_faculty')
@admin_required
def admin_faculty():
    faculty_list = db.list_faculty()
    decorated = _wrap_list_with_id(faculty_list)
    return render_template('admin/faculty.html', faculty=decorated)


@app.route('/admin/faculty/add', methods=['GET', 'POST'], endpoint='add_faculty')
@admin_required
def add_faculty():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        role = request.form.get('role','').strip()
        qualification = request.form.get('qualification','').strip()
        email = request.form.get('email','').strip()
        phone = request.form.get('phone','').strip()
        specialization = request.form.get('specialization','').strip()
        experience = request.form.get('experience','').strip()
        order = int(request.form.get('order') or 0)

        photo_file = request.files.get('photo')
        resume_file = request.files.get('resume')
        photo_path = None
        resume_path = None

        if photo_file and allowed_file(photo_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + photo_file.filename)
            photo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            photo_path = '/uploads/' + filename

        if resume_file and allowed_file(resume_file.filename, ALLOWED_DOC):
            filename = secure_filename(str(uuid.uuid4()) + "-" + resume_file.filename)
            resume_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            resume_path = '/uploads/' + filename

        faculty = {
            'id': str(uuid.uuid4()),
            'name': name,
            'role': role,
            'qualification': qualification,
            'email': email,
            'phone': phone,
            'specialization': specialization,
            'experience': experience,
            'photo': photo_path,
            'resume': resume_path,
            'order': order,
        }
        db.add_faculty(faculty)
        return redirect(url_for('admin_faculty'))

    return render_template('admin/faculty_form.html', faculty=None)


@app.route('/admin/faculty/edit/<faculty_id>', methods=['GET', 'POST'], endpoint='edit_faculty')
@admin_required
def edit_faculty(faculty_id):
    # Use list_faculty so it works in Mongo or JSON mode
    faculty_list = db.list_faculty() or []
    fac = next((f for f in faculty_list if str(f.get('id')) == str(faculty_id)), None)

    if not fac:
        flash('Faculty not found', 'error')
        return redirect(url_for('admin_faculty'))

    if request.method == 'POST':
        changes = {
            'name': (request.form.get('name') or '').strip(),
            'role': (request.form.get('role') or '').strip(),
            'qualification': (request.form.get('qualification') or '').strip(),
            'email': (request.form.get('email') or '').strip(),
            'phone': (request.form.get('phone') or '').strip(),
            'specialization': (request.form.get('specialization') or '').strip(),
            'experience': (request.form.get('experience') or '').strip(),
            'order': int(request.form.get('order') or 0),
        }

        photo_file = request.files.get('photo')
        resume_file = request.files.get('resume')

        if photo_file and allowed_file(photo_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + photo_file.filename)
            photo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            changes['photo'] = '/uploads/' + filename

        if resume_file and allowed_file(resume_file.filename, ALLOWED_DOC):
            filename = secure_filename(str(uuid.uuid4()) + "-" + resume_file.filename)
            resume_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            changes['resume'] = '/uploads/' + filename

        db.update_faculty(faculty_id, changes)
        flash('Faculty updated successfully.', 'success')
        return redirect(url_for('admin_faculty'))

    fac_obj = type("FacObj", (object,), dict(fac))()
    return render_template('admin/faculty_form.html', faculty=fac_obj)



@app.route('/admin/faculty/delete/<faculty_id>', methods=['POST'], endpoint='delete_faculty')
@admin_required
def delete_faculty(faculty_id):
    db.delete_faculty(faculty_id)
    return redirect(url_for('admin_faculty'))


# ---- Events Management ----

@app.route('/admin/events', endpoint='admin_events')
@admin_required
def admin_events():
    events = db.list_events()
    decorated = []
    for e in events:
        dt_val = _to_dt(e.get('date'))
        decorated.append(type("EventObj",(object,),{**e, "_id": e.get("id"), "date": dt_val})())
    return render_template('admin/events.html', events=decorated)


@app.route('/admin/events/add', methods=['GET', 'POST'], endpoint='add_event')
@admin_required
def add_event():
    if request.method == 'POST':
        title = request.form.get('title','').strip()
        date_str = request.form.get('date') or ''
        location = request.form.get('location','').strip()
        description = request.form.get('description','').strip()
        order = int(request.form.get('order') or 0)

        image_file = request.files.get('image')
        image_path = None
        if image_file and allowed_file(image_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_path = '/uploads/' + filename

        event = {
            'id': str(uuid.uuid4()),
            'title': title,
            'date': date_str,
            'location': location,
            'description': description,
            'image': image_path,
            'order': order,
        }
        db.add_event(event)
        return redirect(url_for('admin_events'))

    return render_template('admin/event_form.html', event=None)


@app.route('/admin/events/edit/<event_id>', methods=['GET', 'POST'], endpoint='edit_event')
@admin_required
def edit_event(event_id):
    events = db.list_events() or []
    ev = next((e for e in events if str(e.get('id')) == str(event_id)), None)

    if not ev:
        flash('Event not found', 'error')
        return redirect(url_for('admin_events'))

    if request.method == 'POST':
        changes = {
            'title': (request.form.get('title') or '').strip(),
            'date': request.form.get('date') or '',
            'location': (request.form.get('location') or '').strip(),
            'description': (request.form.get('description') or '').strip(),
            'order': int(request.form.get('order') or 0),
        }

        image_file = request.files.get('image')
        if image_file and allowed_file(image_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            changes['image'] = '/uploads/' + filename

        db.update_event(event_id, changes)
        flash('Event updated successfully.', 'success')
        return redirect(url_for('admin_events'))

    ev_obj = type("EventObj", (object,), dict(ev))()
    return render_template('admin/event_form.html', event=ev_obj)


@app.route('/admin/events/delete/<event_id>', methods=['POST'], endpoint='delete_event')
@admin_required
def delete_event(event_id):
    db.delete_event(event_id)
    return redirect(url_for('admin_events'))


# ---- Gallery Management ----

@app.route('/admin/gallery', endpoint='admin_gallery')
@admin_required
def admin_gallery_admin():
    gallery_items = db.list_gallery()
    decorated = _wrap_list_with_id(gallery_items)
    return render_template('admin/gallery.html', gallery_items=decorated)


@app.route('/admin/gallery/add', methods=['GET', 'POST'], endpoint='add_gallery_item')
@admin_required
def add_gallery_item():
    if request.method == 'POST':
        title = request.form.get('title','').strip()
        category = request.form.get('category','').strip()
        date_str = request.form.get('date') or ''
        description = request.form.get('description','').strip()

        image_file = request.files.get('image')
        image_path = None
        if image_file and allowed_file(image_file.filename, ALLOWED_IMG):
            filename = secure_filename(str(uuid.uuid4()) + "-" + image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_path = '/uploads/' + filename

        item = {
            'id': str(uuid.uuid4()),
            'title': title,
            'category': category,
            'date': date_str,
            'image': image_path,
            'description': description,
        }
        db.add_gallery(item)
        return redirect(url_for('admin_gallery'))

    return render_template('admin/gallery_form.html')


@app.route('/admin/gallery/delete/<item_id>', methods=['POST'], endpoint='delete_gallery_item')
@admin_required
def delete_gallery_item(item_id):
    db.delete_gallery(item_id)
    return redirect(url_for('admin_gallery'))


# ---- Research Management ----

@app.route('/admin/research', endpoint='admin_research')
@admin_required
def admin_research_admin():
    research_papers = db.list_research()
    decorated = _wrap_list_with_id(research_papers)
    return render_template('admin/research.html', research_papers=decorated)


@app.route('/admin/research/add', methods=['GET', 'POST'], endpoint='add_research')
@admin_required
def add_research():
    if request.method == 'POST':
        title = request.form.get('title','').strip()
        author = request.form.get('author','').strip()
        category = request.form.get('category','').strip()
        description = request.form.get('description','').strip()
        date_raw = request.form.get('date','').strip()
        pdf_link = request.form.get('pdf_link','').strip()

        # Parse date into ISO for storage
        date_iso = None
        if date_raw:
            try:
                # HTML date input is YYYY-MM-DD
                date_iso = dt.datetime.fromisoformat(date_raw).isoformat()
            except Exception:
                date_iso = date_raw

        pdf_file = request.files.get('pdf')
        pdf_path = None
        if pdf_file and allowed_file(pdf_file.filename, {'pdf'}):
            filename = secure_filename(str(uuid.uuid4()) + "-" + pdf_file.filename)
            pdf_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            pdf_path = '/uploads/' + filename

        paper = {
            'id': str(uuid.uuid4()),
            'title': title,
            'author': author,
            'category': category,
            'description': description,
            'date': date_iso,
            'pdf_path': pdf_path,
            'pdf_link': pdf_link,
        }
        db.add_research(paper)
        return redirect(url_for('admin_research'))

    return render_template('admin/research_form.html')

@app.route('/admin/research/delete/<research_id>', methods=['POST'], endpoint='admin_delete_research')
@admin_required
def delete_research(research_id):
    db.delete_research(research_id)
    return redirect(url_for('admin_research'))


# ---- Notifications Management ----

@app.route('/admin/notifications', endpoint='admin_notifications')
@admin_required
def admin_notifications():
    notifications = db.list_notifications()
    decorated = []
    for n in notifications:
        dt_val = _to_dt(n.get('date') or n.get('created_at'))
        decorated.append(type("NotifObj",(object,),{**n, "_id": n.get("id"), "date": dt_val})())
    return render_template('admin/notifications.html', notifications=decorated)


@app.route('/admin/notifications/add', methods=['GET', 'POST'], endpoint='add_notification')
@admin_required
def add_notification():
    """
    Admin can create an announcement:
    - title, message
    - category: exam / event / notice / workshop / general
    - board: ticker / board / both
    - optional file (pdf/image) OR external link
    """
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        message = (request.form.get('message') or '').strip()
        category = (request.form.get('category') or '').strip() or 'general'
        board = (request.form.get('board') or '').strip() or 'both'
        date_str = request.form.get('date') or ''
        link_url = (request.form.get('link_url') or '').strip()
        is_active = bool(request.form.get('is_active'))

        file = request.files.get('file')
        file_path = None
        if file and file.filename:
            filename = secure_filename(str(uuid.uuid4()) + "-" + file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            file_path = '/uploads/' + filename

        notif = {
            'id': str(uuid.uuid4()),
            'title': title or (message[:60] if message else 'Announcement'),
            'message': message,
            'category': category,        # exam / event / notice / workshop / general
            'board': board,              # ticker / board / both
            'date': date_str,
            'link_url': link_url or None,
            'file_path': file_path,
            'is_active': is_active,
            'created_at': dt.datetime.utcnow().isoformat()
        }
        db.add_notification(notif)
        return redirect(url_for('admin_notifications'))

    return render_template('admin/notification_form.html', notification=None)


@app.route('/admin/notifications/toggle/<notif_id>', methods=['POST'], endpoint='toggle_notification')
@admin_required
def toggle_notification(notif_id):
    notifications = db.list_notifications() or []
    n = next((x for x in notifications if str(x.get('id')) == str(notif_id)), None)

    if n:
        new_state = not n.get('is_active', True)
        db.update_notification(notif_id, {'is_active': new_state})

    return redirect(url_for('admin_notifications'))



@app.route('/admin/notifications/delete/<notif_id>', methods=['POST'], endpoint='delete_notification')
@admin_required
def delete_notification(notif_id):
    db.delete_notification(notif_id)
    return redirect(url_for('admin_notifications'))


# ---- CSA Members Management ----



@app.route('/admin/csa', endpoint='admin_csa_members')
@admin_required
def admin_csa():
    members = db.list_csa_members() or []
    past_csa = db.list_past_csa() or []
    return render_template(
        'admin/csa_members.html',
        members=members,
        past_csa=past_csa
    )


@app.route('/admin/csa/add', methods=['GET', 'POST'], endpoint='add_csa_member')
@admin_required
def add_csa_member():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        position = request.form.get('position','').strip()
        year = request.form.get('year','').strip()
        contact = request.form.get('contact','').strip()
        order = int(request.form.get('order') or 0)
        is_current = bool(request.form.get('is_current'))

        member = {
            'id': str(uuid.uuid4()),
            'name': name,
            'position': position,
            'year': year,
            'contact': contact,
            'order': order,
            'is_current': is_current,
        }
        db.add_csa_member(member)
        return redirect(url_for('admin_csa_members'))

    return render_template('admin/csa_member_form.html', member=None)


@app.route('/admin/csa/edit/<member_id>', methods=['GET', 'POST'], endpoint='edit_csa_member')
@admin_required
def edit_csa_member(member_id):
    # Works for both MongoDB and JSON
    members = db.list_csa_members() or []
    mem = next((m for m in members if str(m.get('id')) == str(member_id)), None)

    if not mem:
        flash('CSA member not found', 'error')
        return redirect(url_for('admin_csa_members'))

    if request.method == 'POST':
        changes = {
            'name': (request.form.get('name') or '').strip(),
            'position': (request.form.get('position') or '').strip(),
            'year': (request.form.get('year') or '').strip(),
            'contact': (request.form.get('contact') or '').strip(),
            'order': int(request.form.get('order') or 0),
            'is_current': bool(request.form.get('is_current')),
        }
        db.update_csa_member(member_id, changes)
        flash('CSA member updated successfully.', 'success')
        return redirect(url_for('admin_csa_members'))

    mem_obj = type("MemObj", (object,), dict(mem))()
    return render_template('admin/csa_member_form.html', member=mem_obj)



@app.route('/admin/csa/delete/<member_id>', methods=['POST'], endpoint='delete_csa_member')
@admin_required
def delete_csa_member(member_id):
    db.delete_csa_member(member_id)
    return redirect(url_for('admin_csa_members'))

# ---------- ADMIN: Past CSA Members PDF management ----------

@app.route('/admin/csa/past/add', methods=['POST'], endpoint='admin_csa_past_add')
@admin_required
def admin_csa_past_add():
    year = (request.form.get('year') or '').strip()
    title = (request.form.get('title') or '').strip()
    pdf_file = request.files.get('pdf')

    if not (year and pdf_file and pdf_file.filename):
        flash('Year and PDF file are required.', 'danger')
        return redirect(url_for('admin_csa_members'))

    filename = secure_filename(pdf_file.filename)
    # ensure uploads folder
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'past_csa')
    os.makedirs(upload_dir, exist_ok=True)

    file_id = f"{uuid.uuid4().hex}_{filename}"
    save_path = os.path.join(upload_dir, file_id)
    pdf_file.save(save_path)

    # store relative path for URL
    rel_path = f"uploads/past_csa/{file_id}"

    entry = {
        'id': str(uuid.uuid4()),
        'year': year,
        'title': title or f'CSA Members {year}',
        'pdf_path': rel_path,
        'created_at': dt.datetime.utcnow().isoformat()
    }
    db.add_past_csa(entry)
    flash('Past CSA PDF added successfully.', 'success')
    return redirect(url_for('admin_csa_members'))


@app.route('/admin/csa/past/delete/<entry_id>', methods=['POST'], endpoint='admin_csa_past_delete')
@admin_required
def admin_csa_past_delete(entry_id):
    # optionally also remove file from disk
    items = db.list_past_csa()
    target = next((e for e in items if e.get('id') == entry_id), None)
    if target:
        path = target.get('pdf_path')
        if path:
            full = os.path.join(app.root_path, 'static', path)
            try:
                if os.path.exists(full):
                    os.remove(full)
            except Exception as e:
                app.logger.error('Failed to remove past CSA pdf: %s', e)
    db.delete_past_csa(entry_id)
    flash('Past CSA entry removed.', 'success')
    return redirect(url_for('admin_csa_members'))
@app.route("/curriculum")
def curriculum():
    data = db.list_curriculum()
    print("CURRICULUM DATA:", data)  # DEBUG
    return render_template("curriculum.html", records=data)


@app.route("/api/curriculum")
def api_curriculum():
    data = db.list_curriculum()
    for r in data:
        r.pop("_id", None)
    return jsonify(data)


# ---------------- ADMIN ----------------
@app.route("/admin/curriculum")
def admin_curriculum():
    records = db.list_curriculum()
    return render_template("admin/curriculum.html", records=records)

@app.route("/admin/curriculum/upload", methods=["POST"])
def upload_curriculum():
    degree = request.form.get("degree")
    year = request.form.get("year")
    pdf = request.files.get("pdf")

    if not pdf or pdf.filename == "":
        flash("PDF required", "error")
        return redirect(url_for("admin_curriculum"))

    filename = secure_filename(f"{degree}_{year}.pdf")

    # Ensure directory exists
    upload_dir = os.path.join("static", "uploads", "syllabus")
    os.makedirs(upload_dir, exist_ok=True)

    save_path = os.path.join(upload_dir, filename)
    pdf.save(save_path)

    # Save to DB
    db.add_or_update_curriculum({
        "degree": degree,
        "year": year,
        "pdf_url": f"/static/uploads/syllabus/{filename}",
        "uploaded_at": dt.datetime.now().strftime("%Y-%m-%d")
    })

    flash("Curriculum uploaded / replaced successfully", "success")
    return redirect(url_for("admin_curriculum"))

@app.route("/admin/curriculum/delete")
def delete_curriculum():
    degree = request.args.get("degree")
    year = request.args.get("year")

    if not degree or not year:
        flash("Invalid delete request", "error")
        return redirect(url_for("admin_curriculum"))

    records = db.list_curriculum()
    rec = next(
        (r for r in records if r.get("degree") == degree and r.get("year") == year),
        None
    )

    if not rec:
        flash("Curriculum not found", "error")
        return redirect(url_for("admin_curriculum"))

    # Delete PDF file
    pdf_path = rec.get("pdf_url", "").lstrip("/")
    if pdf_path and os.path.exists(pdf_path):
        os.remove(pdf_path)

    # Delete from DB
    db.delete_curriculum(degree, year)

    flash("Curriculum deleted successfully", "success")
    return redirect(url_for("admin_curriculum"))

@app.route("/api/alumni")
def api_alumni():
    return jsonify(db.list_alumni())
@app.route("/admin/alumni")
def admin_alumni():
    records = db.list_alumni()
    return render_template("admin/alumni.html", records=records)
@app.route("/admin/alumni/add", methods=["POST"])
def add_alumni():
    name = request.form["name"]
    message = request.form["message"]
    photo = request.files["photo"]

    if not photo:
        flash("Photo required", "error")
        return redirect(url_for("admin_alumni"))

    filename = secure_filename(photo.filename)
    filename = f"{uuid.uuid4()}-{filename}"
    path = os.path.join("static/uploads/alumni", filename)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    photo.save(path)

    entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "message": message,
        "photo": "/" + path.replace("\\", "/"),
        "created_at": dt.datetime.now().strftime("%Y-%m-%d")

    }

    db.add_alumni(entry)

    flash("Alumni testimonial added", "success")
    return redirect(url_for("admin_alumni"))
@app.route("/admin/alumni/delete/<aid>")
def delete_alumni(aid):
    db.delete_alumni(aid)
    flash("Alumni testimonial deleted", "success")
    return redirect(url_for("admin_alumni"))


@app.route('/send-test-email')
def send_test_email():
    if not ADMIN_EMAIL:
        return "ADMIN_EMAIL not set", 400
    try:
        msg = Message(subject="Test email from CSD website", recipients=[ADMIN_EMAIL])
        msg.body = "This is a test email from your CSD website."
        mail.send(msg)
        return f"Test email sent to {ADMIN_EMAIL}", 200
    except Exception as e:
        return f"Failed to send email: {e}", 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

