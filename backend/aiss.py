import asyncio
import websockets
import json
import os
from dotenv import load_dotenv
import os
load_dotenv()

key = os.getenv("AISSTREAM_API_KEY")
print(f"Key loaded: '{key}'")  # should not be None or empt

async def test_ais():
    url = "wss://stream.aisstream.io/v0/stream"
    
    subscribe_msg = {
        "APIKey": os.getenv("AISSTREAM_API_KEY"),
        "BoundingBoxes": [
            # Strait of Messina bounding box
            [[37.8, 15.2], [38.5, 15.8]]
        ]
    }
    
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(subscribe_msg))
        
        for i in range(10):  # grab 10 messages then stop
            msg = await ws.recv()
            data = json.loads(msg)
            print(json.dumps(data, indent=2))

asyncio.run(test_ais())