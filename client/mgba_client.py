#!/usr/bin/env python3
import socket
from pathlib import Path


class MGBAClient:
    def __init__(self, host='127.0.0.1', port=8765, timeout=2.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.file = self.sock.makefile('rwb', buffering=0)
        self.hello = self.file.readline().decode().rstrip('\r\n')

    def close(self):
        try:
            self.file.close()
        finally:
            self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def command(self, text):
        self.file.write((text + '\n').encode())
        return self.file.readline().decode().rstrip('\r\n')

    def ping(self): return self.command('PING')
    def title(self): return self.command('TITLE')
    def code(self): return self.command('CODE')
    def frame(self): return int(self.command('FRAME'))
    def reset(self): return self.command('RESET')
    def keys(self): return int(self.command('KEYS'), 16)

    def keydown(self, key): return self.command(f'KEYDOWN {key}')
    def keyup(self, key): return self.command(f'KEYUP {key}')
    def press(self, key, frames=2): return self.command(f'PRESS {key} {frames}')

    def read8(self, address): return int(self.command(f'READ8 0x{address:X}'), 16)
    def read16(self, address): return int(self.command(f'READ16 0x{address:X}'), 16)
    def read32(self, address): return int(self.command(f'READ32 0x{address:X}'), 16)

    def write8(self, address, value): return self.command(f'WRITE8 0x{address:X} 0x{value:X}')
    def write16(self, address, value): return self.command(f'WRITE16 0x{address:X} 0x{value:X}')
    def write32(self, address, value): return self.command(f'WRITE32 0x{address:X} 0x{value:X}')

    def screenshot(self, path):
        path = str(Path(path))
        response = self.command(f'SCREENSHOT {path}')
        if not response.startswith('OK '):
            raise RuntimeError(response)
        return Path(response[3:])


if __name__ == '__main__':
    with MGBAClient() as m:
        print(m.hello)
        print('title:', m.title())
        print('code:', m.code())
        print('frame:', m.frame())
        print('keys:', hex(m.keys()))
