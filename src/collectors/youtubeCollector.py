import os
import json
from datetime import datetime, timedelta
import googleapiclient.discovery
from googleapiclient.errors import HttpError

class YoutubeCollector:
	def __init__(self, apiKey, baseDir="data/raw/youtube"):
		self.apiKey = apiKey
		self.baseDir = baseDir
		self.youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=self.apiKey)
		os.makedirs(self.baseDir, exist_ok=True)
		os.makedirs(os.path.join(self.baseDir, "baselines"), exist_ok=True)
		os.makedirs(os.path.join(self.baseDir, "lifecycleTracking"), exist_ok=True)

	def getVideoCategories(self, regionCode="US"):
		"""
		fetches video categories so that data pulled is
	 	closer to searching by subreddit for Reddit
		saves to json for reference
		Returns: a dictionary of categories and their IDs
		"""
		try:
			request = self.youtube.videoCategories().list(
				part="snippet",
				regionCode=regionCode
			)
			response = request.execute()

			categories = {}
			for item in response.get("items", []):
				if item["snippet"]["assignable"]:
					title = item["snippet"]["title"]
					catId = item["id"]
					categories[title] = catId

			outputFile = os.path.join(self.baseDir, f"categories_{regionCode}.json")
			with open(outputFile, "w", encoding="utf-8") as f:
				json.dump(categories, f, indent=4)

			print(f"Categories saved: {outputFile}")
			return categories

		except Exception as e:
			print(f"Error fetching categories: {e}")
			return {}

	def searchVideos(self, query="", categoryId=None, maxResults=30, order="relevance", regionCode="US"):
		"""
		Category searches videos with an optional keyword filter
		order = date is preferred for our research to try and pull newer videos initially
		^^ was returning nothing so forcing recency using a different method
		saves metadata that will not change on subsequent checks once per video
		Returns: a list of dictionaries where each entry is a videos data
		"""
		if not categoryId and not query:
			raise ValueError("Provide at least query or categoryId")

		try:
			params = {
				"part": "snippet",
				"type": "video",
				"maxResults": maxResults,
				"order": order,
				"regionCode": regionCode
			}

			if categoryId:
				params["videoCategoryId"] = categoryId
			
			cats = self.getVideoCategories(regionCode=regionCode)
			catsReverse = {id: title for title, id in cats.items()}
			keyword = catsReverse.get(str(categoryId), "")

			if keyword:
				params["q"] = keyword

			#only videos in last 7 days
			twoDays = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
			params["publishedAfter"] = twoDays			

			request = self.youtube.search().list(**params)
			response = request.execute()

			items = response.get("items", [])
			
			results = []
			videoIds = []

			for item in response.get("items", []):
				if item.get("id", {}).get("kind") == "youtube#video":
					snippet = item["snippet"]
					video = {
						"videoId": item["id"]["videoId"],
						"title": snippet.get("title"),
						"channelTitle": snippet.get("channelTitle"),
						"channelId": snippet.get("channelId"),
						"publishedAt": snippet.get("publishedAt"),
						"description": snippet.get("description")[:200] + "..." if len(snippet.get("description", "")) > 200 else snippet.get("description"),
						"thumbnail": snippet["thumbnails"].get("medium", {}).get("url")
					}
					results.append(video)
					videoIds.append(video["videoId"])

					# save baseline once
					self._saveBaseline(video, categoryId=categoryId)

			# save search filters
			nameNormal = (query or f"cat_{categoryId or 'all'}").replace(" ", "_").replace("/", "_")
			resultsFile = os.path.join(self.baseDir, f"search_{nameNormal}_{regionCode}_{order}.json")
			with open(resultsFile, "w", encoding="utf-8") as f:
				json.dump({
					"metadata": {
						"query": query,
						"categoryId": categoryId,
						"order": order,
						"region": regionCode,
						"maxResults": maxResults
					},
					"items": results
				}, f, indent=4)

			print(f"Search results and  baselines saved at {resultsFile}")
			return results, videoIds	# videos being tracked

		except googleapiclient.errors.HttpError as e:
			print(f"API error: {e.resp.status} - {e.content.decode('utf-8')}")
			return [], []
		except Exception as e:
			print(f"Error: {e}")
			return [], []

	def _saveBaseline(self, video, categoryId=None):
		baseline = {
			"videoId": video["videoId"],
			"title": video.get("title"),
			"publishedAt": video.get("publishedAt"),
			"duration": video.get("duration"),
			"channelTitle": video.get("channelTitle"),
			"channelId": video.get("channelId"),
			"firstSeen": datetime.utcnow().isoformat() + "Z",
			"categoryId": categoryId
		}

		filePath = os.path.join(self.baseDir, "baselines", f"{video['videoId']}.json")
		with open(filePath, "w", encoding="utf-8") as f:
			json.dump(baseline, f, indent=4)

		print(f"Baseline saved at {filePath}")

	def getVideoStats(self, videoIds):
		"""
		fetches views, likes, and comment counts to build lifecylce time series
		saves delta snapshot each run
		"""
		if not videoIds:
			return []

		try:
			if isinstance(videoIds, list):
				# batches videos by 50
				if len(videoIds) > 50:
					allResults = []
					for i in range(0, len(videoIds), 50):
						batch = videoIds[i:i+50]
						allResults.extend(self.getVideoStats(batch))
					return allResults
				idString = ",".join(videoIds)
			else:
				idString = videoIds

			request = self.youtube.videos().list(
				part="statistics",
				id=idString
			)
			response = request.execute()

			results = []
			for item in response.get("items", []):
				stats = item.get("statistics", {})

				results.append({
					"videoId": item["id"],
					"viewCount": int(stats.get("viewCount", 0)),
					"likeCount": int(stats.get("likeCount", 0)),
					"commentCount": int(stats.get("commentCount", 0)),
					"pollTimestamp": datetime.utcnow().isoformat() + "Z"
				})

			trackingDir = os.path.join(self.baseDir, "lifecycleTracking")
			timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
			outputFile = os.path.join(trackingDir, f"stats_delta_{timestamp}.json")

			with open(outputFile, "w", encoding="utf-8") as f:
				json.dump({"items": results}, f, indent=4)

			print(f"Delta stats saved at {outputFile}")
			return results

		except Exception as e:
			print(f"Error fetching stats: {e}")
			return []

	def getComments(self, videoId, maxComments=25):
		# gathers top-level comments at the time gathered no replies

		comments = []

		try:
			request = self.youtube.commentThreads().list(
				part="snippet",
				videoId=videoId,
				maxResults=maxComments,
				textFormat="plainText",
				order="relevance"
			)

			
			response = request.execute()

			for item in response.get("items", []):
				comment = item["snippet"]["topLevelComment"]["snippet"]
				comments.append({
					"text": comment["textDisplay"],
					"author": comment["authorDisplayName"],
					"likes": comment["likeCount"],
					"publishedAt": comment["publishedAt"]
				})

				if len(comments) >= maxComments:
					break

			request = self.youtube.commentThreads().list_next(request, response)

			comments = comments[:maxComments]

			# one file per video
			trackingDir = os.path.join(self.baseDir, "lifecycleTracking")
			os.makedirs(trackingDir, exist_ok=True)

			historyFile = os.path.join(trackingDir, f"comments_{videoId}.json")

			# load or start new
			if os.path.exists(historyFile):
				with open(historyFile, "r", encoding="utf-8") as f:
					history = json.load(f)
			else:
				history = {
					"videoId": videoId,
					"history": []
				}

			timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
			newSnapshot = {
				"fetchedAt": timestamp,
				"commentCount": len(comments),
				"comments": comments
			}

			#only save if differents from last snapshor
			if history["history"] and history["history"][-1]["commentCount"] == newSnapshot["commentCount"]:
				print(f"Skipping save for {videoId}, no change in comment count")
			else:
				history["history"].append(newSnapshot)

			# Save
			with open(historyFile, "w", encoding="utf-8") as f:
				json.dump(history, f, indent=4)

			print(f"{len(comments)} comments saved(snapshots: {len(history['history'])})")

			return comments

		except googleapiclient.errors.HttpError as e:
			status = e.resp.status
			if status == 403:
				print(f"Comments disabled or forbidden for {videoId}")
				return []
			if status == 404:
				print(f"Video not found or removed: {videoId}")
				return []
			
			print(f"HTTP error {status} for {videoId}: {e.content.decode('utf-8')}")
			return []

		except Exception as e:
			print(f"Unexpected error fetching comments for {videoId}: {str(e)}")
			return []
