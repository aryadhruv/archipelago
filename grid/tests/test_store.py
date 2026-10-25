from grid.store import Store, run_id_for


def test_add_runs_is_idempotent(tmp_path):
    store = Store(tmp_path / "grid.db")
    rows = [("t1", "m1", 1), ("t1", "m2", 1)]
    assert store.add_runs(rows) == 2
    assert store.add_runs(rows) == 0
    assert [r.run_id for r in store.runs()] == [
        run_id_for("t1", "m1", 1),
        run_id_for("t1", "m2", 1),
    ]


def test_interrupted_runs_go_back_to_the_queue(tmp_path):
    path = tmp_path / "grid.db"
    store = Store(path)
    store.add_runs([("t1", "m1", 1), ("t1", "m2", 1)])
    first, second = store.runs()
    store.update_run(first.run_id, state="agent_running", port=8100)
    store.update_run(second.run_id, state="scored", score=1.0)
    store.close()

    store = Store(path)
    assert store.reset_interrupted() == [first.run_id]
    assert store.run(first.run_id).state == "retry"
    assert store.run(first.run_id).port is None
    assert store.run(second.run_id).state == "scored"


def test_events_page_by_seq(tmp_path):
    store = Store(tmp_path / "grid.db")
    a = store.add_event("run_state", "r1", state="queued")
    store.add_event("run_state", "r2", state="queued")
    store.add_event("run_state", "r1", state="scored")
    assert [e["data"]["state"] for e in store.events("r1")] == ["queued", "scored"]
    assert len(store.events(after=a)) == 2


def test_meta_round_trips(tmp_path):
    store = Store(tmp_path / "grid.db")
    store.set_meta("manifest", {"image": "x"})
    store.set_meta("manifest", {"image": "y"})
    assert store.get_meta("manifest") == {"image": "y"}
