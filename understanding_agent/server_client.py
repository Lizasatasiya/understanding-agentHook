import urllib.request
import json

class ServerClient:
    def send(self, session_data: dict):
        
        url = "http://127.0.0.1:8000/understanding-session"
        data = json.dumps(session_data).encode('utf-8')
        
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    pass
                else:
                    pass
        except Exception as e:
            # Do not fail the commit if the server is just offline for the prototype
            pass
