import socketio
import os
import time
import sys

sio = socketio.Client()

winner_found = False

@sio.event
def connect():
    print("Connected to server")
    sio.emit('create_room', {'name': 'Tester'})

@sio.event
def room_created(data):
    print(f"Room created: {data}")
    # Force start
    print("Force Starting...")
    sio.emit('force_start', {})

@sio.event
def game_start(data):
    print("Game Started!")
    # Wait a bit for turn structure to settle
    time.sleep(1)
    
    # Cheat: Set Winning Hand (PPHu + Mixed Colors)
    # Hand: 111m, 333m, 555m, 777m, 999m + 11z (Pair)
    # 17 tiles
    hand = [
        '1m','1m','1m', 
        '3m','3m','3m',
        '5m','5m','5m',
        '7m','7m','7m',
        '9m','9m','9m',
        '1z','1z'
    ]
    print("Setting Cheat Hand...")
    sio.emit('debug_set_hand', {'hand': hand})
    time.sleep(1)
    
    # Send HU Action
    # We are usually Player 0 (East).
    # We claim we drew '1z'? Or we maintain '1z' is in hand.
    # Logic in app.py: `check_self_actions` usually triggered.
    # But we bypass check and Send Reply directly.
    # If state matches Player Turn.
    
    print("Sending HU action...")
    # Using tile '1z' as the effective winning tile (Self Draw)
    sio.emit('action_reply', {'type': 'HU', 'tile': '1z'})

@sio.event
def game_over(data):
    global winner_found
    print("\n!!! GAME OVER RECEIVED !!!")
    print(f"Winner: {data['winner_name']}")
    print("Score Breakdown:")
    for item in data['score_breakdown']['breakdown']:
        print(f" - {item['name']}: {item['value']}")
    print(f"Total: {data['score_breakdown']['total']}")
    winner_found = True
    sio.disconnect()

@sio.event
def game_update(data):
    # print("Updated...")
    pass

def main():
    try:
        port = os.environ.get("PORT", "5001")
        url = os.environ.get("BASE_URL", f"http://127.0.0.1:{port}")
        sio.connect(url)
        
        # Wait for outcome
        start = time.time()
        while not winner_found and time.time() - start < 10:
            time.sleep(1)
            
        if winner_found:
            print("\nTest SUCCESS")
            sys.exit(0)
        else:
            print("\nTest TIMEOUT")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
