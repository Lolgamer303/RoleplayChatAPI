import requests
import json

BASE_URL = "http://127.0.0.1:5000"
HEADERS = {"Authorization": "Bearer zXr6kqofaR0uNWBQ5KUceUmm88Rw7+C41tzrSPZyWmc="}

def create_campaign():
    url = f"{BASE_URL}/campaigns"
    data = {
        "name": "Test Campaign",
        "book": "Test Book",
        "prompt": "Test Prompt",
    }
    response = requests.post(url, json=data, headers=HEADERS)
    print("POST /campaigns Response:", response.json())

def test_get_campaigns():
    url = f"{BASE_URL}/campaigns"
    response = requests.get(url, headers=HEADERS)
    print("GET /campaigns Response:", response.json())
    return response.json()[-1].get("id")

def test_campaign_chat(campaignid):
    url = f"{BASE_URL}/campaigns/{campaignid}"
    data = {"input": "1"}
    response = requests.post(url, json=data, headers=HEADERS)
    print(f"POST /campaigns/{campaignid} Response:", response.json())

def test_get_campaign_info(campaignid):
    url = f"{BASE_URL}/campaigns/{campaignid}"
    response = requests.get(url, headers=HEADERS)
    print(f"GET /campaigns/{campaignid} Response:", response.json())
    
def test_detede_campaign(campaignid):
    url = f"{BASE_URL}/campaigns/{campaignid}"
    response = requests.delete(url, headers=HEADERS)
    print(f"DELETE /campaigns/{campaignid} Response:", response.json())

if __name__ == "__main__":
    create_campaign()
    campaign_id = test_get_campaigns()
    test_campaign_chat(campaign_id)
    test_get_campaign_info(campaign_id)
