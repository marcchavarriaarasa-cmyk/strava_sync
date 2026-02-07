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

# Rate Limit Safety
API_CALLS = 0
MAX_API_CALLS = 80

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
    global API_CALLS
    headers = {'Authorization': f"Bearer {access_token}"}
    activities = []
    page = 1
    per_page = 200 if fetch_all else limit
    
    while True:
        if API_CALLS >= MAX_API_CALLS:
            print(f"Rate limit safety cap reached ({API_CALLS}). Stopping fetch.")
            break

        params = {'per_page': per_page, 'page': page}
        try:
            print(f"Fetching page {page}...")
            response = requests.get(f"{API_URL}/athlete/activities", headers=headers, params=params)
            API_CALLS += 1
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

def get_activity_detail(activity_id, access_token):
    """Fetches detailed activity data to get fields like perceived_exertion."""
    global API_CALLS
    headers = {'Authorization': f"Bearer {access_token}"}
    try:
        response = requests.get(f"{API_URL}/activities/{activity_id}", headers=headers)
        API_CALLS += 1
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching detail for {activity_id}: {e}")
        return None

def get_rpe_description(rpe):
    """Maps RPE value (1-10) to a text description."""
    if not rpe:
        return None
    try:
        val = float(rpe)
        if val <= 3: return "Suave"
        if val <= 6: return "Moderado"
        if val <= 8: return "Duro"
        if val <= 9: return "Muy duro"
        return "Máximo"
    except (ValueError, TypeError):
        return None

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
    
    # New fields
    avg_cadence = activity.get('average_cadence')
    perceived_exertion = activity.get('perceived_exertion')
    
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

    # Base description
    description = f"El {formatted_date} realicé una {type_} de {distance_km:.1f}km en {time_str} con {elevation:.0f}m de desnivel. Mi ritmo medio fue de {pace_str} min/km."
    
    # Add Cadence
    # For running (Run), Strava API returns 2x steps per minute usually, or sometimes steps/min directly?
    # Actually for running, 'average_cadence' in API is often full steps per minute (e.g. 170).
    # But some docs say it's RPM (one foot). The debug output showed 73.8 and 74.3.
    # Normal running cadence is 150-180 spm. 74 spm is definitely RPM (one foot).
    # So for Run, we might want to double it to get SPM (Steps Per Minute) which is standard.
    # Cycling is RPM.
    
    if avg_cadence:
        if type_ == "Run":
            spm = avg_cadence * 2
            description += f" Cadencia media: {spm:.0f} ppm."
        else:
             description += f" Cadencia media: {avg_cadence:.0f} rpm."

    # Add Perceived Exertion
    if perceived_exertion:
        rpe_desc = get_rpe_description(perceived_exertion)
        description += f" Sensación: {rpe_desc} ({perceived_exertion:.0f}/10)."

    return description

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

def save_activities(activities, access_token):
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
            
            # Rate limit check specifically for detail fetch
            # We check if we have budget for one more call
            if API_CALLS >= MAX_API_CALLS:
                print(f"Rate limit safety cap reached ({API_CALLS}). Stopping sync for now.")
                print("Run the script again in 15 minutes to continue.")
                break

            # Fetch details for RPE if not present (it won't be in summary)
            # We already have summary, let's clone it or just update it
            
            print(f"Fetching details for new activity {act_id}...")
            detail = get_activity_detail(act_id, access_token)
            
            # Use detail if successful, otherwise fallback to summary
            # We merge detail into activity so we keep any summary fields that might be useful (though detail usually has all)
            full_activity = activity.copy()
            if detail:
                full_activity.update(detail)
            
            description = format_activity(full_activity)
            activities_to_add.append((act_id, description))
            existing_ids.add(act_id) # Update local set to preventing dupes in same batch
            
            # Sleep briefly to respect rate limits if syncing many
            time.sleep(1)

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
            save_activities(activities, access_token)
        else:
            print("No activities found or error fetching.")
    else:
        print("Authentication failed.")

if __name__ == "__main__":
    main()
