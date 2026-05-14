"""


main.py — docstring

1) Discovery- appends unseen posts to posts.csv.
2) Tracking: tracks updates for previously discovered posts
   (score, num_comments, upvote_ratio) using batched requests via reddit.info().
   Appends snapshots to a daily CSV file snapshots_YYYY-MM-DD.csv.
3) Scheduling: avoids exponential API growth by only updating posts when due
   (next_poll_at), lowering call rate as posts age.

Outputs into OneDrive:
- posts.csv
- snapshots_YYYY-MM-DD.csv (daily append-only)
- state.json (scheduler + seen/tracked)
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


SUBREDDITS = [
    "Announcements", "funny", "AskReddit", "worldnews", "gaming",
    "todayilearned", "Music", "aww", "movies", "memes", "science"
]

DISCOVERY_LIMIT_PER_SUB = 25  # how many "new" posts to check each discovery cycle
TRACKING_HORIZON_DAYS = 14

#return seconds until next poll based on age in sec.
def next_interval_seconds(age_s: int) -> int:
    if age_s < 3600:         # < 1 hour
        return 10 * 60       # 10 minutes
    if age_s < 6 * 3600:     # 1–6 hours
        return 30 * 60       # 30 minutes
    if age_s < 24 * 3600:    # 6–24 hours
        return 2 * 3600      # 2 hours
    return 12 * 3600         # 12 hours (1–14 days)


PLATEAU_MAX = 5
PLATEAU_SCORE_EPS = 2        #if score change < eps AND comments change == 0, count as plateau
BATCH_SIZE = 100             #reddit.info says query limit is 100, so possibly supports 100 fullnames per call
TRACKING_CYCLE_SLEEP = 5     #seconds between tracking cycles, to potentially idle when multiple posts aren't being pulled


#cleaning

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
    Append a single row atomically-ish (open/write/flush/close).
    Creates file + header if missing.
    """
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(row)
        f.flush()


#token bucket budget v1

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
        # Refill linearly: capacity per 60 seconds
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


#state model


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
    status: str  # "tracking" | "done"
    stop_reason: str

def load_state(state_path: Path) -> Dict[str, TrackedPost]:
    if not state_path.exists():
        return {}
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    posts = {}
    for pid, d in raw.get("tracked", {}).items():
        posts[pid] = TrackedPost(**d)
    return posts

def save_state(state_path: Path, tracked: Dict[str, TrackedPost]) -> None:
    data = {"tracked": {pid: vars(tp) for pid, tp in tracked.items()}}
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(state_path)


#Reddit client


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



#core loops

def discovery_cycle(
    reddit: praw.Reddit,
    tracked: Dict[str, TrackedPost],
    posts_csv: Path,
    bucket: TokenBucket,
) -> int:
    """
    Returns number of new posts discovered.
    """
    now = utc_now()
    new_count = 0

    # Budget: treat each subreddit listing fetch as 1 request.
    for sub in SUBREDDITS:
        if not bucket.take(1.0):
            # Out of budget; stop discovery early this cycle
            break

        try:
            subreddit = reddit.subreddit(sub)
            for submission in subreddit.new(limit=DISCOVERY_LIMIT_PER_SUB):
                pid = submission.id
                if pid in tracked:
                    continue

                created = int(submission.created_utc)
                fullname = f"t3_{pid}"
                permalink = "https://www.reddit.com" + submission.permalink
                title = submission.title if submission.title else ""

                tp = TrackedPost(
                    post_id=pid,
                    fullname=fullname,
                    subreddit=sub,
                    title=title[:3000],
                    created_utc=created,
                    permalink=permalink,
                    first_seen_utc=now,
                    next_poll_at=now + 5 * 60,   # first snapshot soon
                    last_polled_at=0,
                    last_score=0,
                    last_comments=0,
                    plateau_count=0,
                    status="tracking",
                    stop_reason="",
                )
                tracked[pid] = tp
                new_count += 1

                safe_append_row(
                    posts_csv,
                    header=["post_id", "fullname", "subreddit", "title", "created_utc", "first_seen_utc", "permalink"],
                    row=[pid, fullname, sub, title, iso_utc(created), iso_utc(now), permalink],
                )

        except Exception as e:
            # Don't crash discovery; move on
            print(f"[discovery] error on r/{sub}: {e}")

    return new_count


def pick_due_posts(tracked: Dict[str, TrackedPost], now: int, max_posts: int) -> List[TrackedPost]:
    due = [tp for tp in tracked.values() if tp.status == "tracking" and tp.next_poll_at <= now]
    due.sort(key=lambda x: x.next_poll_at)
    return due[:max_posts]


def tracking_cycle(
    reddit: praw.Reddit,
    tracked: Dict[str, TrackedPost],
    snapshots_dir: Path,
    bucket: TokenBucket,
) -> int:
    """
    Refresh due posts in batches of <= 100, append snapshots.
    Returns number of posts snapshotted.
    """
    now = utc_now()
    horizon_s = TRACKING_HORIZON_DAYS * 24 * 3600

    # How many posts to attempt this cycle depends on available budget.
    # Each batch refresh costs ~1 request (reddit.info).
    # We'll try to spend up to, say, 10 requests per cycle if available.
    max_batches = 10
    # but also cap total posts
    max_posts = max_batches * BATCH_SIZE

    due_posts = pick_due_posts(tracked, now, max_posts=max_posts)
    if not due_posts:
        return 0

    fullnames = [tp.fullname for tp in due_posts]
    total_done = 0

    # daily snapshots file
    snap_csv = snapshots_dir / f"snapshots_{today_utc_yyyymmdd()}.csv"
    header = ["observed_utc", "post_id", "subreddit", "age_minutes", "score", "num_comments", "upvote_ratio", "permalink"]

    for batch in chunked(fullnames, BATCH_SIZE):
        # budget check
        if not bucket.take(1.0):
            break

        # Backoff wrapper (simple)
        attempt = 0
        while True:
            try:
                # reddit.info yields submissions/comments for given fullnames
                refreshed = list(reddit.info(fullnames=batch))
                break
            except Exception as e:
                attempt += 1
                if attempt >= 5:
                    print(f"[tracking] batch failed after retries: {e}")
                    refreshed = []
                    break
                sleep_s = min(60, (2 ** attempt) + random.random())
                print(f"[tracking] error, backing off {sleep_s:.1f}s: {e}")
                time.sleep(sleep_s)

        # Map fullname -> refreshed submission
        ref_map = {f"t3_{s.id}": s for s in refreshed if hasattr(s, "id")}

        for fullname in batch:
            # find tracked post by fullname
            pid = fullname.replace("t3_", "")
            tp = tracked.get(pid)
            if not tp or tp.status != "tracking":
                continue

            # horizon/stop check
            age_s = now - tp.created_utc
            if age_s > horizon_s:
                tp.status = "done"
                tp.stop_reason = "horizon_reached"
                continue

            subm = ref_map.get(fullname)
            if subm is None:
                # Could be deleted/removed or transient failure; reschedule later
                tp.next_poll_at = now + 60 * 60
                continue

            score = int(getattr(subm, "score", 0) or 0)
            num_comments = int(getattr(subm, "num_comments", 0) or 0)
            upvote_ratio = getattr(subm, "upvote_ratio", None)
            if upvote_ratio is None:
                upvote_ratio = ""

            # plateau detection
            score_delta = abs(score - tp.last_score)
            comments_delta = abs(num_comments - tp.last_comments)
            if score_delta < PLATEAU_SCORE_EPS and comments_delta == 0:
                tp.plateau_count += 1
            else:
                tp.plateau_count = 0

            if tp.plateau_count >= PLATEAU_MAX:
                tp.status = "done"
                tp.stop_reason = "plateau"
            else:
                # reschedule based on age
                interval = next_interval_seconds(age_s)
                # add tiny jitter so writes don't align perfectly
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

    # prune very old done posts occasionally to keep state small
    prune_state(tracked, now)

    return total_done


def prune_state(tracked: Dict[str, TrackedPost], now: int) -> None:
    """
    Keep state.json from growing forever:
    - remove posts marked done whose created_utc is older than horizon + 7 days
    """
    cutoff = now - ((TRACKING_HORIZON_DAYS + 7) * 24 * 3600)
    to_del = [pid for pid, tp in tracked.items() if tp.status == "done" and tp.created_utc < cutoff]
    for pid in to_del:
        del tracked[pid]


# Main

def main() -> None:
    load_dotenv()
    out_dir = os.getenv("ONEDRIVE_DATA_DIR")
    if not out_dir:
        raise RuntimeError("Missing ONEDRIVE_DATA_DIR in .env (should point to your local OneDrive folder).")

    discovery_interval = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "60"))
    max_rpm = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))

    base = Path(out_dir).expanduser()
    ensure_dir(base)

    posts_csv = base / "posts.csv"
    state_path = base / "state.json"

    reddit = make_reddit()
    bucket = TokenBucket.create(capacity_per_minute=max_rpm)

    tracked = load_state(state_path)
    print(f"[startup] loaded {len(tracked)} tracked posts")
    print(f"[startup] writing to: {base}")

    last_discovery = 0

    while True:
        now = utc_now()

        # Discovery every N seconds
        if now - last_discovery >= discovery_interval:
            new_posts = discovery_cycle(reddit, tracked, posts_csv, bucket)
            last_discovery = now
            if new_posts:
                print(f"[discovery] +{new_posts} new posts (tracked now: {sum(1 for t in tracked.values() if t.status=='tracking')})")

        # Tracking continuously (but budget-limited)
        snap_count = tracking_cycle(reddit, tracked, base, bucket)
        if snap_count:
            print(f"[tracking] snapshotted {snap_count} posts (budget tokens: {bucket.tokens:.1f})")

        # Persist state frequently (safe for restarts)
        save_state(state_path, tracked)

        # Sleep a bit
        time.sleep(TRACKING_CYCLE_SLEEP)


if __name__ == "__main__":
    main()