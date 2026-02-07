import requests
import config
import urllib.parse

def step_one_get_authorization_url():
    params = {
        "client_id": config.CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "http://localhost/exchange_token",
        "approval_prompt": "force",
        "scope": "activity:read"
    }
    url = f"https://www.strava.com/oauth/authorize?{urllib.parse.urlencode(params)}"
    print("\n--- Step 1: Authorization ---")
    print("Please visit the following URL in your browser to authorize the app:")
    print(f"\n{url}\n")
    print("After logging in and clicking 'Authorize', you will be redirected to a page (which might fail to load, that's fine).")
    print("Look at the URL in your browser address bar. It will look like:")
    print("http://localhost/exchange_token?state=&code=YOUR_CODE_HERE&scope=activity:read")
    print("\nCopy the value of the 'code' parameter.")

def step_two_exchange_code(code):
    print("\n--- Step 2: Exchange Code for Token ---")
    payload = {
        'client_id': config.CLIENT_ID,
        'client_secret': config.CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    }
    
    try:
        response = requests.post(config.AUTH_URL, data=payload)
        response.raise_for_status()
        data = response.json()
        
        print("\nAuthentication Successful!")
        print(f"New Refresh Token: {data['refresh_token']}")
        print(f"New Access Token: {data['access_token']}")
        print("\nPlease update your .env file with this new STRAVA_REFRESH_TOKEN.")
        
    except requests.exceptions.RequestException as e:
        print(f"Error exchanging code: {e}")
        if response.content:
            print(f"Response: {response.content}")

if __name__ == "__main__":
    step_one_get_authorization_url()
    code = input("\nEnter the 'code' from the URL: ").strip()
    if code:
        step_two_exchange_code(code)
