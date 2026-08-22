import socketio
import os
import time
import sys

sio = socketio.Client()
game_over_data = None
room_id = None

@sio.event
def connect():
    print("Connected to server")
    sio.emit('create_room', {'name': 'DrawTester'})

@sio.event
def room_created(data):
    print(f"Room created: {data}")
    global room_id
    room_id = data['room_id']
    print("Force Starting...")
    sio.emit('force_start', {})

@sio.event
def game_start(data):
    print("Game Started!")
    time.sleep(1)
    
    # Drain Wall to 16
    print("Draining Wall to 16 (Trigger Liu Ju)...")
    sio.emit('debug_drain_wall', {'remaining': 16})
    time.sleep(1)
    
    print("Discarding to trigger advance_turn...")
    # Set known hand to ensure we have index 0 available
    sio.emit('debug_set_hand', {'hand': ['1m']*16 + ['2m']})
    time.sleep(0.5)
    
    # Send Action Logic using action_discard
    # Discard index 0 ('1m')
    if room_id:
        sio.emit('action_discard', {'room_id': room_id, 'tile_index': 0})
    else:
        print("Error: Room ID not found")

@sio.event
def game_over(data):
    global game_over_data
    game_over_data = data
    print("\n!!! GAME OVER RECEIVED !!!")
    print(f"Winner: {data['winner_name']}")
    sio.disconnect()

def main():
    try:
        port = os.environ.get("PORT", "5001")
        url = os.environ.get("BASE_URL", f"http://127.0.0.1:{port}")
        sio.connect(url)
        
        start = time.time()
        while not game_over_data and time.time() - start < 10:
            time.sleep(1)
            
        if game_over_data and game_over_data['winner_uid'] is None:
            print("\nTest SUCCESS: Draw Game Confirmed.")
            sys.exit(0)
        elif game_over_data:
            print(f"\nTest FAILED: Winner is {game_over_data['winner_name']}")
            sys.exit(1)
        else:
            print("\nTest TIMEOUT")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
