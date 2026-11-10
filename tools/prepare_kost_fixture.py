"""Download and safely provision the official KOST eCH-0160 v1.3 FILES test SIP."""

import argparse
import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

FIXTURE_NAME = "SIP_20260421_KOST_eCH1.3F_1a-3d-IO"
FIXTURE_URL = ("https://kost-ceco.ch/cms/dl/4e987f3ea41c4da065b191e39c34cf8c/"
               "SIP_20260421_KOST_eCH1.3F_1a-3d-IO.zip?target=1")
FIXTURE_SHA256 = "dc4474a3cfe654f7e459cd594cd43d91f7e563b98c39e809417e6778ba023c65"
MAX_ARCHIVE_MEMBERS = 10000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 ** 3
CHUNK_SIZE = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(path: Path) -> None:
    request = urllib.request.Request(FIXTURE_URL, headers={"User-Agent": "Package-Turbo-CI"})
    with urllib.request.urlopen(request, timeout=90) as response, Path(path).open("xb") as output:
        shutil.copyfileobj(response, output, CHUNK_SIZE)


def _member_target(root: Path, info: zipfile.ZipInfo, seen: set[str]) -> Path:
    name = info.filename
    member = PurePosixPath(name)
    if (not name or "\\" in name or "\0" in name or ":" in name or member.is_absolute()
            or any(part in ("", ".", "..") for part in member.parts)):
        raise ValueError(f"Unsafe ZIP member path: {name!r}")
    normalized = member.as_posix().rstrip("/").casefold()
    if normalized in seen:
        raise ValueError(f"Duplicate ZIP member path: {name!r}")
    seen.add(normalized)
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise ValueError(f"Symbolic links are not allowed in the fixture ZIP: {name!r}")
    target = root.joinpath(*member.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"ZIP member escapes the fixture directory: {name!r}")
    return target


def _extract(archive: Path, staging: Path) -> None:
    seen = set()
    total_size = 0
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Fixture ZIP contains too many entries.")
        total_size = sum(info.file_size for info in members if not info.is_dir())
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Fixture ZIP exceeds the uncompressed size limit.")
        for info in members:
            target = _member_target(staging, info, seen)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as input_stream, target.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, CHUNK_SIZE)
    if not (staging / "header" / "metadata.xml").is_file() or not (staging / "content").is_dir():
        raise ValueError("The KOST archive does not contain a complete SIP.")


def provision_fixture(samples_dir: Path, archive_path: Path | None = None,
                      expected_sha256: str = FIXTURE_SHA256) -> Path:
    samples_dir = Path(samples_dir).resolve()
    samples_dir.mkdir(parents=True, exist_ok=True)
    target = samples_dir / FIXTURE_NAME
    if target.exists():
        if target.is_symlink() or not (target / "header" / "metadata.xml").is_file() or not (target / "content").is_dir():
            raise FileExistsError(f"An incomplete fixture already exists: {target}")
        return target

    temporary_archive = None
    if archive_path is None:
        handle = tempfile.NamedTemporaryFile(prefix="kost-ech0160-", suffix=".zip", delete=False)
        temporary_archive = Path(handle.name)
        handle.close()
        temporary_archive.unlink()
        archive_path = temporary_archive
        try:
            _download(archive_path)
        except BaseException:
            archive_path.unlink(missing_ok=True)
            raise

    archive_path = Path(archive_path)
    try:
        actual = _sha256(archive_path)
        if actual != expected_sha256.lower():
            raise ValueError(f"KOST fixture SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        with tempfile.TemporaryDirectory(prefix=".kost-fixture-", dir=samples_dir) as temporary_directory:
            staging = Path(temporary_directory) / FIXTURE_NAME
            staging.mkdir()
            _extract(archive_path, staging)
            if target.exists():
                raise FileExistsError(f"Fixture destination appeared during extraction: {target}")
            os.rename(staging, target)
        return target
    finally:
        if temporary_archive is not None:
            temporary_archive.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "samples")
    parser.add_argument("--archive", type=Path,
                        help="Use a previously downloaded archive instead of downloading it")
    arguments = parser.parse_args()
    path = provision_fixture(arguments.samples_dir, arguments.archive)
    print(f"Verified and provisioned {path}")


if __name__ == "__main__":
    main()