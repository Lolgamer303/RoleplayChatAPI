import requests
import json

url = "http://127.0.0.1:5000/dm"
data = {"key": "dp4MYmFdEH0t/0Rqn4kk2bJMKjpvTI4L3kEaFdVHtPU=", "input": "1"}

while True:
    response = requests.post(url, json=data)
    response_dict = response.json()  # Parse the JSON response
    print(response_dict.get('response', 'No response found. Make shure to input 1, 2, 3, 4 or 5'))  # Print the 'response' value
    user_input = input('Enter your input (1, 2, 3, 4 or 5): ')
    data['input'] = user_input