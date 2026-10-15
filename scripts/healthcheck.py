#!/usr/bin/env python3
"""Check the served desktop and actual X11 capture without exposing its content."""
import os
import socket
import urllib.request

from PIL import ImageGrab


def main():
    with urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=3) as response:
        if response.status != 200 or b'noVNC' not in response.read(262144):
            raise RuntimeError('noVNC page unavailable')
    with socket.create_connection(('127.0.0.1', 5900), timeout=3) as connection:
        if not connection.recv(12).startswith(b'RFB '):
            raise RuntimeError('VNC protocol unavailable')
    image = ImageGrab.grab(xdisplay=os.environ.get('DISPLAY', ':99'))
    if image.width < 640 or image.height < 480:
        raise RuntimeError('Desktop capture has invalid dimensions')


if __name__ == '__main__':
    main()
