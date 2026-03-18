Pipeline Breakdown:

Requires:
    - A CSV file with all reddit posts (an older version is currently in data-old, the new version is being updated live on the OneDrive), 
    - a categories/bucketing CSV file (pipeline currently uses one from Google Trends)

Once the relevant CSV paths are added to reddit_bucketeer_v2.py, it will produce vector embeddings for each Reddit post, and for each category/bucket. It will then assign the closest Category vector embedding to each Reddit post, and produce a similarity score. Additionally, any posts with a similarity score <0.35 are labeled as 'Uncertain'.

The output will ideally be a standardized reddit data csv. Line 24, for generating the contextual embeddings for each category/bucket, will likely need to be changed and only column names that are anticipated to provide the most context to each category should be used.
