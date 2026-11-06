#!/usr/bin/env python3
"""Run inside Gamescope headless; launch only a disposable Chromium profile."""

import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[2] / 'docs/icon-suite'
(root/'checks').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='odysseus-icons-chromium-') as profile:
    with (root/'checks/browser.log').open('w') as log:
        browser = subprocess.Popen([
            'chromium', '--ozone-platform=wayland', '--remote-debugging-port=9223',
            '--user-data-dir='+profile, '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--disable-sync', '--disable-extensions',
            '--password-store=basic', '--disable-features=Translate,MediaRouter',
            '--enable-gpu', '--window-size=1600,1040', 'about:blank',
        ], cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for _ in range(150):
                if browser.poll() is not None:
                    raise RuntimeError('Chromium exited; inspect checks/browser.log')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:9223/json/version', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Chromium debugging endpoint did not become ready')
            result = subprocess.run([
                'deno', 'run', '--no-config', '--no-remote', '--no-npm',
                '--allow-net=127.0.0.1:9223,localhost:9223', '--allow-write='+str(root/'checks'),
                str(Path(__file__).resolve().parent/'browser-check.js'),
            ], cwd=root, check=False)
            raise SystemExit(result.returncode)
        finally:
            if browser.poll() is None:
                browser.terminate()
                try:
                    browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=5)
