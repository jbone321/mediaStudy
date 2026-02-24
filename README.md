# mediaStudy
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
