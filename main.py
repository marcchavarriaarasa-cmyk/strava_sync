import requests
import os
import sys
import time
import argparse
import tempfile
from datetime import datetime
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
REQUEST_TIMEOUT = (10, 30)


class StravaSyncError(RuntimeError):
    """Raised when a sync cannot be completed without risking partial data."""


def build_session():
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1,
        status_forcelist=(403, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({'GET', 'POST'}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount('https://', adapter)
    return session


SESSION = build_session()


def require_credentials():
    missing = [
        name for name, value in (
            ('STRAVA_CLIENT_ID', CLIENT_ID),
            ('STRAVA_CLIENT_SECRET', CLIENT_SECRET),
            ('STRAVA_REFRESH_TOKEN', REFRESH_TOKEN),
        ) if not value
    ]
    if missing:
        raise StravaSyncError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Configure them in .env or as GitHub Repository Secrets."
        )


def reserve_api_call():
    global API_CALLS
    if API_CALLS >= MAX_API_CALLS:
        raise StravaSyncError(
            f"Rate limit safety cap reached ({MAX_API_CALLS} API calls). "
            "No output file was changed."
        )
    API_CALLS += 1

def get_access_token():
    """Refreshes the access token using the refresh token."""
    require_credentials()
    payload = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    
    try:
        reserve_api_call()
        response = SESSION.post(AUTH_URL, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        token_data = response.json()
        if not token_data.get('access_token'):
            raise StravaSyncError('Strava token response did not include an access token.')
        return token_data['access_token']
    except (requests.exceptions.RequestException, ValueError) as error:
        detail = getattr(locals().get('response'), 'text', '')
        if detail:
            detail = f" Response: {detail[:500]}"
        raise StravaSyncError(
            f"Unable to refresh the Strava token: {error}.{detail}"
        ) from error

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
        params = {'per_page': per_page, 'page': page}
        try:
            print(f"Fetching page {page}...")
            reserve_api_call()
            response = SESSION.get(
                f"{API_URL}/athlete/activities",
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            batch = response.json()
            
            if not batch:
                break
                
            activities.extend(batch)
            
            if not fetch_all:
                break
                
            page += 1
            
        except (requests.exceptions.RequestException, ValueError) as error:
            raise StravaSyncError(
                f"Unable to fetch activities on page {page}: {error}"
            ) from error
            
    return activities

def get_activity_detail(activity_id, access_token):
    """Fetches detailed activity data to get fields like perceived_exertion."""
    headers = {'Authorization': f"Bearer {access_token}"}
    try:
        reserve_api_call()
        response = SESSION.get(
            f"{API_URL}/activities/{activity_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, ValueError) as error:
        raise StravaSyncError(
            f"Unable to fetch details for activity {activity_id}: {error}"
        ) from error

def get_zones(activity_id, access_token):
    """Fetches heart rate and pace zones for an activity."""
    headers = {'Authorization': f"Bearer {access_token}"}
    try:
        reserve_api_call()
        response = SESSION.get(
            f"{API_URL}/activities/{activity_id}/zones",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, ValueError) as error:
        raise StravaSyncError(
            f"Unable to fetch zones for activity {activity_id}: {error}"
        ) from error

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
        date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        formatted_date = date_obj.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
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

    temp_path = None
    try:
        output_dir = os.path.dirname(OUTPUT_FILE) or '.'
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=output_dir, delete=False
        ) as f:
            temp_path = f.name
            f.write(header)
            for act_id, description in sorted_activities:
                f.write(f"<!-- ID: {act_id} -->\n")
                f.write(f"{description}\n\n")
        os.replace(temp_path, OUTPUT_FILE)
        temp_path = None
        print("File updated successfully.")
                
    except IOError as error:
        raise StravaSyncError(f"Unable to write {OUTPUT_FILE}: {error}") from error
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

def main():
    global API_CALLS
    API_CALLS = 0
    parser = argparse.ArgumentParser(description="Strava Activity Sync")
    parser.add_argument("--all", action="store_true", help="Fetch all historical activities (pagination)")
    args = parser.parse_args()

    print("Starting Strava Sync...")
    
    try:
        access_token = get_access_token()
        print("Authentication successful.")
        activities = get_activities(access_token, fetch_all=args.all)
        if activities:
            print(f"Fetched {len(activities)} activities.")
            save_activities(activities, access_token)
        else:
            print("No activities found.")
        return 0
    except StravaSyncError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
