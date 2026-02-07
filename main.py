import requests
import os
import time
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")

AUTH_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
OUTPUT_FILE = "entrenamientos_contexto.txt"

def get_access_token():
    """Refreshes the access token using the refresh token."""
    payload = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    
    try:
        response = requests.post(AUTH_URL, data=payload)
        response.raise_for_status()
        token_data = response.json()
        return token_data['access_token']
    except requests.exceptions.RequestException as e:
        print(f"Error refreshing token: {e}")
        return None

def get_activities(access_token, fetch_all=False, limit=10):
    """
    Fetches activities from Strava.
    If fetch_all is True, paginates through all history.
    Otherwise, fetches the most recent 'limit' activities.
    """
    headers = {'Authorization': f"Bearer {access_token}"}
    activities = []
    page = 1
    per_page = 200 if fetch_all else limit
    
    while True:
        params = {'per_page': per_page, 'page': page}
        try:
            print(f"Fetching page {page}...")
            response = requests.get(f"{API_URL}/athlete/activities", headers=headers, params=params)
            response.raise_for_status()
            batch = response.json()
            
            if not batch:
                break
                
            activities.extend(batch)
            
            if not fetch_all:
                break
                
            page += 1
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching activities on page {page}: {e}")
            break
            
    return activities

def format_pace(seconds, distance_km):
    """Calculates pace in min/km."""
    if distance_km <= 0:
        return "N/A"
    
    pace_decimal = (seconds / 60) / distance_km
    pace_min = int(pace_decimal)
    pace_sec = int((pace_decimal - pace_min) * 60)
    return f"{pace_min}:{pace_sec:02d}"

def format_activity(activity):
    """Formats an activity into a natural language description."""
    # Extract data with safe defaults
    name = activity.get('name', 'Actividad')
    date_str = activity.get('start_date_local', '')
    type_ = activity.get('sport_type', activity.get('type', 'Unknown'))
    distance_meters = activity.get('distance', 0)
    moving_time_seconds = activity.get('moving_time', 0)
    elevation = activity.get('total_elevation_gain', 0)
    
    # Conversions
    distance_km = distance_meters / 1000
    
    # Format date (e.g., 2026-02-07T10:00:00Z -> 07/02/2026)
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        formatted_date = date_obj.strftime("%d/%m/%Y")
    except ValueError:
        formatted_date = date_str

    # Format time
    if moving_time_seconds < 3600:
        time_str = f"{moving_time_seconds // 60} minutos"
    else:
        hours = moving_time_seconds // 3600
        minutes = (moving_time_seconds % 3600) // 60
        time_str = f"{hours}h {minutes}min"

    # Calculate pace
    pace_str = format_pace(moving_time_seconds, distance_km)

    return f"El {formatted_date} realicé una {type_} de {distance_km:.1f}km en {time_str} con {elevation:.0f}m de desnivel. Mi ritmo medio fue de {pace_str} min/km."

def get_existing_ids(filepath):
    """Reads existing activity IDs from the file to avoid duplicates."""
    if not os.path.exists(filepath):
        return set()
    
    ids = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith('<!-- ID:'):
                    # Extract ID from "<!-- ID: 12345 -->"
                    import re
                    match = re.search(r'ID:\s*(\d+)', line)
                    if match:
                        ids.add(match.group(1))
    except IOError as e:
        print(f"Error reading existing file: {e}")
    return ids

def save_activities(activities):
    """Saves new activities to the file."""
    existing_ids = get_existing_ids(OUTPUT_FILE)
    
    activities_to_add = []

    # Process activities
    # Use reversed() if we want to add oldest of the batch first
    # But if we fetch multiple pages (newest to oldest), resolving order is tricky.
    # We'll just process them as they come (newest first usually) and append.
    # Or reverse the whole list?
    # Strava API returns newest first. Page 1 = newest.
    # If we fetch all, we have [Newest ... Oldest].
    # If we reverse, we get [Oldest ... Newest].
    # That's better for a chronological log.
    
    for activity in reversed(activities):
        # Filter out WeightTraining
        act_type = activity.get('sport_type', activity.get('type', 'Unknown'))
        if act_type == "WeightTraining":
            continue

        act_id = str(activity.get('id'))
        if act_id not in existing_ids:
            description = format_activity(activity)
            activities_to_add.append((act_id, description))
            existing_ids.add(act_id) # Update local set to preventing dupes in same batch

    if not activities_to_add:
        print("No new activities to sync.")
        return

    try:
        with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
            for act_id, description in activities_to_add:
                f.write(f"<!-- ID: {act_id} -->\n")
                f.write(f"{description}\n\n")
                # Print only first few characters of description to avoid spam
                # print(f"Added activity: {description[:50]}...")
        print(f"Synced {len(activities_to_add)} new activities.")
                
    except IOError as e:
        print(f"Error writing to file: {e}")

def main():
    parser = argparse.ArgumentParser(description="Strava Activity Sync")
    parser.add_argument("--all", action="store_true", help="Fetch all historical activities (pagination)")
    args = parser.parse_args()

    print("Starting Strava Sync...")
    
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        print("Error: Missing credentials. Please check your .env file.")
        return

    access_token = get_access_token()
    if access_token:
        print("Authentication successful.")
        activities = get_activities(access_token, fetch_all=args.all)
        if activities:
            print(f"Fetched {len(activities)} activities.")
            save_activities(activities)
        else:
            print("No activities found or error fetching.")
    else:
        print("Authentication failed.")

if __name__ == "__main__":
    main()
