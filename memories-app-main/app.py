import os
import re
import uuid
import secrets
import time
from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {"mp4", "webm", "mov", "m4v", "ogg"}

app = Flask(__name__)

SECRET_FILE = os.path.join(BASE_DIR, ".memories-secret")
configured_secret = os.environ.get("MEMORIES_SECRET_KEY")
if configured_secret:
    if len(configured_secret) < 32:
        raise RuntimeError("MEMORIES_SECRET_KEY must be at least 32 characters")
    app_secret = configured_secret
else:
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r", encoding="ascii") as secret_handle:
            app_secret = secret_handle.read().strip()
    else:
        app_secret = secrets.token_urlsafe(48)
        with open(SECRET_FILE, "x", encoding="ascii") as secret_handle:
            secret_handle.write(app_secret)

app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(BASE_DIR, "memories.db"),
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,  # 200 MB per video
    SECRET_KEY=app_secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("MEMORIES_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
db = SQLAlchemy(app)

LOGIN_ATTEMPTS = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Reel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    hashtags = db.Column(db.String(300), default="")  # space-separated, without '#'
    filename = db.Column(db.String(200), nullable=False)
    views = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    liked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    comments = db.relationship(
        "Comment", backref="reel", cascade="all, delete-orphan", order_by="Comment.created_at"
    )

    @property
    def tags(self):
        return [t for t in self.hashtags.split() if t]

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "tags": self.tags,
            "url": url_for("static", filename=f"uploads/{self.filename}"),
            "views": self.views,
            "likes": self.likes,
            "liked": self.liked,
            "comments": len(self.comments),
            "created": self.created_at.strftime("%d %b %Y"),
        }


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reel_id = db.Column(db.Integer, db.ForeignKey("reel.id"), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


with app.app_context():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db.create_all()
    configured_email = os.environ.get("MEMORIES_LOGIN_EMAIL", "").strip().lower()
    configured_password = os.environ.get("MEMORIES_LOGIN_PASSWORD", "")
    if configured_email or configured_password:
        if not configured_email or not configured_password:
            raise RuntimeError("Set both MEMORIES_LOGIN_EMAIL and MEMORIES_LOGIN_PASSWORD")
        if "@" not in configured_email or len(configured_password) < 12:
            raise RuntimeError("Login email is invalid or password is shorter than 12 characters")
        configured_user = User.query.filter_by(email=configured_email).first()
        if configured_user:
            configured_user.password_hash = generate_password_hash(configured_password)
        else:
            db.session.add(User(
                name="Memories User",
                email=configured_email,
                password_hash=generate_password_hash(configured_password),
            ))
        db.session.commit()
    else:
        legacy_user = User.query.filter_by(email="admin@memories.app").first()
        if legacy_user:
            db.session.delete(legacy_user)
            db.session.commit()
        if not User.query.first():
            first_email = "admin@memories.app"
            first_password = secrets.token_urlsafe(18)
            db.session.add(User(
                name="Memories Admin",
                email=first_email,
                password_hash=generate_password_hash(first_password),
            ))
            db.session.commit()
            print("FIRST-RUN LOGIN")
            print(f"Email: {first_email}")
            print(f"Password: {first_password}")


def parse_tags(raw):
    """'#tripzz, sayeesh #editzz' -> 'tripzz sayeesh editzz'"""
    tags = re.findall(r"[A-Za-z0-9_]+", raw or "")
    seen = []
    for t in tags:
        t = t.lower()
        if t not in seen:
            seen.append(t)
    return " ".join(seen[:10])


def search_reels(q):
    q = (q or "").strip().lstrip("#").lower()
    query = Reel.query.order_by(Reel.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Reel.title.ilike(like), Reel.hashtags.ilike(like)))
    return query.all()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id or not db.session.get(User, user_id):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect_requests():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRFToken") or request.form.get("_csrf_token")
        if not supplied or not secrets.compare_digest(supplied, csrf_token()):
            abort(403)


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "base-uri 'self'; frame-ancestors 'none'"
    )
    if request.path in {"/login"}:
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------- Pages ----------
@app.route("/")
@login_required
def home():
    reels = Reel.query.order_by(Reel.created_at.desc()).all()
    user = db.session.get(User, session["user_id"])
    stats = {
        "reels": len(reels),
        "views": sum(r.views for r in reels),
        "likes": sum(r.likes for r in reels),
        "comments": Comment.query.count(),
    }
    return render_template("home.html", reels=reels[:6], stats=stats, user=user, active="home")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("home"))
    error = None
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        client_key = f"{request.remote_addr}:{email}"
        attempts = LOGIN_ATTEMPTS.get(client_key)
        now = time.time()
        if attempts and now - attempts["first"] > LOGIN_WINDOW_SECONDS:
            LOGIN_ATTEMPTS.pop(client_key, None)
            attempts = None
        if attempts and attempts["count"] >= LOGIN_MAX_ATTEMPTS:
            error = "Too many login attempts. Try again later."
            return render_template("login.html", error=error, email=email)
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            record = LOGIN_ATTEMPTS.setdefault(client_key, {"count": 0, "first": now})
            record["count"] += 1
            error = "Invalid email or password."
        else:
            LOGIN_ATTEMPTS.pop(client_key, None)
            session.clear()
            session.permanent = True
            session["user_id"] = user.id
            session["user_name"] = user.name
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("home"))
    return render_template("login.html", error=error, email=email)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/memories")
@login_required
def feed():
    tag = request.args.get("tag")
    start = request.args.get("start", type=int)
    reels = search_reels(tag) if tag else Reel.query.order_by(Reel.created_at.desc()).all()
    if start:  # open the feed at a chosen reel
        reels.sort(key=lambda r: r.id != start)
    return render_template("feed.html", reels=reels, tag=tag, active="feed")


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    error = None
    if request.method == "POST":
        file = request.files.get("video")
        title = (request.form.get("title") or "").strip()
        ext = file.filename.rsplit(".", 1)[-1].lower() if file and "." in file.filename else ""
        if not file or not file.filename:
            error = "Please choose a video."
        elif ext not in ALLOWED_EXT:
            error = "Only MP4, WEBM, MOV, M4V or OGG videos are allowed."
        elif not title:
            error = "Please add a title."
        else:
            name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            file.save(os.path.join(UPLOAD_DIR, name))
            db.session.add(Reel(title=title[:120], hashtags=parse_tags(request.form.get("hashtags")), filename=name))
            db.session.commit()
            return redirect(url_for("feed"))
    return render_template("upload.html", error=error, active="upload")


@app.route("/collections")
@login_required
def collections():
    groups = defaultdict(list)
    for r in Reel.query.order_by(Reel.created_at.desc()).all():
        for t in r.tags or ["untagged"]:
            groups[t].append(r)
    groups = dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))
    return render_template("collections.html", groups=groups, active="collections")


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "")
    results = search_reels(q) if q else []
    return render_template("search.html", q=q, results=results, active="search")


@app.route("/settings")
@login_required
def settings():
    reels = Reel.query.order_by(Reel.created_at.desc()).all()
    size = sum(
        os.path.getsize(os.path.join(UPLOAD_DIR, r.filename))
        for r in reels
        if os.path.exists(os.path.join(UPLOAD_DIR, r.filename))
    )
    stats = {
        "reels": len(reels),
        "views": sum(r.views for r in reels),
        "likes": sum(r.likes for r in reels),
        "comments": Comment.query.count(),
        "storage_mb": round(size / (1024 * 1024), 1),
    }
    return render_template("settings.html", reels=reels, stats=stats, active="settings")


@app.route("/sw.js")
def service_worker():
    # served from the root so the PWA can control every page
    return app.send_static_file("sw.js")


# ---------- JSON API ----------
@app.get("/api/reels")
@login_required
def api_reels():
    return jsonify([r.to_dict() for r in search_reels(request.args.get("q"))])


@app.post("/api/reels/<int:rid>/view")
@login_required
def api_view(rid):
    r = db.get_or_404(Reel, rid)
    r.views += 1
    db.session.commit()
    return jsonify(views=r.views)


@app.post("/api/reels/<int:rid>/like")
@login_required
def api_like(rid):
    r = db.get_or_404(Reel, rid)
    r.liked = not r.liked
    r.likes = max(0, r.likes + (1 if r.liked else -1))
    db.session.commit()
    return jsonify(liked=r.liked, likes=r.likes)


@app.route("/api/reels/<int:rid>/comments", methods=["GET", "POST"])
@login_required
def api_comments(rid):
    r = db.get_or_404(Reel, rid)
    if request.method == "POST":
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
        if not text:
            abort(400)
        db.session.add(Comment(reel_id=r.id, text=text[:500]))
        db.session.commit()
    return jsonify(
        [{"id": c.id, "text": c.text, "time": c.created_at.strftime("%d %b, %H:%M")} for c in r.comments]
    )


@app.post("/api/reels/<int:rid>/edit")
@login_required
def api_edit(rid):
    r = db.get_or_404(Reel, rid)
    data = request.get_json(silent=True) or {}
    if (data.get("title") or "").strip():
        r.title = data["title"].strip()[:120]
    if "hashtags" in data:
        r.hashtags = parse_tags(data["hashtags"])
    db.session.commit()
    return jsonify(r.to_dict())


@app.delete("/api/reels/<int:rid>")
@login_required
def api_delete(rid):
    r = db.get_or_404(Reel, rid)
    path = os.path.join(UPLOAD_DIR, r.filename)
    if os.path.exists(path):
        os.remove(path)
    db.session.delete(r)
    db.session.commit()
    return jsonify(ok=True)


@app.errorhandler(413)
def too_large(_):
    return render_template("upload.html", error="Video is too large (max 200 MB).", active="upload"), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
