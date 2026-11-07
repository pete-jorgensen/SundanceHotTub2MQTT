#!/usr/bin/env python3
"""Commands must still arrive after the MQTT broker restarts under a running bridge.

The bridge connects with a clean session, so a broker that restarts (or any dropped
connection) forgets its subscriptions. paho reconnects by itself; unless the bridge
subscribes again on that reconnect, state keeps publishing while every command from
Home Assistant is silently discarded by the broker.

This runs app.py's real MQTT client against a minimal in-process MQTT 3.1.1 broker,
sends a set-point command, restarts the broker on the same port, and sends another.
No spa, no LAN. Needs the bridge's own dependency (paho-mqtt<2.0):

    pip install 'paho-mqtt<2.0' && python3 tools/test_resubscribe.py
"""
import asyncio
import os
import socket
import sys
import threading
import time

COMMAND_TOPIC = "hottub/temperature_command_topic"


class MiniBroker:
    """Just enough MQTT 3.1.1 for this test: CONNECT, SUBSCRIBE, PUBLISH, PING.

    restart() drops every connection and forgets every subscription, as a broker
    container being recreated does.
    """

    def __init__(self):
        self.port = None
        self.lock = threading.Lock()
        self.subs = {}          # socket -> set of exact topic filters
        self.connects = 0
        self.server = None

    def start(self):
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", self.port or 0))
        self.port = self.server.getsockname()[1]
        self.server.listen()
        threading.Thread(target=self._accept, args=(self.server,), daemon=True).start()

    def restart(self):
        server, self.server = self.server, None
        server.shutdown(socket.SHUT_RDWR)       # close() alone leaves accept() holding the port
        server.close()
        with self.lock:
            clients, self.subs = list(self.subs), {}
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()
        self.start()

    def subscribed(self, topic):
        with self.lock:
            return any(topic in filters for filters in self.subs.values())

    def publish(self, topic, payload):
        """Deliver to subscribers, as if Home Assistant had published it."""
        packet = self._publish_packet(topic, payload)
        with self.lock:
            targets = [c for c, filters in self.subs.items() if topic in filters]
        for c in targets:
            c.sendall(packet)

    @staticmethod
    def _publish_packet(topic, payload):
        body = len(topic).to_bytes(2, "big") + topic.encode() + payload.encode()
        return bytes([0x30]) + MiniBroker._length(len(body)) + body

    @staticmethod
    def _length(n):
        out = bytearray()
        while True:
            byte, n = n % 128, n // 128
            out.append(byte | (0x80 if n else 0))
            if not n:
                return bytes(out)

    def _accept(self, server):
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with self.lock:
                self.subs[conn] = set()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    @staticmethod
    def _read(conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def _client(self, conn):
        try:
            while True:
                header = self._read(conn, 1)[0]
                kind, qos = header >> 4, (header >> 1) & 3
                length, shift = 0, 0
                while True:
                    byte = self._read(conn, 1)[0]
                    length += (byte & 0x7F) << shift
                    shift += 7
                    if not byte & 0x80:
                        break
                body = self._read(conn, length) if length else b""
                if kind == 1:                                   # CONNECT
                    self.connects += 1
                    conn.sendall(b"\x20\x02\x00\x00")
                elif kind == 3 and qos:                         # PUBLISH, QoS 1
                    # the bridge's discovery configs go out at QoS 1 and need a PUBACK
                    tlen = int.from_bytes(body[:2], "big")
                    conn.sendall(b"\x40\x02" + body[2 + tlen:4 + tlen])
                elif kind == 8:                                 # SUBSCRIBE
                    pid, rest, granted = body[:2], body[2:], b""
                    while rest:
                        tlen = int.from_bytes(rest[:2], "big")
                        with self.lock:
                            self.subs.setdefault(conn, set()).add(rest[2:2 + tlen].decode())
                        rest = rest[3 + tlen:]
                        granted += b"\x00"
                    conn.sendall(b"\x90" + self._length(2 + len(granted)) + pid + granted)
                elif kind == 12:                                # PINGREQ
                    conn.sendall(b"\xd0\x00")
                elif kind == 14:                                # DISCONNECT
                    return
        except (ConnectionError, OSError):
            return
        finally:
            with self.lock:
                self.subs.pop(conn, None)
            conn.close()


class FakeSpa:
    """What app.py's MQTT side touches on the spa object."""

    LIGHT_MODE_MAP = [[0, "Off"], [1, "White"]]

    def __init__(self):
        self.temps = []

    async def send_temp_change(self, newtemp):
        self.temps.append(newtemp)


def wait_for(predicate, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def main():
    broker = MiniBroker()
    broker.start()

    os.environ["MQTT_HOST"] = "127.0.0.1"
    os.environ["MQTT_PORT"] = str(broker.port)
    for name in ("MQTT_TLS", "MQTT_USER", "MQTT_PASSWORD"):
        os.environ.pop(name, None)
    sys.path.insert(0, os.environ.get(
        "APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pybalboa")))
    import app  # noqa: E402

    spa = FakeSpa()
    app.spa = spa
    asyncio.run(app.start_mqtt(spa))

    results = []

    def step(name, ok):
        results.append(ok)
        print(("PASS  " if ok else "FAIL  ") + name)

    step("subscribed to the command topic on first connect",
         wait_for(lambda: broker.subscribed(COMMAND_TOPIC), 5))
    broker.publish(COMMAND_TOPIC, "100.0")
    step("a command before the broker restart reaches the spa",
         wait_for(lambda: spa.temps == [100.0], 5))

    broker.restart()
    step("the bridge reconnects after the broker restart",
         wait_for(lambda: broker.connects >= 2, 15))
    step("subscribed to the command topic again after reconnecting",
         wait_for(lambda: broker.subscribed(COMMAND_TOPIC), 5))
    broker.publish(COMMAND_TOPIC, "95.0")
    step("a command after the broker restart reaches the spa",
         wait_for(lambda: spa.temps == [100.0, 95.0], 5))

    app.mqtt_client.loop_stop()
    ok = all(results)
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
