# Start and Correct Environment
1. Clone the repo and navigate to the project root
2. Install pyenv
- macOS: brew install pyenv
- Linux/WSL: curl https://pyenv.run | bash

3. Install matching python version (3.12.5)

pyenv install 3.12.5

pyenv local 3.12.5

Now when in this folder python version should be 3.12.5 you can verify with python3 --version

4. Create and activate venv

python3 -m venv venv

source venv/bin/activate

6. Install packages

pip install -r requirements.txt

# pipeline.py usage
1.
```python
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

                self.google = {
                        "cats": [3, 35, 8, 18, 65],
                        "timeframe": "2026-02-11 2026-02-25"
                }

                # Sentiment analysis settings
                self.sentiment = {
                        "enabled": True,
                        "sources": ["comments", "titles", "descriptions"],
                        "outputDir": "data/processed/sentiment"
                }
   ```
   First thing is to ensure that everything is saving to a seperate file to prevent unwanted joins of our data. Edit "baseDir": "data/raw/youtube" to "baseDir": "data/raw/youtube{YOUR_NAME}" and while not required "categories": can be any categories that you wish.
3. Make sure to be at the root of the project and run src/pipeline.py

   src/pipeline.py has 4 flags [--youtube][--google-trends][--update-comments][--all]

   [--youtube] runs youtube collection
   
   [--google-trends] runs google trends collection
   
   [--update-comments] updates youtube video comments
   
   [--all] runs full pipeline
   

