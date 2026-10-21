from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


URL = "https://github.com/blueCFD/Core/releases/download/blueCFD-Core-2020-1/blueCFD-Core-2020-1-win64-setup.exe"
SIZE = 870543288
SHA1 = "31c78a935309ece053035e4d2a81ebac5f766c43"
CHUNK = 1024 * 1024


def main():
    root = Path(__file__).resolve().parents[1] / "output/native_rans_20261012/sdk"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "blueCFD-Core-2020-1-win64-setup.exe"
    if path.exists() and path.stat().st_size == SIZE:
        print("Official SDK installer already downloaded", flush=True)
    else:
        parts = root / "download_parts"
        parts.mkdir(exist_ok=True)
        # Recover completed chunks from an interrupted sequential download.
        if path.exists():
            with path.open("rb") as previous:
                for index in range(path.stat().st_size // CHUNK):
                    target = parts / f"{index:05d}.part"
                    block = previous.read(CHUNK)
                    if not target.exists():
                        target.write_bytes(block)

        def fetch(index):
            start = index * CHUNK
            end = min(SIZE, start + CHUNK) - 1
            target = parts / f"{index:05d}.part"
            if target.exists() and target.stat().st_size == end - start + 1:
                return target.stat().st_size
            for attempt in range(6):
                try:
                    request = urllib.request.Request(URL, headers={
                        "User-Agent": "wind3d-ninja-native-bootstrap",
                        "Range": f"bytes={start}-{end}",
                    })
                    with urllib.request.urlopen(request, timeout=45) as response:
                        expected = f"bytes {start}-{end}/{SIZE}"
                        if response.status != 206 or response.headers.get("Content-Range") != expected:
                            raise ValueError(f"Range response mismatch for {index}")
                        block = response.read()
                    if len(block) != end - start + 1:
                        raise ValueError(f"Truncated chunk {index}")
                    temporary = target.with_suffix(".tmp")
                    temporary.write_bytes(block)
                    temporary.replace(target)
                    return len(block)
                except (OSError, ValueError):
                    if attempt == 5:
                        raise
                    time.sleep(min(5, attempt + 1))
            raise AssertionError("unreachable")

        received = 0
        last = time.monotonic()
        with ThreadPoolExecutor(max_workers=16) as pool:
            jobs = [pool.submit(fetch, index) for index in range((SIZE + CHUNK - 1) // CHUNK)]
            for job in as_completed(jobs):
                received += job.result()
                if time.monotonic() - last > 10:
                    print(f"SDK chunks {received}/{SIZE} bytes", flush=True)
                    last = time.monotonic()
        combined = path.with_suffix(".complete")
        with combined.open("wb") as output:
            for index in range((SIZE + CHUNK - 1) // CHUNK):
                output.write((parts / f"{index:05d}.part").read_bytes())
        combined.replace(path)
    with path.open("rb") as artifact:
        if hashlib.file_digest(artifact, "sha1").hexdigest() != SHA1:
            raise ValueError("Official publisher SHA1 mismatch")
    digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    (root / "download_manifest.json").write_text(json.dumps({
        "url": URL, "release": "blueCFD-Core-2020-1", "size": SIZE,
        "sha256": digest, "sha256_kind": "downloaded artifact identity; not an independently published digest",
        "publisher_sha1": SHA1,
        "deployment": "Windows native OpenFOAM8 runtime; Linux/WSL is not a runtime dependency",
    }, indent=2), encoding="utf-8")
    print(f"SDK downloaded: {path}; sha256={digest}", flush=True)


if __name__ == "__main__":
    main()
