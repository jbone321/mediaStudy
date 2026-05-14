"""
docstring for main.py — Reddit Attention Tracker (CSV + OneDrive) with TRUE "new since start" discovery

Key upgrades vs prior version:
- Uses PRAW stream.submissions(skip_existing=True) across a multi-subreddit feed
  to capture only posts that appear AFTER the scraper starts.
- Persists scraper_start_utc in state.json so restarts do NOT backfill older posts.
- Hard filters posts by created_utc >= scraper_start_utc - GRACE_SECONDS to ensure
  we only track beginning lifecycle.

Outputs (in ONEDRIVE_DATA_DIR):
- posts.csv                     (one row per discovered post)
- snapshots_YYYY-MM-DD.csv      (append-only snapshots, one file per UTC day)
- state.json                    (persistent tracker state + scraper_start_utc)

Requires:
- praw
- python-dotenv

.env example (Windows OneDrive Personal):
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=attention-tracker:1.0 (by u/yourname)
ONEDRIVE_DATA_DIR=C:\\Users\\YOURNAME\\OneDrive\\RedditAttentionTracker\\data
MAX_REQUESTS_PER_MINUTE=60

Notes:
- Reddit does NOT provide true upvote counts; we track score (net votes), comments, upvote_ratio.
- Only one machine should write to these CSVs to avoid OneDrive conflicts.
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import praw
from dotenv import load_dotenv


# -----------------------------
# Configuration
# -----------------------------

SUBREDDITS = [
    "Announcements", "funny", "AskReddit", "worldnews", "gaming",
    "todayilearned", "Music", "aww", "movies", "memes", "science"
]

# How long to track a post after creation
TRACKING_HORIZON_DAYS = 14

# Grace window to allow for slight delays/caching between "created" and stream visibility
GRACE_SECONDS = 60

# Cadence policy: seconds until next poll based on age (seconds)
def next_interval_seconds(age_s: int) -> int:
    if age_s < 3600:         # < 1 hour
        return 10 * 60       # 10 minutes
    if age_s < 6 * 3600:     # 1–6 hours
        return 30 * 60       # 30 minutes
    if age_s < 24 * 3600:    # 6–24 hours
        return 2 * 3600      # 2 hours
    return 12 * 3600         # 12 hours (1–14 days)

PLATEAU_MAX = 5
PLATEAU_SCORE_EPS = 2        # if score change < eps AND comments change == 0 => plateau increment
BATCH_SIZE = 100             # reddit.info supports up to 100 fullnames per call

# Loop tuning
TRACKING_CYCLE_SLEEP = 5     # seconds between tracking cycles
MAX_STREAM_DRAINS_PER_TICK = 50  # max new posts to ingest per outer loop iteration

HEALTH_INTERVAL_SECONDS = 600  # 10 minutes

# -----------------------------
# Helpers
# -----------------------------

def utc_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

def today_utc_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def chunked(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def safe_append_row(csv_path: Path, header: List[str], row: List) -> None:
    """
    Append a single row. Creates file + header if missing.
    """
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(row)
        f.flush()


# -----------------------------
# Token bucket budget (simple)
# -----------------------------

@dataclass
class TokenBucket:
    capacity_per_minute: int
    tokens: float
    last_refill_ts: float

    @classmethod
    def create(cls, capacity_per_minute: int) -> "TokenBucket":
        return cls(capacity_per_minute=capacity_per_minute, tokens=capacity_per_minute, last_refill_ts=time.time())

    def refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill_ts
        refill_amount = (elapsed / 60.0) * self.capacity_per_minute
        if refill_amount > 0:
            self.tokens = min(self.capacity_per_minute, self.tokens + refill_amount)
            self.last_refill_ts = now

    def take(self, amount: float = 1.0) -> bool:
        self.refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


# -----------------------------
# State model
# -----------------------------

@dataclass
class TrackedPost:
    post_id: str
    fullname: str
    subreddit: str
    title: str
    created_utc: int
    permalink: str
    first_seen_utc: int

    next_poll_at: int
    last_polled_at: int

    last_score: int
    last_comments: int
    plateau_count: int
    status: str      # "tracking" | "done"
    stop_reason: str # e.g. "plateau" | "horizon_reached"

def load_state(state_path: Path) -> Tuple[Dict[str, TrackedPost], Dict]:
    """
    Returns (tracked_posts, meta)
    meta includes scraper_start_utc
    """
    if not state_path.exists():
        return {}, {}
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    meta = raw.get("meta", {})
    posts = {}
    for pid, d in raw.get("tracked", {}).items():
        posts[pid] = TrackedPost(**d)
    return posts, meta

def save_state(state_path: Path, tracked: Dict[str, TrackedPost], meta: Dict) -> None:
    data = {
        "meta": meta,
        "tracked": {pid: vars(tp) for pid, tp in tracked.items()},
    }
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(state_path)


# -----------------------------
# Reddit client
# -----------------------------

def make_reddit() -> praw.Reddit:
    load_dotenv()
    cid = os.getenv("REDDIT_CLIENT_ID")
    csec = os.getenv("REDDIT_CLIENT_SECRET")
    ua = os.getenv("REDDIT_USER_AGENT")

    if not cid or not csec or not ua:
        raise RuntimeError("Missing Reddit credentials in .env (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT).")

    return praw.Reddit(
        client_id=cid,
        client_secret=csec,
        user_agent=ua,
        check_for_async=False,
    )


# -----------------------------
# Tracking logic
# -----------------------------

def pick_due_posts(tracked: Dict[str, TrackedPost], now: int, max_posts: int) -> List[TrackedPost]:
    due = [tp for tp in tracked.values() if tp.status == "tracking" and tp.next_poll_at <= now]
    due.sort(key=lambda x: x.next_poll_at)
    return due[:max_posts]

def prune_state(tracked: Dict[str, TrackedPost], now: int) -> None:
    cutoff = now - ((TRACKING_HORIZON_DAYS + 7) * 24 * 3600)
    to_del = [pid for pid, tp in tracked.items() if tp.status == "done" and tp.created_utc < cutoff]
    for pid in to_del:
        del tracked[pid]

def tracking_cycle(
    reddit: praw.Reddit,
    tracked: Dict[str, TrackedPost],
    snapshots_dir: Path,
    bucket: TokenBucket,
) -> int:
    now = utc_now()
    horizon_s = TRACKING_HORIZON_DAYS * 24 * 3600

    # Each reddit.info batch is ~1 request. We'll try to spend up to 10 requests/cycle.
    max_batches = 10
    max_posts = max_batches * BATCH_SIZE

    due_posts = pick_due_posts(tracked, now, max_posts=max_posts)
    if not due_posts:
        return 0

    fullnames = [tp.fullname for tp in due_posts]
    total_done = 0

    snap_csv = snapshots_dir / f"snapshots_{today_utc_yyyymmdd()}.csv"
    header = ["observed_utc", "post_id", "subreddit", "age_minutes", "score", "num_comments", "upvote_ratio", "permalink"]

    for batch in chunked(fullnames, BATCH_SIZE):
        if not bucket.take(1.0):
            break

        attempt = 0
        while True:
            try:
                refreshed = list(reddit.info(fullnames=batch))
                break
            except Exception as e:
                attempt += 1
                if attempt >= 5:
                    print(f"[tracking] batch failed after retries: {e}")
                    refreshed = []
                    break
                sleep_s = min(120, (2 ** attempt) + random.random() * 2)
                print(f"[tracking] error, backing off {sleep_s:.1f}s: {e}")
                time.sleep(sleep_s)

        ref_map = {f"t3_{s.id}": s for s in refreshed if hasattr(s, "id")}

        for fullname in batch:
            pid = fullname.replace("t3_", "")
            tp = tracked.get(pid)
            if not tp or tp.status != "tracking":
                continue

            age_s = now - tp.created_utc
            if age_s > horizon_s:
                tp.status = "done"
                tp.stop_reason = "horizon_reached"
                continue

            subm = ref_map.get(fullname)
            if subm is None:
                # Deleted/removed/transient failure; try later
                tp.next_poll_at = now + 60 * 60
                continue

            score = int(getattr(subm, "score", 0) or 0)
            num_comments = int(getattr(subm, "num_comments", 0) or 0)
            upvote_ratio = getattr(subm, "upvote_ratio", None)
            if upvote_ratio is None:
                upvote_ratio = ""

            score_delta = abs(score - tp.last_score)
            comments_delta = abs(num_comments - tp.last_comments)
            if score_delta < PLATEAU_SCORE_EPS and comments_delta == 0:
                tp.plateau_count += 1
            else:
                tp.plateau_count = 0

            # Plateau detection
            if score_delta < PLATEAU_SCORE_EPS and comments_delta == 0:
                tp.plateau_count += 1
            else:
                tp.plateau_count = 0

            # Early slowdown when plateau begins
            if tp.plateau_count >= 2 and tp.plateau_count < PLATEAU_MAX:
                # Poll much less frequently once engagement stabilizes
                tp.next_poll_at = now + (6 * 3600)  # 6 hours
            elif tp.plateau_count >= PLATEAU_MAX:
                tp.status = "done"
                tp.stop_reason = "plateau"
            else:
                interval = next_interval_seconds(age_s)
                jitter = random.randint(-5, 5)
                tp.next_poll_at = now + max(60, interval + jitter)

            tp.last_polled_at = now
            tp.last_score = score
            tp.last_comments = num_comments

            safe_append_row(
                snap_csv,
                header=header,
                row=[
                    iso_utc(now),
                    pid,
                    tp.subreddit,
                    round(age_s / 60, 2),
                    score,
                    num_comments,
                    upvote_ratio,
                    tp.permalink,
                ],
            )
            total_done += 1

    prune_state(tracked, now)
    return total_done


# -----------------------------
# Stream-based discovery ("new since start")
# -----------------------------

def ingest_from_stream(
    submission,
    tracked: Dict[str, TrackedPost],
    posts_csv: Path,
    scraper_start_utc: int,
) -> bool:
    """
    Returns True if the submission was ingested as a new tracked post.
    """
    now = utc_now()
    created = int(getattr(submission, "created_utc", 0) or 0)

    # HARD FILTER: only accept posts created after scraper start (with small grace window)
    if created < scraper_start_utc - GRACE_SECONDS:
        return False

    pid = getattr(submission, "id", None)
    if not pid:
        return False
    if pid in tracked:
        return False

    sub = str(getattr(submission, "subreddit", ""))
    title = getattr(submission, "title", "") or ""
    fullname = f"t3_{pid}"
    permalink = "https://www.reddit.com" + (getattr(submission, "permalink", "") or "")

    tp = TrackedPost(
        post_id=pid,
        fullname=fullname,
        subreddit=sub,
        title=title[:3000],
        created_utc=created,
        permalink=permalink,
        first_seen_utc=now,
        next_poll_at=now + 5 * 60,  # first snapshot soon
        last_polled_at=0,
        last_score=0,
        last_comments=0,
        plateau_count=0,
        status="tracking",
        stop_reason="",
    )
    tracked[pid] = tp

    safe_append_row(
        posts_csv,
        header=["post_id", "fullname", "subreddit", "title", "created_utc", "first_seen_utc", "permalink"],
        row=[pid, fullname, sub, title, iso_utc(created), iso_utc(now), permalink],
    )
    return True


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    load_dotenv()
    out_dir = os.getenv("ONEDRIVE_DATA_DIR")
    if not out_dir:
        raise RuntimeError("Missing ONEDRIVE_DATA_DIR in .env (must point to your local OneDrive folder).")

    max_rpm = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))

    base = Path(out_dir).expanduser()
    ensure_dir(base)

    posts_csv = base / "posts.csv"

    state_dir = Path(os.getenv("STATE_DIR", str(base))).expanduser()
    ensure_dir(state_dir)
    state_path = state_dir / "state.json"

    print(f"[startup] ONEDRIVE_DATA_DIR = {base}")
    print(f"[startup] state_path = {state_path}")
    
    reddit = make_reddit()
    bucket = TokenBucket.create(capacity_per_minute=max_rpm)

    tracked, meta = load_state(state_path)

    # Persist scraper_start_utc across restarts
    if "scraper_start_utc" not in meta or not isinstance(meta["scraper_start_utc"], int):
        meta["scraper_start_utc"] = utc_now()
        save_state(state_path, tracked, meta)

    scraper_start_utc = int(meta["scraper_start_utc"])
    print(f"[startup] loaded {len(tracked)} tracked posts")
    print(f"[startup] writing to: {base}")
    print(f"[startup] scraper_start_utc (persistent) = {iso_utc(scraper_start_utc)}")
    print(f"[startup] subreddits: {', '.join(SUBREDDITS)}")

    last_health_print = time.time()

    print(f"[startup] tracking {sum(1 for t in tracked.values() if t.status == 'tracking')} posts")


    # Multi-subreddit stream
    multi_sub = "+".join(SUBREDDITS)
    subreddit = reddit.subreddit(multi_sub)

    # pause_after=0 makes the generator yield None when no new submissions are available
    stream = subreddit.stream.submissions(skip_existing=True, pause_after=0)

    while True:
        # Drain a limited number of new posts per tick so tracking still runs
        ingested = 0
        drained = 0

        # Stream calls do hit the API. We'll gate by budget loosely.
        # NOTE: PRAW streaming internally manages some pacing; the token bucket is a safety layer.
        while drained < MAX_STREAM_DRAINS_PER_TICK:
            if not bucket.take(1.0):
                break

            try:
                submission = next(stream)
            except StopIteration:
                break
            except Exception as e:
                # stream errors: backoff, continue
                sleep_s = 10 + random.random() * 5
                print(f"[stream] error, backing off {sleep_s:.1f}s: {e}")
                time.sleep(sleep_s)
                break

            drained += 1

            if submission is None:
                break  # no new submissions right now

            try:
                if ingest_from_stream(submission, tracked, posts_csv, scraper_start_utc):
                    ingested += 1
            except Exception as e:
                print(f"[stream] ingest error: {e}")

        if ingested:
            tracking_count = sum(1 for t in tracked.values() if t.status == "tracking")
            print(f"[discovery-stream] +{ingested} new posts (tracking now: {tracking_count})")

        # Run tracking cycle
        snap_count = tracking_cycle(reddit, tracked, base, bucket)
        if snap_count:
            print(f"[tracking] snapshotted {snap_count} posts (budget tokens: {bucket.tokens:.1f})")

        # Persist state frequently
        save_state(state_path, tracked, meta)

        # periodic health report
        if time.time() - last_health_print > HEALTH_INTERVAL_SECONDS:
            print_health(tracked, bucket, base)
            last_health_print = time.time()

        time.sleep(TRACKING_CYCLE_SLEEP)

def print_health(tracked, bucket, snapshots_dir):
    now = utc_now()

    total_tracked = len(tracked)
    active_posts = sum(1 for t in tracked.values() if t.status == "tracking")
    completed_posts = sum(1 for t in tracked.values() if t.status == "done")

    # count today's snapshots
    snap_file = snapshots_dir / f"snapshots_{today_utc_yyyymmdd()}.csv"
    snapshot_count = 0

    if snap_file.exists():
        try:
            with snap_file.open("r", encoding="utf-8") as f:
                snapshot_count = sum(1 for _ in f) - 1
        except:
            snapshot_count = 0

    print("\n[health]")
    print(f"time: {iso_utc(now)}")
    print(f"tracked posts: {total_tracked}")
    print(f"active posts: {active_posts}")
    print(f"completed lifecycles: {completed_posts}")
    print(f"snapshots today: {snapshot_count}")
    print(f"reddit tokens available: {bucket.tokens:.1f}")
    print("")

if __name__ == "__main__":
    main()