import os
import time
import json
import math
import pandas as pd
import praw
from pathlib import Path
from prawcore.exceptions import RequestException, ResponseException, ServerError
from praw.exceptions import RedditAPIException

from praw.models import MoreComments

#config
TOP_N = 5
SAVE_EVERY = 200
SLEEP_BETWEEN_POSTS = 1.5   # adjust to ~1.5–2.0 for safer long unattended runs

OUTPUT_FILE = "top5_comments.csv"
CHECKPOINT_FILE = "comment_scrape_checkpoint.json"
ERROR_FILE = "comment_scrape_errors.csv"



import os
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

cfg = dotenv_values(env_path)
print("Loaded keys:", list(cfg.keys()))

required = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"]
missing = [k for k in required if not os.getenv(k)]

if missing:
    raise RuntimeError(
        f"Missing env vars: {missing}\n"
        f"Checked .env at: {env_path}\n"
        f"Exists: {env_path.exists()}\n"
        f"CWD: {Path.cwd()}"
    )
#Reddit Client setup
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
    ratelimit_seconds=1200,
    timeout=30
)

#Loads posts
posts_df = pd.read_csv(r'C:\reddit_attention_tracker\posts.csv')

print("Columns found:")
print(posts_df.columns.tolist())

# Ensure required columns exist
required_cols = ["post_id", "subreddit", "permalink", "title"]
missing = [c for c in required_cols if c not in posts_df.columns]

if missing:
    raise RuntimeError(f"Missing required columns: {missing}")

# Keep one row per post
posts_df = (
    posts_df[["post_id", "subreddit", "permalink", "title"]]
    .drop_duplicates(subset="post_id")
    .rename(columns={
        "permalink": "post_permalink"
    })
    .copy()
)

posts_df["post_id"] = posts_df["post_id"].astype(str)

total_posts = len(posts_df)
print(f"Loaded {total_posts:,} unique posts")

#--------------------------
#Loads existing progress
done_ids = set()
results_buffer = []
errors_buffer = []

if Path(CHECKPOINT_FILE).exists():
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        ckpt = json.load(f)
        done_ids = set(ckpt.get("done_post_ids", []))

if Path(OUTPUT_FILE).exists():
    existing_df = pd.read_csv(OUTPUT_FILE, usecols=["post_id"])
    done_ids.update(existing_df["post_id"].astype(str).unique())

completed_at_start = len(done_ids)

#---------
#Helpers
start_time = time.time()
processed_this_run = 0
comments_saved_this_run = 0

def format_seconds(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def append_df_to_csv(df, filename):
    if df.empty:
        return
    file_exists = Path(filename).exists()
    df.to_csv(filename, mode="a", header=not file_exists, index=False)

def save_progress():
    global results_buffer, errors_buffer

    if results_buffer:
        append_df_to_csv(pd.DataFrame(results_buffer), OUTPUT_FILE)
        results_buffer = []

    if errors_buffer:
        append_df_to_csv(pd.DataFrame(errors_buffer), ERROR_FILE)
        errors_buffer = []

    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"done_post_ids": list(done_ids)}, f)

def print_progress():
    completed_total = len(done_ids)
    remaining = total_posts - completed_total
    elapsed = time.time() - start_time

    # posts per second for THIS run
    rate = processed_this_run / elapsed if elapsed > 0 else 0
    eta_seconds = remaining / rate if rate > 0 else float("inf")

    percent = (completed_total / total_posts) * 100 if total_posts else 0

    print(
        f"[PROGRESS] "
        f"{completed_total:,}/{total_posts:,} posts done "
        f"({percent:5.2f}%) | "
        f"comments saved this run: {comments_saved_this_run:,} | "
        f"elapsed: {format_seconds(elapsed)} | "
        f"rate: {rate*60:,.1f} posts/min | "
        f"ETA: {format_seconds(eta_seconds) if math.isfinite(eta_seconds) else 'N/A'}"
    )

#Main
for idx, row in enumerate(posts_df.itertuples(index=False), start=1):
    post_id = str(row.post_id)

    if post_id in done_ids:
        continue

    while True:

        try:
            submission = reddit.submission(id=post_id)
            submission.comment_sort = "top"

            kept = 0

            # Top-level comments only
            for c in submission.comments:
                if kept >= TOP_N:
                    break

                # Skip MoreComments placeholders
                if isinstance(c, MoreComments):
                    continue

                body = getattr(c, "body", None)
                author = str(c.author) if c.author else None

                if not body or body in ("[deleted]", "[removed]"):
                    continue
                if author == "AutoModerator":
                    continue
                if getattr(c, "stickied", False):
                    continue

                results_buffer.append({
                    "post_id": post_id,
                    "subreddit": row.subreddit,
                    "post_title": row.title,
                    "post_permalink": row.post_permalink,
                    "comment_id": c.id,
                    "comment_author": author,
                    "comment_body": body,
                    "comment_score": c.score,
                    "comment_created_utc": c.created_utc,
                    "comment_permalink": f"https://www.reddit.com{c.permalink}",
                    "is_submitter": getattr(c, "is_submitter", False),
                    "parent_id": getattr(c, "parent_id", None),
                    "depth": getattr(c, "depth", None),
                    "rank_within_post": kept + 1
                })
                kept += 1
                comments_saved_this_run += 1

            done_ids.add(post_id)
            processed_this_run += 1

            if processed_this_run % SAVE_EVERY == 0:
                save_progress()
                print_progress()

            time.sleep(SLEEP_BETWEEN_POSTS)
            break

        except RedditAPIException as e:
            print(f"[API EXCEPTION] post {post_id}: {e}")
            wait_s = 120
            print(f"Sleeping {wait_s}s and retrying...")
            time.sleep(wait_s)

        except (RequestException, ResponseException, ServerError) as e:
            print(f"[NETWORK/SERVER] post {post_id}: {e}")
            wait_s = 60
            print(f"Sleeping {wait_s}s and retrying...")
            time.sleep(wait_s)

        except Exception as e:
            print(f"[OTHER ERROR] post {post_id}: {e}")

            errors_buffer.append({
                "post_id": post_id,
                "subreddit": row.subreddit,
                "post_title": row.title,
                "error": str(e)
            })

            done_ids.add(post_id)
            processed_this_run += 1

            if processed_this_run % SAVE_EVERY == 0:
                save_progress()
                print_progress()

            break


# Final save
save_progress()
print_progress()
print("Finished.")