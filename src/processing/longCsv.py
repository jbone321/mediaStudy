import os
import json
import pandas as pd
from pathlib import Path

class jsonToLongCsv:
	def __init__(self, baseDir="data/raw/youtube", outputDir="data/processed", outputFile="statsLong.csv"):
		self.baseDir = Path(baseDir)
		self.baselineDir = self.baseDir / "baselines"
		self.statsDir = self.baseDir / "lifecycleTracking"
		self.outputPath = Path(outputDir) / outputFile



	def loadBaselines(self) -> pd.DataFrame:
		rows: List[Dict] = []

		#UPDATE: category id -> category Name
		catMap = {
			"24": "Entertainment",
			"10": "Music",
			"20": "Gaming",
			"27": "Education",
			"26": "Howto & Style"
		}

		for file in self.baselineDir.glob("*.json"):
			try:
				with open(file, "r", encoding="utf-8") as f:
					data = json.load(f)

				#UPDATE: category id -> category name
				# missed this first time
				catId = data.get("categoryId")
				catName = catMap.get(catId, "Unknown")
				rows.append({
					"videoId": data.get("videoId"),
					"title": data.get("title"),
					"publishedAt": data.get("publishedAt"),
					"duration": data.get("duration"),
					"channelTitle": data.get("channelTitle"),
					"firstSeen": data.get("firstSeen"),
					"category": catName #UPDATE
				})
			except Exception as e:
				print(f"Error loading baseline {file.name}: {e}")
			
		df = pd.DataFrame(rows)
		print(f"Loaded {len(df)} baselines")
		return df

	def loadStats(self) -> pd.DataFrame:
		stats: List[Dict] = []
		for file in self.statsDir.glob("stats_delta_*.json"):
			try:
				timeStr = file.stem.split("stats_delta_")[1]
				timestamp = pd.to_datetime(timeStr, format="%Y%m%d_%H%M%S")
				with open(file, "r", encoding="utf-8") as f:
					data = json.load(f)
				
				for item in data.get("items", []):
					stats.append({
						"videoId": item["videoId"],
						"pollTimestamp": timestamp,
						"viewCount": item["viewCount"],
						"likeCount": item["likeCount"],
						"commentCount": item["commentCount"]
					})
			except Exception as e:
				print(f"Error reading stats from {file.name}: {e}")

		df = pd.DataFrame(stats)
		print(f"Loaded {len(df)} stats rows")
		return df

	def convert(self) -> pd.DataFrame:
		dfBase = self.loadBaselines()
		dfStats = self.loadStats()
		dfFinal = dfStats.merge(dfBase, on="videoId", how="left")
		dfFinal = dfFinal.sort_values(["videoId", "pollTimestamp"])
		return dfFinal

	def save(self, df: pd.DataFrame):
		df.to_csv(self.outputPath, index=False, encoding="utf-8")
		print(f"Saved at {self.outputPath}")

	def run(self):
		try:
			df = self.convert()
			self.save(df)
		except Exception as e:
			print(f"Conversion failed: {e}")

if __name__ == "__main__":
	converter = jsonToLongCsv()
	converter.run()
