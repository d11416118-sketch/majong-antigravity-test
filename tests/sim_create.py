import socketio
import os
import time
import sys
import uuid

sio = socketio.Client()
username = f"tester_{uuid.uuid4().hex[:8]}"

@sio.event
def connect():
    print("Connected to server")
    time.sleep(1)
    print("Registering...")
    sio.emit('register_account', {'username': username, 'password': 'secret123'})

@sio.event
def auth_success(data):
    print(f"Authenticated as {data['account']['username']}")
    print("Creating Room...")
    sio.emit('create_room', {})

@sio.event
def room_created(data):
    print(f"PASS: Room Created! ID: {data['room_id']}")
    sio.disconnect()
    sys.exit(0)

@sio.event
def connect_error(data):
    print("Connection failed")
    sys.exit(1)

@sio.on('error')
def on_error(data):
    print(f"FAIL: Received error: {data}")
    sys.exit(1)

if __name__ == '__main__':
    try:
        port = os.environ.get("PORT", "5001")
        url = os.environ.get("BASE_URL", f"http://127.0.0.1:{port}")
        sio.connect(url)
        sio.wait()
    except Exception as e:
        print(f"Exception: {e}")
