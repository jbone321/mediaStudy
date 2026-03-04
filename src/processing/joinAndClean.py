import pandas as pd

stats1 = pd.read_csv("data/processed/statsLong.csv")
stats2 = pd.read_csv("data/processed/youtube_popular_videos.csv")

stats1["pollTimestamp"] = pd.to_datetime(stats1["pollTimestamp"], errors='coerce').dt.tz_localize(None)
stats2["pollTimestamp"] = pd.to_datetime(stats2["pollTimestamp"], errors='coerce').dt.tz_localize(None)
stats1 = stats1.sort_values(["videoId", "pollTimestamp"])
stats2 = stats2.sort_values(["videoId", "pollTimestamp"])

stats1 = stats1.drop("duration", axis=1)

dfMerged = pd.concat([stats1, stats2], ignore_index=True)

dfMerged["viewsLag1"] = dfMerged.groupby("videoId")["viewCount"].shift(1)
dfMerged["likesLag1"] = dfMerged.groupby("videoId")["likeCount"].shift(1)
dfMerged["commentsLag1"] = dfMerged.groupby("videoId")["commentCount"].shift(1)

dfMerged["pctChangeViews"] = dfMerged.groupby("videoId")["viewCount"].pct_change()
dfMerged["pctChangeLikes"] = dfMerged.groupby("videoId")["likeCount"].pct_change()
dfMerged["pctChangeComments"] = dfMerged.groupby("videoId")["commentCount"].pct_change()

dfMerged["viewsRollingMean"] = dfMerged.groupby("videoId")["viewCount"].rolling(window=3).mean().reset_index(0, drop=True)
dfMerged["viewsRollingStd"] = dfMerged.groupby("videoId")["viewCount"].rolling(window=3).std().reset_index(0, drop=True)

dfMerged["engagementRate"] = (dfMerged["likeCount"] + dfMerged["commentCount"]) / dfMerged["viewCount"]

dfMerged["publishedAt"] = pd.to_datetime(dfMerged["publishedAt"], errors='coerce').dt.tz_localize(None)
dfMerged["daysSinceUpload"] = (dfMerged["pollTimestamp"] - dfMerged["publishedAt"]).dt.days

dfMerged = dfMerged.dropna(subset=['videoId', 'viewCount', 'likeCount', 'commentCount', 'pollTimestamp', 'publishedAt'])

dfMerged = dfMerged.fillna(0)

dfMerged.to_csv("data/processed/mergedStats.csv", index=False)