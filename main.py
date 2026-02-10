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

def get_zones(activity_id, access_token):
    """Fetches heart rate and pace zones for an activity."""
    global API_CALLS
    headers = {'Authorization': f"Bearer {access_token}"}
    try:
        response = requests.get(f"{API_URL}/activities/{activity_id}/zones", headers=headers)
        API_CALLS += 1
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching zones for {activity_id}: {e}")
        return []

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
    avg_heartrate = activity.get('average_heartrate')
    perceived_exertion = activity.get('perceived_exertion')
    
    # Check if we should add detailed info (Splits & Zones)
    # Target ID: 17347409698. Future IDs will be larger.
    show_details = False
    try:
        if activity.get('id') and int(activity.get('id')) >= 17347409698:
            show_details = True
    except (ValueError, TypeError):
        pass

    
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

    # Add Heart Rate
    if avg_heartrate:
         description += f" Frecuencia cardíaca media: {avg_heartrate:.0f} ppm."

    # Add Perceived Exertion
    if perceived_exertion:
        rpe_desc = get_rpe_description(perceived_exertion)
        description += f" Sensación: {rpe_desc} ({perceived_exertion:.0f}/10)."

    # Add Detailed Data (Splits and Zones) if applicable
    if show_details:
        # Splits
        splits = activity.get('splits_metric', [])
        if splits:
            description += "\n\nDesglose por Km:"
            for split in splits:
                try:
                    split_num = split.get('split')
                    # format pace
                    s_dist = split.get('distance', 1000) / 1000 # should be around 1km usually
                    if s_dist > 0:
                        s_pace = format_pace(split.get('moving_time', 0), s_dist)
                    else:
                        s_pace = "N/A"
                    
                    s_hr = split.get('average_heartrate')
                    s_elev = split.get('elevation_difference', 0)
                    
                    line = f"- Km {split_num}: {s_pace}/km"
                    if s_hr:
                         line += f", {s_hr:.0f} ppm"
                    
                    elev_sign = "+" if s_elev >= 0 else ""
                    line += f", {elev_sign}{s_elev:.0f}m"
                    
                    description += f"\n{line}"
                except Exception:
                    continue

        # Zones
        zones = activity.get('zones', [])
        if zones:
            for zone in zones:
                z_type = zone.get('type') # 'heartrate' or 'pace'
                buckets = zone.get('distribution_buckets', [])
                if not buckets: continue
                
                if z_type == 'heartrate':
                    description += "\n\nZonas de Frecuencia Cardíaca:"
                elif z_type == 'pace':
                    description += "\n\nZonas de Ritmo:"
                else:
                    continue
                
                total_time = sum(b.get('time', 0) for b in buckets)
                if total_time == 0: continue

                for i, b in enumerate(buckets):
                    b_time = b.get('time', 0)
                    if b_time == 0: continue
                    
                    pct = (b_time / total_time) * 100
                    
                    # Format time
                    mins = int(b_time // 60)
                    secs = int(b_time % 60)
                    time_str = f"{mins}m {secs}s"
                    
                    # Range
                    z_min = b.get('min')
                    z_max = b.get('max')
                    if z_max == -1: z_max = "+" # Open ended
                    
                    description += f"\n- Z{i+1} ({z_min}-{z_max}): {time_str} ({pct:.0f}%)"

    return description

def parse_activities_file(filepath):
    """
    Parses the file into a header and a dictionary of activities.
    Returns: (header_content, activities_dict)
    activities_dict is an OrderedDict: { 'activity_id': 'description_text' }
    """
    from collections import OrderedDict
    import re

    activities = OrderedDict()
    header_lines = []
    
    if not os.path.exists(filepath):
        return "", activities

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Split by the ID marker
        # We need to capture the ID to use it as key
        # Pattern: <!-- ID: 12345 -->
        # We use a lookahead or just standard split and process chunks
        
        # Regex to find all ID markers
        # We can split the text by the marker. 
        # re.split('<!-- ID: (\d+) -->', content) will return:
        # [header, id1, desc1, id2, desc2, ...]
        parts = re.split(r'<!-- ID: (\d+) -->', content)
        
        # First part is the header (text before the first ID)
        header = parts[0] if parts else ""
        
        # The rest come in pairs: ID, Description
        for i in range(1, len(parts), 2):
            act_id = parts[i]
            desc = parts[i+1].strip()
            activities[act_id] = desc

        return header, activities

    except IOError as e:
        print(f"Error reading existing file: {e}")
        return "", activities

def save_activities(activities, access_token):
    """Saves activities to the file, updating existing ones and appending new ones."""
    header, existing_activities = parse_activities_file(OUTPUT_FILE)
    
    # Track if we made any changes to avoid unnecessary writes
    updates_made = False
    
    # Process activities
    # We process reversed (oldest of the fetch first) so that if we are appending
    # new consecutive activities, they appear in order.
    for activity in reversed(activities):
        # Filter out WeightTraining
        act_type = activity.get('sport_type', activity.get('type', 'Unknown'))
        if act_type == "WeightTraining":
            continue

        act_id = str(activity.get('id'))
        
        # Rate limit check for optimization
        # Since we ALWAYS fetch details now to update descriptions (like RPE),
        # we need to be careful.
        # But wait, checking for updates requires fetching details, because the summary
        # doesn't have RPE.
        
        # Optimization: Only fetch details if we need to.
        # But we don't know if we need to update without fetching details first to compare.
        # Strava limit is generous enough for manual daily syncs (10 activities * 1 = 10 calls).
        # Daily limit is 1000. 15-min is 100.
        # If we fetch 10 recent activities, that's 10 detail calls. Safe.
        
        if API_CALLS >= MAX_API_CALLS:
            print(f"Rate limit safety cap reached ({API_CALLS}). Stopping sync for now.")
            break

        # Check if we already have it to decide on logging
        is_update = act_id in existing_activities
        
        # Fetch details to get full data (RPE, etc.)
        # Only print if it's new, to avoid spam, or finding changes?
        # Let's print fetching...
        if not is_update:
             print(f"Fetching details for new activity {act_id}...")
        else:
             print(f"Checking updates for activity {act_id}...")

        detail = get_activity_detail(act_id, access_token)
        
        full_activity = activity.copy()
        if detail:
            full_activity.update(detail)
            
            # Fetch Zones if it's a target activity (>= 17347409698)
            try:
                if int(act_id) >= 17347409698:
                    print(f"  -> Fetching zones for {act_id}...")
                    zones = get_zones(act_id, access_token)
                    if zones:
                         full_activity['zones'] = zones
            except (ValueError, TypeError):
                pass

        
        new_description = format_activity(full_activity)
        
        if is_update:
            # Check if description changed
            old_description = existing_activities[act_id]
            if old_description != new_description:
                print(f"  -> Updating activity {act_id}.")
                existing_activities[act_id] = new_description
                updates_made = True
            else:
                pass # Unchanged
        else:
            print(f"  -> Adding new activity {act_id}.")
            existing_activities[act_id] = new_description
            updates_made = True
            
        # Sleep briefly
        time.sleep(1)

    if not updates_made:
        print("No changes detected.")
        return

    # Sort activities by newest first (descending ID)
    sorted_activities = sorted(existing_activities.items(), key=lambda x: int(x[0]), reverse=True)

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header)
            for act_id, description in sorted_activities:
                f.write(f"<!-- ID: {act_id} -->\n")
                f.write(f"{description}\n\n")
        print("File updated successfully.")
                
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
