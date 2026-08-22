import os
import socket

from app import app, socketio


def get_lan_ips():
    ips = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(item[4][0])
    except OSError:
        pass

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        ips.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    return sorted(ip for ip in ips if ip and not ip.startswith("127."))


def print_urls(host, port):
    print(f"Local: http://127.0.0.1:{port}/", flush=True)
    print(f"Four clients: http://127.0.0.1:{port}/test-clients", flush=True)
    if host in {"0.0.0.0", "::"}:
        for ip in get_lan_ips():
            print(f"LAN: http://{ip}:{port}/", flush=True)
            print(f"LAN four clients: http://{ip}:{port}/test-clients", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    host = os.environ.get("HOST", "0.0.0.0")
    print_urls(host, port)
    socketio.run(app, debug=False, host=host, port=port, log_output=False)
