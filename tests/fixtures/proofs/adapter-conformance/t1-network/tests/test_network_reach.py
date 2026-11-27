import socket


def test_dashboard_port_answers():
    with socket.create_connection(("127.0.0.1", 9), timeout=1):
        pass
