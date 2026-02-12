import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

class SentimentAnalyzer:
	"""
	sentiment analyzer using VADER for all text based data obtained from our platforms
	VADER is strong for social media text that typically contains a lot of slang and emoticons
	"""
	
	def __init__(self):
		self.analyzer = SentimentIntensityAnalyzer()

	def analyzeText(self, text):
		# for video descriptions and original post
		if not text:
			return {"neg" : 0.0, "neu" : 0.0, "pos" : 0.0, "compound" : 0.0}
		return self.analyzer.polarity_scores(text)

	def analyzeTexts(self, texts):
		"""`
		for list of text like comments
		returns a list of dicts with scores for each text
		"""

		return [self.analyzeText(text) for text in texts]
