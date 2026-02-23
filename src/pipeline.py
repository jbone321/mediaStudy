import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
load_dotenv()

# CUSTOM CLASSES
from collectors.youtubeCollector import YoutubeCollector
from processing.sentimentAnalyzer import SentimentAnalyzer


class PipelineConfig:
	"""
	All configuration values held in one place
	Makes tweaking the pipeline much quicker
	"""
	def __init__(self):
		# YouTube collection settings
		self.youtube = {
			"apiKey": os.getenv("YOUTUBE_API_KEY"),
			"baseDir": "data/raw/youtube",
			"categories": ["Entertainment", "Music", "Gaming", "Education", "Howto & Style"],
			"videosPerCategory": 30,
			"region": "US",
			"order": "date"
		}

		# Sentiment analysis settings
		self.sentiment = {
			"enabled": True,
			"sources": ["comments", "titles", "descriptions"],
			"outputDir": "data/processed/sentiment"
		}

	def validate(self) -> bool:
		# quick check that required settings exist before running
		if not self.youtube.get("apiKey"):
			print("Error: YOUTUBE_API_KEY is missing from environment variables.")
			print("Add it to .env file or export it before running.")
			return False
		return True


class MediaPipeline:
	"""
	Controller for the pipeline
	holds the collector and analyzer objects and coordinates the steps
	designed to be easily expandable to add other platforms
	"""

	def __init__(self, config: PipelineConfig):
		self.config = config
		self.collector: YoutubeCollector | None = None
		self.analyzer: SentimentAnalyzer | None = None
		self._initializeComponents()

	def _initializeComponents(self):
		# intializes pipeline components and ensures correct API authentication
		if self.config.youtube["apiKey"]:
			self.collector = YoutubeCollector(self.config.youtube["apiKey"], baseDir=self.config.youtube["baseDir"])
		else:
			print("No YouTube API key")

		self.analyzer = SentimentAnalyzer()

	def ensureDirectories(self):
		for section in [self.config.youtube, self.config.sentiment]:
			for key in ["baseDir", "outputDir"]:
				path = section.get(key)
				if path:
					os.makedirs(path, exist_ok=True)
					# Create subfolders used by YoutubeCollector
					os.makedirs(os.path.join(path, "baselines"), exist_ok=True)
					os.makedirs(os.path.join(path, "lifecycleTracking"), exist_ok=True)

	def loadTrackedVideos(self) -> List[str]:
		trackingFile = os.path.join(self.config.youtube["baseDir"], "tracked_video_ids.json")

		if os.path.exists(trackingFile):
			with open(trackingFile, "r", encoding="utf-8") as f:
				return json.load(f)
		return []

	def saveTrackedVideos(self, videoIds: List[str]):
		trackingFile = os.path.join(self.config.youtube["baseDir"], "tracked_video_ids.json")

		with open(trackingFile, "w", encoding="utf-8") as f:
			json.dump(videoIds, f, indent=4)
		print(f"Tracked video list updated,  now tracking {len(videoIds)} videos total")

	def collectYoutubeData(self):
		"""
		1. Load existing tracked videos
		2. Search for newest videos in each category
		3. Add any genuinely new ones to the tracking list
		4. Fetch fresh stats for ALL tracked videos (builds time-series data)
		"""
		if not self.collector:
			print("Cannot collect YouTube data → no API key provided.")
			return

		print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting YouTube collection...")

		trackingFile = os.path.join(self.config.youtube["baseDir"], "tracked_video_ids.json")
		allTrackedIds = self.loadTrackedVideos()

		# refresh category list
		categories = self.collector.getVideoCategories(self.config.youtube["region"])

		newIdsThisRun: List[str] = []

		# grab newest videos
		for catName in self.config.youtube["categories"]:
			catId = categories.get(catName)
			if not catId:
				print(f"Skipping missing category {catName}")
				continue

			print(f"Searching newest videos in {catName} ({catId})")
			i, newIds = self.collector.searchVideos(
				categoryId=catId,
				maxResults=self.config.youtube["videosPerCategory"],
				order=self.config.youtube["order"],
				regionCode=self.config.youtube["region"]
			)
			newIdsThisRun.extend(newIds)

		# new IDS only
		previouslyKnown = set(allTrackedIds)
		actuallyNew = [vid for vid in newIdsThisRun if vid not in previouslyKnown]

		if actuallyNew:
			print(f"Found {len(actuallyNew)} brand new videos to start tracking")
			allTrackedIds.extend(actuallyNew)
			self.saveTrackedVideos(allTrackedIds)

			for video in actuallyNew:
				self.collector.getComments(video)
		# Update stats
		if allTrackedIds:
			print(f"Pulling current stats for {len(allTrackedIds)} videos")
			self.collector.getVideoStats(allTrackedIds)

		print("YouTube data collection finished.\n")

	def runSentimentAnalysis(self):
		"""
		Runs VADER sentiment analysis on multiple text sources
		Results are saved in one combined JSON file with source tagging.
		"""
		if not self.analyzer:
			print("Cannot run sentiment, analyzer not initialized.")
			return

		print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting sentiment analysis...")

		processedDir = self.config.sentiment["outputDir"]
		os.makedirs(processedDir, exist_ok=True)

		allResults: List[Dict[str, Any]] = []

		# Process each requested source
		for source in self.config.sentiment["sources"]:
			print(f"Analyzing source: {source}")

			if source == "comments":
				# Look in the folder where getComments saves files
				commentsDir = os.path.join(self.config.youtube["baseDir"], "lifecycleTracking")
				if not os.path.exists(commentsDir):
					print("No comments folder yet run collection first")
					continue

				commentFiles = list(Path(commentsDir).glob("comments_*.json"))
				print(f"Found {len(commentFiles)} comment files")

				for filePath in commentFiles:
					with open(filePath, "r", encoding="utf-8") as f:
						data = json.load(f)
					videoId = data.get("videoId")
					texts = [c["text"] for c in data.get("comments", []) if c.get("text")]
					if not texts:
						continue

					scores = self.analyzer.analyzeTexts(texts)
					for comment, score in zip(data["comments"], scores):
						allResults.append({
							"videoId": videoId,
							"source": "comment",
							"text": comment["text"],
							"publishedAt": comment.get("publishedAt"),
							"sentiment": score,
							"overall": "positive" if score["compound"] > 0.05 else "negative" if score["compound"] < -0.05 else "neutral",
							"processedAt": datetime.utcnow().isoformat() + "Z"
						})

			elif source in ["titles", "descriptions"]:
				# titles and descriptions are in baseline files
				baselinesDir = os.path.join(self.config.youtube["baseDir"], "baselines")
				if not os.path.exists(baselinesDir):
					print(f"    No baselines folder found for {source}")
					continue

				baselineFiles = list(Path(baselinesDir).glob("*.json"))
				print(f"    Found {len(baselineFiles)} baseline files")

				for filePath in baselineFiles:
					with open(filePath, "r", encoding="utf-8") as f:
						baseline = json.load(f)
					videoId = baseline.get("videoId")
					key = "title" if source == "titles" else "description"
					text = baseline.get(key)
					if not text:
						continue

					score = self.analyzer.analyzeText(text)
					allResults.append({
						"videoId": videoId,
						"source": source.rstrip("s"),
						"text": text,
						"publishedAt": baseline.get("publishedAt"),
						"sentiment": score,
						"overall": "positive" if score["compound"] > 0.05 else "negative" if score["compound"] < -0.05 else "neutral",
						"processedAt": datetime.utcnow().isoformat() + "Z"
					})

			else:
				print(f"Skipping unknown source: {source}")

		# Save
		if allResults:
			timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
			outputFile = os.path.join(processedDir, f"sentiment_multi_source_{timestamp}.json")
			with open(outputFile, "w", encoding="utf-8") as f:
				json.dump(allResults, f, indent=4)
			print(f"Saved {len(allResults)} sentiment items to: {outputFile}")
		else:
			print("No text found to analyze this run")

		print("Sentiment analysis finished.\n")

	def run(self, runYoutube: bool = False, runSentiment: bool = False, updateComments: bool = False):
		# runs the selected parts of the pipeline, will be all mostly but may need to check individual parts once we start adding
		if not self.config.validate():
			print("Pipeline stopped due to invalid configuration.")
			return

		self.ensureDirectories()

		if runYoutube:
			self.collectYoutubeData()

		if updateComments:
			print("Running full comment update for all tracked videos.")
			allTrackedIds = self.loadTrackedVideos()
			if allTrackedIds:
				for vid in allTrackedIds:
					self.collector.getComments(vid)
			else:
				print("No tracked videos yet")

		if runSentiment:
			self.runSentimentAnalysis()

		if not (runYoutube or runSentiment or updateComments):
			print("No tasks selected. Use --youtube, --update-comments or --all")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Media Attention Lifecycle Pipeline")
	parser.add_argument("--youtube", action="store_true", help="Run YouTube collection")
	parser.add_argument("--update-comments", action="store_true", help="Force full comment update for ALL tracked videos")
	parser.add_argument("--all", action="store_true", help="Run everything, for multi-platform functionality")
	args = parser.parse_args()

	config = PipelineConfig()
	pipeline = MediaPipeline(config)

	pipeline.run(
		runYoutube=args.youtube or args.all,
		runSentiment=True,
		updateComments=args.update_comments or args.all
	)
