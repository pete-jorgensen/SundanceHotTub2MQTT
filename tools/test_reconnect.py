#!/usr/bin/env python3
"""A dead link must be closed, and failing reconnects must back off.

The EW11 serves `maxAccept 2` with `Timeout 0` (no idle timeout), so the gateway can hold a
stale connection slot: a link the bridge abandons without closing keeps its slot, because the
kernel still answers the gateway's keepalives. With both slots held, the EW11 accepts every new
connection and closes it at once. So the bridge closes the transport on every dead-link path,
and backs off between failed reconnects instead of retrying about once a second.

This runs the real listen() loop against a fake EW11 on localhost that behaves the same way.
No spa, no LAN, no MQTT. Run from the repo root:

    python3 tools/test_reconnect.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.environ.get(
    "APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pybalboa")))
import sundanceRS485 as S  # noqa: E402


class FakeEW11:
    """TCP server with N client slots and no idle timeout; it never sends a byte.

    A session holds its slot until the CLIENT closes it, as on the EW11 with Timeout 0.
    A connection beyond the slots is accepted and closed at once.
    """

    def __init__(self, slots=2):
        self.slots = slots
        self.live = 0
        self.max_live = 0
        self.rejected = 0
        self.accept_times = []

    async def handle(self, reader, writer):
        self.accept_times.append(time.monotonic())
        if self.live >= self.slots:
            self.rejected += 1
            writer.close()
            return
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        try:
            await reader.read()          # returns only when the client closes its end
        finally:
            self.live -= 1
            writer.close()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]


async def run_listen(port, seconds, slots=2, read_timeout=0.2):
    ew11 = FakeEW11(slots)
    port = await ew11.start()
    spa = S.SundanceRS485("127.0.0.1", port)
    spa.read_timeout = read_timeout
    spa.log.setLevel("CRITICAL")
    task = asyncio.ensure_future(spa.listen())
    await asyncio.sleep(seconds)
    task.cancel()
    ew11.server.close()
    return ew11


def check(name, ok, detail):
    print("{0} {1}: {2}".format("PASS" if ok else "FAIL", name, detail))
    return ok


async def main():
    S.RECONNECT_BACKOFF_START = 0.1
    S.RECONNECT_BACKOFF_MAX = 0.4
    ok = True

    # 1. Silent gateway: every link times out. Each must be closed before the next opens,
    #    so the bridge never holds more than one slot and is never refused.
    ew11 = await run_listen(0, 3.0)
    ok &= check("dead link is closed", ew11.max_live == 1 and ew11.rejected == 0,
                "max slots held {0}, refused {1}, sessions {2}".format(
                    ew11.max_live, ew11.rejected, len(ew11.accept_times)))

    # 2. Gateway with no free slot: every connect is accepted then closed. Attempts must
    #    space out (0, 0.1, 0.2, 0.4, 0.4 ...), not spin.
    ew11 = await run_listen(0, 2.0, slots=0)
    gaps = [b - a for a, b in zip(ew11.accept_times, ew11.accept_times[1:])]
    spin = len(ew11.accept_times)
    ok &= check("refused reconnects back off", 3 <= spin <= 8 and gaps and max(gaps) >= 0.35,
                "{0} attempts in 2s, largest gap {1:.2f}s".format(
                    spin, max(gaps) if gaps else 0))

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
