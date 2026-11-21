import os
import re
import uuid
from collections import defaultdict
from datetime import datetime

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {"mp4", "webm", "mov", "m4v", "ogg"}

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(BASE_DIR, "memories.db"),
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,  # 200 MB per video
)
db = SQLAlchemy(app)


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


# ---------- Pages ----------
@app.route("/")
def feed():
    tag = request.args.get("tag")
    start = request.args.get("start", type=int)
    reels = search_reels(tag) if tag else Reel.query.order_by(Reel.created_at.desc()).all()
    if start:  # open the feed at a chosen reel
        reels.sort(key=lambda r: r.id != start)
    return render_template("feed.html", reels=reels, tag=tag, active="feed")


@app.route("/upload", methods=["GET", "POST"])
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
def collections():
    groups = defaultdict(list)
    for r in Reel.query.order_by(Reel.created_at.desc()).all():
        for t in r.tags or ["untagged"]:
            groups[t].append(r)
    groups = dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))
    return render_template("collections.html", groups=groups, active="collections")


@app.route("/search")
def search():
    q = request.args.get("q", "")
    results = search_reels(q) if q else []
    return render_template("search.html", q=q, results=results, active="search")


@app.route("/settings")
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
def api_reels():
    return jsonify([r.to_dict() for r in search_reels(request.args.get("q"))])


@app.post("/api/reels/<int:rid>/view")
def api_view(rid):
    r = db.get_or_404(Reel, rid)
    r.views += 1
    db.session.commit()
    return jsonify(views=r.views)


@app.post("/api/reels/<int:rid>/like")
def api_like(rid):
    r = db.get_or_404(Reel, rid)
    r.liked = not r.liked
    r.likes = max(0, r.likes + (1 if r.liked else -1))
    db.session.commit()
    return jsonify(liked=r.liked, likes=r.likes)


@app.route("/api/reels/<int:rid>/comments", methods=["GET", "POST"])
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
    # host 0.0.0.0 lets your phone open it on the same Wi-Fi
    app.run(host="0.0.0.0", port=5000, debug=True)
