import os
import json
import pandas as pd
from pathlib import Path

class JsonToCommentCsv:
    def __init__(self, baseDir="data/raw/youtube", outputDir="data/processed", outputFile="comments.csv"):
        self.baseDir = Path(baseDir)
        self.commentsDir = self.baseDir / "lifecycleTracking"
        self.outputPath = Path(outputDir) / outputFile

    def loadComments(self) -> pd.DataFrame:
        rows: List[Dict] = []
        for file in self.commentsDir.glob("comments_*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                videoId = data.get("videoId") or file.stem.split("comments_")[1]

                seen = {}
                for snapshot in data.get("history", []):
                    for comment in snapshot.get("comments", []):
                        key = (comment.get("author"), comment.get("publishedAt"))
                        if key not in seen:
                            seen[key] = {
                                "videoId": videoId,
                                "author": comment.get("author"),
                                "text": comment.get("text"),
                                "publishedAt": comment.get("publishedAt"),
                                "likes": comment.get("likes"),
                            }

                rows.extend(seen.values())

            except Exception as e:
                print(f"Error loading {file.name}: {e}")

        df = pd.DataFrame(rows)
        print(f"Loaded {len(df)} comments")
        return df

    def run(self):
        df = self.loadComments()
        self.outputPath.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.outputPath, index=False)
        print(f"Saved to {self.outputPath}")

if __name__ == "__main__":
    loader = JsonToCommentCsv()
    loader.run()
