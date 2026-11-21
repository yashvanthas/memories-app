# Memories — your videos as reels

A Flask web app that stores your personal videos as vertical reels. It has a dark UI, full-screen swipe, likes, comments, hashtags, collections and search, and you can install it on your phone as a PWA.

## Features
- Full-screen vertical reel feed: autoplay, loop, tap to pause, double-tap to like
- Upload a video with title and hashtags (drag & drop, preview, progress bar)
- Likes, view counts, comments, emoji reactions, share link
- Edit title/hashtags, delete (long-press a reel or the ••• button)
- Collections grouped by hashtag, search by title or #hashtag
- Settings: stats (reels, views, likes, storage) and a manage list
- Installable PWA (Add to Home screen)

## Tech stack
Python · Flask · Flask-SQLAlchemy · SQLite · Jinja2 · HTML5 video · CSS scroll-snap · Vanilla JS · REST API · PWA

## Run
```bash
python -m venv venv
venv\Scripts\activate          # Windows  (source venv/bin/activate on Mac/Linux)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000. On your phone (same Wi-Fi), open `http://<your-PC-IP>:5000`.

## REST API
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/reels?q=` | List or search reels |
| POST | `/api/reels/<id>/view` | Count a view |
| POST | `/api/reels/<id>/like` | Toggle like |
| GET/POST | `/api/reels/<id>/comments` | List / add comment |
| POST | `/api/reels/<id>/edit` | Edit title / hashtags |
| DELETE | `/api/reels/<id>` | Delete reel and its file |

## Project structure
```
app.py              Flask app, models, routes, API
templates/          Jinja pages (feed, upload, collections, search, settings)
static/style.css    Dark UI, purple glow, pill nav
static/uploads/     Saved videos
memories.db         SQLite database (created on first run)
```
