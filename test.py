import os
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("YOUTUBE_API_KEY")
if not api_key:
    print("No API key found in environment")
    exit(1)

print(f"Using API key: {api_key[:10]}...")  # partial for safety

youtube = build("youtube", "v3", developerKey=api_key)

# ────────────────────────────────────────────────
# Category search test (pick one from your list)
# ────────────────────────────────────────────────
category_id = "24"  # Example: Entertainment
# Other options you can swap in:
# "10" → Music
# "20" → Gaming
# "27" → Education
# "26" → Howto & Style

# Force recency: videos from last 7 days
seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"

request = youtube.search().list(
    part="snippet",
    type="video",
    videoCategoryId=category_id,       # ← this makes it a category search
    publishedAfter=seven_days_ago,     # ← forces recent videos
    maxResults=10,                     # small for testing
    order="relevance"                  # or try "date" to compare
)

try:
    response = request.execute()
    items = response.get("items", [])
    print(f"Found {len(items)} results in category {category_id} (last 7 days)")
    
    if items:
        print("\nFirst result:")
        print("  Title:", items[0]["snippet"]["title"])
        print("  Video ID:", items[0]["id"]["videoId"])
        print("  Published:", items[0]["snippet"]["publishedAt"])
        print("  Channel:", items[0]["snippet"]["channelTitle"])
        print("  Description (short):", items[0]["snippet"]["description"][:150] + "...")
    else:
        print("Response had no items. Full response keys:", list(response.keys()))
        print("Full response:", response)
        
        # Optional: show if there was any error/info
        if "error" in response:
            print("Error details:", response["error"])

except Exception as e:
    print("API call failed:", str(e))
