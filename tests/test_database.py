from pathlib import Path

from app.database import Database, metric_number


def creator(channel_id="UC" + "a" * 22, title="Ada Creates"):
    return {
        "channel_id": channel_id,
        "handle": "@adacreates",
        "title": title,
        "description": "Design and maker videos",
        "stats": {
            "account_year": 2020,
            "joined": "Joined Jan 1, 2020",
            "country": "Nigeria",
            "subscribers": "12.4K",
            "videos": "88",
            "views": "1,203,441",
        },
        "business_email": ["hello@ada.example"],
        "emails": ["hello@ada.example"],
        "contact_source": "description",
        "contact_status": "no_reveal_button",
        "socials": {"instagram": ["adacreates"]},
        "region_searched": "Nigeria",
        "niches_matched": ["Lifestyle"],
        "found_at": "2026-10-20T10:00:00+00:00",
    }


def test_metric_number_parses_youtube_abbreviations():
    assert metric_number("21.3M") == 21_300_000
    assert metric_number("12.4K") == 12_400
    assert metric_number("1,852") == 1_852
    assert metric_number("Unknown") == 0


def test_upsert_query_stats_and_update(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    sample = creator()
    sample["regions_searched"] = ["Nigeria", "Canada"]
    db.upsert_creator(sample)

    result = db.list_creators(q="Ada", region="Nigeria", niche="Lifestyle")
    assert result["total"] == 1
    item = result["items"][0]
    assert item["subscribers_num"] == 12_400
    assert item["emails"] == ["hello@ada.example"]
    assert item["youtube_url"].endswith("/@adacreates")
    assert item["region_searched"] == "Nigeria; Canada"
    assert db.list_creators(region="Canada")["total"] == 1
    assert db.facet_values()["regions"] == ["Canada", "Nigeria"]
    assert db.add_creator_regions(item["channel_id"], ["Germany", "Canada"])
    assert db.list_creators(region="Germany")["total"] == 1
    assert db.get_creator(item["channel_id"])["region_searched"] == "Canada; Germany; Nigeria"

    stats = db.stats()
    assert stats["total"] == 1
    assert stats["with_email"] == 1
    assert stats["contact_rate"] == 100.0

    updated = db.update_creator(item["channel_id"], {
        "pipeline_stage": "shortlisted", "favorite": True, "notes": "Strong fit"
    })
    assert updated["pipeline_stage"] == "shortlisted"
    assert updated["favorite"] is True
    assert updated["notes"] == "Strong fit"


def test_bulk_update_and_processed_tracking(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    first = creator()
    second = creator("UC" + "b" * 22, "Bola Builds")
    db.upsert_creator(first)
    db.upsert_creator(second)

    assert db.is_processed(first["channel_id"])
    assert db.bulk_update([first["channel_id"], second["channel_id"]], "favorite") == 2
    assert db.stats()["favorites"] == 2
    assert db.bulk_update([first["channel_id"]], "archive") == 1
    assert db.list_creators()["total"] == 1

    cleared = db.clear_workspace()
    assert cleared["creators"] == 2
    assert db.list_creators()["total"] == 0
    assert not db.is_processed(first["channel_id"])


def test_job_lifecycle(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.create_job({
        "id": "job123", "region_code": "NG", "region_name": "Nigeria, Canada",
        "regions": [
            {"code": "NG", "name": "Nigeria"},
            {"code": "CA", "name": "Canada"},
        ],
        "niches": [{"query": "vlog", "label": "Vlogs"}],
        "preferences": {"min_subscribers": 10_000, "contact_requirement": "email"},
        "target": 10,
    })
    assert db.active_job_count() == 1
    db.update_job("job123", status="running", found=4, processed=7, filtered=2)
    job = db.get_job("job123")
    assert job["progress"] == 40
    assert job["niches"][0]["label"] == "Vlogs"
    assert [region["code"] for region in job["regions"]] == ["NG", "CA"]
    assert job["preferences"]["min_subscribers"] == 10_000
    assert job["filtered"] == 2
