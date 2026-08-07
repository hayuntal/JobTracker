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


def test_different_location_is_a_different_job(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert len(store.new_jobs([job(location="Tel Aviv"), job(location="Haifa")])) == 2


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


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([job()])
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]
