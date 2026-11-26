import asyncio
import mailbox
import multiprocessing
import os
import threading
import time
from email.message import EmailMessage

import pytest
from models.mail import SendMailInput
from tools.list_mails import list_mails
from tools.send_mail import send_mail
from utils import mbox_utils
from utils.mbox_utils import MailboxBusy, UTF8Mbox, locked_mbox
from utils.path import get_mbox_path


@pytest.fixture(autouse=True)
def fresh_mbox():
    path = get_mbox_path()
    for p in (path, path + ".lock"):
        if os.path.exists(p):
            os.remove(p)
    yield path


def _message(i: int) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Subject"] = f"m{i}"
    msg.set_content("body")
    return msg


async def test_concurrent_sends_and_lists_all_succeed(fresh_mbox):
    async def sender(t: int) -> list[str]:
        out = []
        for i in range(25):
            r = await send_mail(
                SendMailInput(
                    from_email="a@example.com",
                    to_email="b@example.com",
                    subject=f"t{t}-{i}",
                    body="hi",
                )
            )
            out.append(r)
        return out

    stop = asyncio.Event()

    async def lister() -> list[str]:
        out = []
        while not stop.is_set():
            out.append(await list_mails(limit=100))
        return out

    listers = [asyncio.create_task(lister()) for _ in range(4)]
    sends = await asyncio.gather(*(sender(t) for t in range(8)))
    stop.set()
    lists = await asyncio.gather(*listers)

    failed = [r for b in sends for r in b if not r.startswith("Mail sent successfully")]
    assert not failed, failed[:3]
    bad = [r for b in lists for r in b if not r.startswith(("Found", "No emails"))]
    assert not bad, bad[:3]
    assert len(UTF8Mbox(fresh_mbox)) == 200


def _append_in_process(path: str, start: int, count: int) -> None:
    for i in range(start, start + count):
        box = UTF8Mbox(path)
        try:
            with locked_mbox(box, path):
                box.add(_message(i))
                box.flush()
        finally:
            box.close()


def test_two_processes_append_without_loss(fresh_mbox):
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_append_in_process, args=(fresh_mbox, n * 50, 50))
        for n in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    subjects = {m["Subject"] for m in UTF8Mbox(fresh_mbox)}
    assert subjects == {f"m{i}" for i in range(100)}


def test_lock_times_out_as_busy(fresh_mbox, monkeypatch):
    monkeypatch.setattr(mbox_utils, "LOCK_TIMEOUT_SEC", 0.3)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        box = UTF8Mbox(fresh_mbox)
        with locked_mbox(box, fresh_mbox):
            held.set()
            release.wait(5)
        box.close()

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    start = time.monotonic()
    with pytest.raises(MailboxBusy):
        with locked_mbox(UTF8Mbox(fresh_mbox), fresh_mbox):
            pass
    assert time.monotonic() - start >= 0.3
    release.set()
    t.join()


def test_stale_dot_lock_is_removed(fresh_mbox, monkeypatch):
    monkeypatch.setattr(mbox_utils, "STALE_LOCK_SEC", 1)
    UTF8Mbox(fresh_mbox).close()
    dot_lock = fresh_mbox + ".lock"
    open(dot_lock, "w").close()
    old = time.time() - 5
    os.utime(dot_lock, (old, old))
    box = UTF8Mbox(fresh_mbox)
    with locked_mbox(box, fresh_mbox):
        box.add(_message(0))
        box.flush()
    box.close()
    assert len(mailbox.mbox(fresh_mbox)) == 1
