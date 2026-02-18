import json
from pathlib import Path
import googleapiclient.discovery
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
import os

class Backfiller:
	# did not originally save the category by mistake so have to backfill
	
	def __init__(self):
		load_dotenv()
		self.key = os.getenv("YOUTUBE_API_KEY")
		self.youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=self.key)
		self.baseDir = Path("data/raw/youtube/baselines")

	def getCatIds(self, videoId):
		try:
			request = self.youtube.videos().list(part="snippet", id=videoId)
			response = request.execute()
			items = response.get("items", [])

			if items:
				return items[0]["snippet"].get("categoryId")
			return None

		except HttpError as e:
			print("API error on {videoId}")
		except Exception as e:
			print(f"Unexpected error on {videoId}: {e}")

	def backfill(self, filePath):
		try:
			with open(filePath, "r", encoding="utf-8") as f:
				data = json.load(f)
			

			#chceks for category first
			if "categoryId" in data:
				return False

			videoId = data.get("videoId")
			if not videoId:
				print("No video id")
				return False
			
			catId = self.getCatIds(videoId)
			if not catId:
				print("No category found")
				return False

			data["categoryId"] = catId
			with open(filePath, "w", encoding="utf-8") as f:
				json.dump(data, f, indent=4)

			print(f"{videoId} has been backfilled with {catId}")
			return True

		except Exception as e:
			print(f"Failed for {filePath.name}")
			return False

	def run(self):
		files = list(self.baseDir.glob("*.json"))

		updated = skipped = failed = 0

		for f in files:
			if self.backfill(f):
				updated += 1
			else:
				skipped += 1

		print(f"Finished backfilling. Updated: {updated} Skipped: {skipped}")

if __name__ == "__main__":
	print("Starting backfill.")
	backfiller = Backfiller()
	backfiller.run()
