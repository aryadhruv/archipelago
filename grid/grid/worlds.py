"""Turn worlds and task overlays into the per-subsystem tarballs /data/populate takes.

Each world zip is converted once and cached by content hash, so a world shared
by many tasks and models isn't re-packed for every run.

The simulated human's folder (.apps_data/human/) never goes into the sandbox:
it's left out of the tarballs and laid out on the host by ``human_folder``.
"""

import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

SUBSYSTEMS = ("filesystem", ".apps_data")
HUMAN_DIR = ".apps_data/human"
# Part of every cache key; bump it when what goes into a tarball changes.
PACK_VERSION = 2


def _is_human(subsystem: str, rest: str) -> bool:
    path = f"{subsystem}/{rest}".rstrip("/")
    return path == HUMAN_DIR or path.startswith(HUMAN_DIR + "/")


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
    directory = (
        cache_dir
        / "worlds"
        / f"{world_zip.stem}-{_file_digest(world_zip)}-v{PACK_VERSION}"
    )
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
                if (
                    subsystem not in SUBSYSTEMS
                    or not rest
                    or _is_human(subsystem, rest)
                ):
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
    directory = (
        cache_dir
        / "tasks"
        / f"{task_dir.name}-{_tree_digest(task_dir)}-v{PACK_VERSION}"
    )
    if (done := _finished(directory)) is not None:
        return done

    tmp = directory.with_name(directory.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for subsystem in SUBSYSTEMS:
        root = task_dir / subsystem
        entries = [
            e
            for e in (sorted(root.rglob("*")) if root.is_dir() else [])
            if not _is_human(subsystem, e.relative_to(root).as_posix())
        ]
        if not entries:
            continue
        with tarfile.open(tmp / f"{subsystem}.tar.gz", "w:gz") as tar:
            # HF snapshots store files as symlinks to blobs.
            tar.dereference = True
            for entry in entries:
                tar.add(entry, arcname=str(entry.relative_to(root)), recursive=False)
    return _publish(tmp, directory)


def human_folder(world_zip: Path, task_dir: Path | None, cache_dir: Path) -> Path:
    """Lay out the simulated human's folder for one task, on the host.

    ``human/`` holds the world's .apps_data/human/ with the task overlay's on
    top (knowledge.md, files/). ``agent_files.txt`` lists the paths the agent
    starts with, so the human can point to them.
    """
    overlay = task_dir if task_dir is not None and task_dir.is_dir() else None
    key = _file_digest(world_zip) + (_tree_digest(overlay) if overlay else "")
    name = f"{world_zip.stem}-{overlay.name if overlay else 'world'}"
    directory = (
        cache_dir / "humans" / f"{name}-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
    )
    if (directory / ".done").exists():
        return directory

    tmp = directory.with_name(directory.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "human").mkdir(parents=True)
    agent_files: set[str] = set()
    prefix = HUMAN_DIR + "/"
    with zipfile.ZipFile(world_zip) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.filename.startswith(prefix):
                dest = (tmp / "human" / info.filename[len(prefix) :]).resolve()
                if dest.is_relative_to((tmp / "human").resolve()):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
            elif info.filename.startswith("filesystem/"):
                agent_files.add(info.filename.removeprefix("filesystem/"))
    if overlay:
        human = overlay / HUMAN_DIR
        if human.is_dir():
            shutil.copytree(human, tmp / "human", dirs_exist_ok=True)
        fs = overlay / "filesystem"
        if fs.is_dir():
            agent_files |= {
                p.relative_to(fs).as_posix() for p in fs.rglob("*") if p.is_file()
            }
    (tmp / "agent_files.txt").write_text("".join(f"{p}\n" for p in sorted(agent_files)))
    (tmp / ".done").touch()
    if directory.exists():
        shutil.rmtree(directory)
    tmp.rename(directory)
    return directory


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
