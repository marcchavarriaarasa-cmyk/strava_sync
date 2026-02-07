import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Strava API Credentials
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")

# Strava API URLs
AUTH_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"

# File settings
OUTPUT_FILE = "entrenamientos_contexto.txt"
