import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.neighbors import NearestNeighbors

reddit_csv =  #-----change this to the current reddit data csv
googletrends_csv = "googleTrendsCategoriesBucketed.csv"
OUT_CSV = "posts_mapped_to_categories.csv"

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K = 1 #each post may only be assigned to the first closest google trends category
MIN_SIM = 0.35  # confidence threshold for similarity level

def normalize_text(s: str) -> str:
    return (s or "").strip()

def main():
    posts = pd.read_csv(reddit_csv)
    tax = pd.read_csv(googletrends_csv)

    #building the Google trends contextual embeddings (uses path and name columns)
    tax["category_text"] = tax["path"].fillna(tax["name"]).apply(normalize_text)

    #building reddit post contextual embeddings (using subreddit and title columns) #-----change this to columns relevant to the categorization csv
    posts["post_text"] = ("subreddit: " + posts["subreddit"].astype(str).fillna("") +" | title: " + posts["title"].astype(str).fillna("")).apply(normalize_text)

    model = SentenceTransformer(MODEL_NAME)

    #embedding google trends categories and post embeddings into individual vectors
    cat_emb = model.encode(tax["category_text"].tolist(), normalize_embeddings=True, batch_size=64, show_progress_bar=True)
    post_emb = model.encode(posts["post_text"].tolist(), normalize_embeddings=True, batch_size=64, show_progress_bar=True)

    #nearest neighbor search (cosine distance = 1 - cosine similarity)
    nn = NearestNeighbors(n_neighbors=TOP_K, metric="cosine")
    nn.fit(cat_emb)

    distances, indices = nn.kneighbors(post_emb, return_distance=True)

    best_idx = indices[:, 0]
    best_dist = distances[:, 0]
    best_sim = 1.0 - best_dist

    #attach best match
    posts["matched_category_name"] = tax.loc[best_idx, "name"].values
    posts["matched_category_path"] = tax.loc[best_idx, "path"].values
    posts["matched_bucket"] = tax.loc[best_idx, "bucket"].values
    posts["match_similarity"] = best_sim

    # confidence fallback
    posts.loc[posts["match_similarity"] < MIN_SIM, "matched_category_name"] = "Uncertain"
    posts.loc[posts["match_similarity"] < MIN_SIM, "matched_category_path"] = ""
    posts.loc[posts["match_similarity"] < MIN_SIM, "matched_bucket"] = "Other"

    posts.to_csv(OUT_CSV, index=False)
    print("Wrote:", OUT_CSV)
    print(posts["matched_bucket"].value_counts().head(10))

if __name__ == "__main__":
    main()
    