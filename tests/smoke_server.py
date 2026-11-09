"""Start a real local Streamlit server, check HTTP, then stop this process."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def main():
    root = Path(__file__).absolute().parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command = [sys.executable, '-B', '-m', 'streamlit', 'run', str(root / 'app.py'),
               '--server.headless=true', '--server.address=127.0.0.1',
               '--server.port=' + str(port), '--browser.gatherUsageStats=false']
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, cwd=str(root), stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Streamlit exited: ' + str(process.returncode))
                try:
                    with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=2) as response:
                        assert response.status == 200
                        assert response.read().strip() == b'ok'
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise TimeoutError('Streamlit health endpoint did not become ready')
            with urlopen(f'http://127.0.0.1:{port}/', timeout=3) as response:
                assert response.status == 200
                assert b'<html' in response.read().lower()
            print('PASS: real Streamlit server health and HTML returned HTTP 200')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            log.seek(0)
            print(log.read().decode('utf-8', errors='replace'))


if __name__ == '__main__':
    main()
