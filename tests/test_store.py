"""De-duplication: being told about the same job twice is the loudest failure."""
import json
from datetime import datetime, timedelta, timezone

from jobtracker.models import Job
from jobtracker.store import SeenStore


def job(title="Data Engineer", company="Wiz", location="Tel Aviv", url="https://a"):
    return Job(title=title, company=company, location=location, url=url, source="test")


def test_new_jobs_filters_already_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    first = job()
    assert store.new_jobs([first]) == [first]

    store.mark_seen([first])
    assert store.new_jobs([job()]) == []


def test_duplicates_within_one_batch_collapse(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert len(store.new_jobs([job(), job(), job()])) == 1


def test_same_role_from_two_sources_alerts_once(tmp_path):
    """The identity is company+title+location, so differing URLs still collapse."""
    store = SeenStore(tmp_path / "seen.json")
    via_ats = Job(title="Data Engineer", company="Wiz", location="Tel Aviv",
                  url="https://boards.greenhouse.io/x", source="greenhouse")
    via_linkedin = Job(title="data  engineer", company="wiz", location="Tel Aviv",
                       url="https://linkedin.com/jobs/y", source="linkedin")
    assert len(store.new_jobs([via_ats, via_linkedin])) == 1


def test_a_respelt_location_is_still_the_same_job(tmp_path):
    """The bug that caused repeats: sources word the same office differently."""
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([job(location="Tel Aviv, Israel")])

    respelt = job(location="Tel Aviv-Yafo, Tel Aviv District, Israel")
    assert store.new_jobs([respelt]) == []


def test_the_same_job_from_two_sources_collapses_despite_location_wording(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    from_ats = Job(title="Product Analyst", company="Similarweb",
                   location="Tel Aviv-Yafo, Tel Aviv District, Israel",
                   url="https://boards.greenhouse.io/x", source="greenhouse")
    from_linkedin = Job(title="Product Analyst", company="Similarweb",
                        location="Tel Aviv, Israel",
                        url="https://linkedin.com/jobs/y", source="linkedin")
    assert len(store.new_jobs([from_ats, from_linkedin])) == 1


# -- state migration ---------------------------------------------------------

def test_state_written_under_the_old_fingerprint_is_migrated(tmp_path):
    """Old records keyed by company+title+location must not all re-alert."""
    import hashlib
    from jobtracker.models import _norm

    old_key = hashlib.sha256("|".join([
        _norm("Wiz"), _norm("Data Engineer"), _norm("Tel Aviv"),
    ]).encode("utf-8")).hexdigest()[:16]

    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"seen": {old_key: {
        "title": "Data Engineer", "company": "Wiz", "location": "Tel Aviv",
        "first_seen": "2026-08-01T09:00:00+00:00",
    }}}), encoding="utf-8")

    store = SeenStore(path)
    assert old_key not in store._data              # re-keyed
    assert len(store) == 1                         # not lost
    assert store.new_jobs([job()]) == []           # and still recognised


def test_migration_collapses_duplicates_and_keeps_the_earliest(tmp_path):
    """Two old spellings of one job become a single record."""
    import hashlib
    from jobtracker.models import _norm

    def old_key(loc):
        return hashlib.sha256("|".join([
            _norm("Wiz"), _norm("Data Engineer"), _norm(loc)]).encode("utf-8")
        ).hexdigest()[:16]

    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"seen": {
        old_key("Tel Aviv"): {"title": "Data Engineer", "company": "Wiz",
                              "location": "Tel Aviv",
                              "first_seen": "2026-08-05T09:00:00+00:00"},
        old_key("Tel Aviv-Yafo, Israel"): {"title": "Data Engineer", "company": "Wiz",
                                           "location": "Tel Aviv-Yafo, Israel",
                                           "first_seen": "2026-08-01T09:00:00+00:00"},
    }}), encoding="utf-8")

    store = SeenStore(path)
    assert len(store) == 1
    assert list(store._data.values())[0]["first_seen"] == "2026-08-01T09:00:00+00:00"


def test_migration_is_a_no_op_on_current_state(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen([job()])
    store.save()

    reloaded = SeenStore(path)
    assert reloaded._rekey() == 0
    assert len(reloaded) == 1


def test_state_survives_a_reload(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen([job()])
    store.save()

    assert SeenStore(path).new_jobs([job()]) == []


def test_corrupt_state_file_does_not_stop_the_run(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json at all", encoding="utf-8")
    store = SeenStore(path)
    assert len(store) == 0
    assert store.new_jobs([job()])  # still usable


def test_prune_drops_only_expired_records(tmp_path):
    path = tmp_path / "seen.json"
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(timespec="seconds")
    recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps({"seen": {
        "aaa": {"title": "Old", "first_seen": old},
        "bbb": {"title": "Recent", "first_seen": recent},
    }}), encoding="utf-8")

    store = SeenStore(path)
    assert store.prune() == 1
    assert len(store) == 1


# -- merging (what makes the CI push race survivable) ------------------------

def test_merge_is_a_union_not_last_writer_wins(tmp_path):
    """Two runs alerting on different jobs must both be remembered."""
    theirs = SeenStore(tmp_path / "theirs.json")
    theirs.mark_seen([job(title="Data Engineer")])

    ours = SeenStore(tmp_path / "ours.json")
    ours.mark_seen([job(title="Data Analyst")])
    added = ours.merge(theirs._data)

    assert added == 1
    assert len(ours) == 2
    assert ours.new_jobs([job(title="Data Engineer"), job(title="Data Analyst")]) == []


def test_merge_is_idempotent(tmp_path):
    """CI may re-apply this several times while losing push races."""
    theirs = SeenStore(tmp_path / "theirs.json")
    theirs.mark_seen([job()])

    ours = SeenStore(tmp_path / "ours.json")
    assert ours.merge(theirs._data) == 1
    assert ours.merge(theirs._data) == 0
    assert ours.merge(theirs._data) == 0
    assert len(ours) == 1


def test_merge_order_does_not_matter(tmp_path):
    a = SeenStore(tmp_path / "a.json"); a.mark_seen([job(title="A")])
    b = SeenStore(tmp_path / "b.json"); b.mark_seen([job(title="B")])

    one = SeenStore(tmp_path / "one.json")
    one.merge(a._data); one.merge(b._data)
    two = SeenStore(tmp_path / "two.json")
    two.merge(b._data); two.merge(a._data)

    assert set(one._data) == set(two._data)


def test_merge_keeps_the_earliest_sighting(tmp_path):
    """Retention must measure from when the job was really first seen."""
    record = {"title": "Data Engineer", "company": "Wiz", "location": "Tel Aviv"}
    key = SeenStore._key_for(record)

    store = SeenStore(tmp_path / "s.json")
    store._data = {key: dict(record, first_seen="2026-08-07T12:00:00+00:00")}
    store.merge({key: dict(record, first_seen="2026-08-01T09:00:00+00:00")})

    assert store._data[key]["first_seen"] == "2026-08-01T09:00:00+00:00"


def test_merge_file_reads_a_state_file(tmp_path):
    theirs = SeenStore(tmp_path / "theirs.json")
    theirs.mark_seen([job()])
    theirs.save()

    ours = SeenStore(tmp_path / "ours.json")
    assert ours.merge_file(tmp_path / "theirs.json") == 1
    assert ours.new_jobs([job()]) == []


def test_merging_an_old_format_file_does_not_double_the_state(tmp_path):
    """Trusting the other file's keys once doubled 36 records into 71."""
    import hashlib
    from jobtracker.models import _norm

    old_key = hashlib.sha256("|".join([
        _norm("Wiz"), _norm("Data Engineer"), _norm("Tel Aviv"),
    ]).encode("utf-8")).hexdigest()[:16]

    old_file = tmp_path / "old.json"
    old_file.write_text(json.dumps({"seen": {old_key: {
        "title": "Data Engineer", "company": "Wiz", "location": "Tel Aviv",
        "first_seen": "2026-08-01T09:00:00+00:00",
    }}}), encoding="utf-8")

    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([job()])                       # same job, current key
    assert store.merge_file(old_file) == 0         # recognised, not re-added
    assert len(store) == 1


def test_merging_a_file_into_itself_is_a_no_op(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen([job(title="A"), job(title="B")])
    store.save()

    reloaded = SeenStore(path)
    assert reloaded.merge_file(path) == 0
    assert len(reloaded) == 2


def test_merge_file_survives_a_missing_or_corrupt_file(tmp_path):
    ours = SeenStore(tmp_path / "ours.json")
    assert ours.merge_file(tmp_path / "nope.json") == 0

    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert ours.merge_file(bad) == 0


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([job()])
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]
