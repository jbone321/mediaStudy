import pandas as pd
from pytrends.request import TrendReq
import time
import random

class GoogleTrendsCollector():
	def __init__(self):
		self.pytrends = TrendReq(hl="en-US", tz=360)

	def gatherHistory(self, cats: list, timeframe):

		result = None

		counter = 0

		for cat in cats:
			time.sleep(random.uniform(8,15))

			for attempt in range(5):
				try:
					self.pytrends.build_payload(kw_list=[""],cat=cat,timeframe=timeframe)
					dfHistory = self.pytrends.interest_over_time()
					print(f"cat {cat} columns: {dfHistory.columns.tolist()}")
					break
				except Exception as e:
					if "429" in str(e):
						sleepTime = (2 ** attempt) + random.uniform(5, 10)
						time.sleep(sleepTime)
						self.pytrends = TrendReq(hl="en-US", tz=360)
					else:
						raise

			if dfHistory.empty:
				print(f"No data for category {cat}")
				continue

			if "" in dfHistory.columns:
				dfHistory = dfHistory.rename(columns={"": f"cat{cat}"})

			if "isPartial" in dfHistory.columns:
				dfHistory = dfHistory.drop(columns=["isPartial"])

			if result is not None:
				overlap = [col for col in dfHistory.columns if col in result.columns]
				if overlap:
					print(f"Dropping overlapping columns for category {cat}: {overlap}")
					dfHistory = dfHistory.drop(columns=overlap)

			if result is None:
				result = dfHistory
			else:
				result = result.join(dfHistory, how="outer")

			counter += 1
			if counter % 5 == 0:
				time.sleep(random.uniform(60,120))


		return result
