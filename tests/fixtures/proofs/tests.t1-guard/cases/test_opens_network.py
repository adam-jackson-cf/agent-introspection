import socket


def test_a_t1_test_that_opens_a_network_connection() -> None:
    with socket.socket() as connection:
        connection.connect(("127.0.0.1", 9))
