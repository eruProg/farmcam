from concurrent.futures import ThreadPoolExecutor
import pytest
from persistence import PersistenceDB, CooldownManager

DET = [10,10,30,30,'worm',.9]

def test_commit_returns_exact_one_to_one_snapshot_ids(tmp_path):
    db = PersistenceDB(str(tmp_path/'farm.db'))
    old = db.commit_scan(db.begin_scan(1, 'upper'), 1, 'upper', [DET])[0]
    ids = []
    new = db.commit_scan(db.begin_scan(1, 'upper'), 1, 'upper', [DET, DET],
                         committed_track_ids=ids)
    assert ids == [old, new[0]]
    assert len(set(ids)) == 2
    assert db.get_track(old)['scans_seen'] == 2

def test_rescan_atomicity_isolation_review_and_restart(tmp_path):
    db = PersistenceDB(str(tmp_path/'farm.db'))
    first = db.commit_scan(db.begin_scan(1,'upper'),1,'upper',[DET])[0]
    other = db.commit_scan(db.begin_scan(2,'upper'),2,'upper',[DET])[0]
    scan = db.begin_scan(1,'upper')
    assert db.get_track(first)['status'] == 'active'
    db.abort_scan(scan)
    assert db.get_track(first)['status'] == 'active'
    assert db.commit_scan(db.begin_scan(1,'upper'),1,'upper',[DET]) == []
    assert db.get_track(first)['scans_seen'] == 2
    assert db.precision_by_class() == {}
    db.mark_reviewed(first)
    db.mark_false_positive(other)
    assert db.precision_by_class() == {'worm': .5}
    scan = db.begin_scan(1,'upper')
    db.commit_scan(scan,1,'upper',[])
    with pytest.raises(ValueError):
        db.commit_scan(scan,1,'upper',[])
    reopened = PersistenceDB(str(tmp_path/'farm.db'))
    assert reopened.get_track(first)['status'] == 'resolved'
    with reopened._conn() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'

def test_aborted_empty_window_does_not_clear_other_positions(tmp_path):
    db = PersistenceDB(str(tmp_path/'farm.db'))
    a = db.commit_scan(db.begin_scan(1,'upper'),1,'upper',[DET])[0]
    b = db.commit_scan(db.begin_scan(1,'lower'),1,'lower',[DET])[0]
    db.commit_scan(db.begin_scan(1,'lower'),1,'lower',[])
    assert db.get_track(a)['status'] == 'active'
    assert db.get_track(b)['status'] == 'resolved'

def test_cooldown_claim_is_atomic_and_survives_restart(tmp_path):
    name = str(tmp_path/'farm.db')
    cm = CooldownManager(PersistenceDB(name))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: cm.claim('pump',300,now=1000),range(8)))
    assert sum(results) == 1
    cm = CooldownManager(PersistenceDB(name))
    assert cm.status('pump',300,now=1100)['remaining_s'] == 200
    assert not cm.claim('pump',300,now=1299)
    assert cm.claim('pump',300,now=1300)
