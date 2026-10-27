"""Turn worlds and task overlays into the per-subsystem tarballs /data/populate takes.

Each world zip is converted once and cached by content hash, so a world shared
by many tasks and models isn't re-packed for every run.
"""

import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

SUBSYSTEMS = ("filesystem", ".apps_data")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(
                f"{path.relative_to(root)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()
            )
    return digest.hexdigest()[:16]


def _finished(directory: Path) -> dict[str, Path] | None:
    if not (directory / ".done").exists():
        return None
    return {
        s: directory / f"{s}.tar.gz"
        for s in SUBSYSTEMS
        if (directory / f"{s}.tar.gz").exists()
    }


def _publish(tmp: Path, directory: Path) -> dict[str, Path]:
    (tmp / ".done").touch()
    if directory.exists():
        shutil.rmtree(directory)
    tmp.rename(directory)
    return _finished(directory) or {}


def world_tarballs(world_zip: Path, cache_dir: Path) -> dict[str, Path]:
    """Split a world zip into one tar.gz per subsystem, without the subsystem prefix."""
    directory = cache_dir / "worlds" / f"{world_zip.stem}-{_file_digest(world_zip)}"
    if (done := _finished(directory)) is not None:
        return done

    tmp = directory.with_name(directory.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    tars: dict[str, tarfile.TarFile] = {}
    try:
        with zipfile.ZipFile(world_zip) as zf:
            for info in zf.infolist():
                subsystem, _, rest = info.filename.partition("/")
                if subsystem not in SUBSYSTEMS or not rest:
                    continue
                if subsystem not in tars:
                    tars[subsystem] = tarfile.open(tmp / f"{subsystem}.tar.gz", "w:gz")
                member = tarfile.TarInfo(name=rest.rstrip("/"))
                if info.is_dir():
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    tars[subsystem].addfile(member)
                else:
                    member.size = info.file_size
                    member.mode = 0o644
                    with zf.open(info) as src:
                        tars[subsystem].addfile(member, src)
    finally:
        for tar in tars.values():
            tar.close()
    return _publish(tmp, directory)


def overlay_tarballs(task_dir: Path, cache_dir: Path) -> dict[str, Path]:
    """Tar a task's overlay folder (filesystem/, .apps_data/) per subsystem."""
    directory = cache_dir / "tasks" / f"{task_dir.name}-{_tree_digest(task_dir)}"
    if (done := _finished(directory)) is not None:
        return done

    tmp = directory.with_name(directory.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for subsystem in SUBSYSTEMS:
        root = task_dir / subsystem
        entries = sorted(root.rglob("*")) if root.is_dir() else []
        if not entries:
            continue
        with tarfile.open(tmp / f"{subsystem}.tar.gz", "w:gz") as tar:
            # HF snapshots store files as symlinks to blobs.
            tar.dereference = True
            for entry in entries:
                tar.add(entry, arcname=str(entry.relative_to(root)), recursive=False)
    return _publish(tmp, directory)


def tar_gz_to_zip(tar_gz_path: Path, zip_path: Path) -> Path:
    """Convert a /data/snapshot tarball into the zip grading reads."""
    with (
        tarfile.open(tar_gz_path, "r:gz") as tar,
        zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        for member in tar:
            if not member.isfile():
                continue
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, zf.open(member.name, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return zip_path
