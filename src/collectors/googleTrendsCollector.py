import pandas as pd
from pytrends.request import TrendReq

class GoogleTrendsCollector():
	def __init__(self):
		self.pytrends = TrendReq(hl="en-US", tz=360)

	def gatherHistory(self, cats: list, timeframe):

		result = None

		for cat in cats:
			self.pytrends.build_payload(kw_list=[""], timeframe=timeframe, cat=cat, geo="", gprop="")

			dfHistory = self.pytrends.interest_over_time()

			if dfHistory.empty:
				print(f"No data for category {cat}")
				continue

			if "" in dfHistory.columns:
				dfHistory = dfHistory.rename(columns={"": f"cat{cat}"})

			if "isPartial" in dfHistory.columns:
				dfHistory = dfHistory.drop(columns=["isPartial"])

			if result is None:
				result = dfHistory
			else:
				result = result.join(dfHistory, how="outer")

		return result
