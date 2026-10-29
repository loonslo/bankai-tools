import csv
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from bankai_tools.twitterapi import ApiError, NoRedirect, TwitterApiClient
from bankai_tools.xfollow import analyze, collect, main, write_output


def user(number, username="alice", **extra):
    return {"id": str(number), "username": username, "name": "", "bio": "", **extra}


class Pages:
    def __init__(self, pages):
        self.pages = iter(pages)

    def page(self, *_):
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        return value


def page(rows, cursor=None, terminal=False):
    return {"rows": rows, "cursor": cursor, "terminal": terminal}


def section(rows):
    return {"complete": True, "users": rows}


def test_collect_deduplicates_and_requires_terminal():
    result = collect(
        Pages([page([user(1)], "a"), page([user(1), user(2)], terminal=True)]), "alice", "following"
    )
    assert result["complete"] and len(result["users"]) == 2 and result["pages"] == 2


@pytest.mark.parametrize(
    "pages,reason",
    [
        ([page([user(1)], "a"), page([], "a")], "invalid_cursor"),
        ([page([user(1)])], "invalid_cursor"),
        ([page([user(1)], "a"), ApiError("synthetic")], "api_error"),
    ],
)
def test_partial_never_becomes_complete(pages, reason):
    result = collect(Pages(pages), "alice", "followers")
    assert not result["complete"] and result["reason"] == reason
    assert len(result["users"]) == 1


def test_page_and_item_limits_are_partial():
    result = collect(Pages([page([user(1)], "a")]), "alice", "followers", max_pages=1)
    assert not result["complete"] and result["reason"] == "collection_limit"
    result = collect(
        Pages([page([user(1), user(2)], terminal=True)]), "alice", "followers", max_items=1
    )
    assert not result["complete"] and len(result["users"]) == 1


def test_analysis_uses_ids_and_refuses_partial_lists():
    snapshot = {
        "following": section([user(1), user(2)]),
        "followers": section([user(1, "renamed"), user(3)]),
    }
    result = analyze(snapshot)
    assert [r["id"] for r in result["mutual"]] == ["1"]
    assert [r["id"] for r in result["not_following_back"]] == ["2"]
    assert [r["id"] for r in result["fans_only"]] == ["3"]
    snapshot["following"]["complete"] = False
    with pytest.raises(ValueError):
        analyze(snapshot)


def test_empty_complete_lists_and_invalid_input():
    assert all(
        not rows for rows in analyze({r: section([]) for r in ("following", "followers")}).values()
    )
    with pytest.raises(ValueError):
        analyze({"following": section([{"id": 1}]), "followers": section([])})


def test_csv_formula_escaping_and_no_overwrite(tmp_path):
    target = tmp_path / "result.csv"
    write_output(target, {"mutual": [user(1, name="@formula", bio=" \t=1+1")]}, "csv")
    rows = list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8-sig"))))
    assert rows[0]["name"].startswith("'") and rows[0]["bio"].startswith("'")
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        write_output(target, {})
    assert target.read_bytes() == before


def test_offline_cli_and_existing_output_preflight(tmp_path, capsys):
    source = tmp_path / "snapshot.json"
    source.write_text(
        json.dumps({r: section([]) for r in ("following", "followers")}), encoding="utf-8"
    )
    target = tmp_path / "relations.json"
    assert main(["analyze", str(source), "--output", str(target)]) == 0
    assert main(["fetch", "alice", "--output", str(target)]) == 1
    assert "already exists" in capsys.readouterr().out


def test_fetch_stops_on_partial_and_saves_without_analysis(tmp_path, monkeypatch):
    class Fake(Pages):
        def __init__(self, *_args, **_kwargs):
            super().__init__([page([user(1)], "next")])

        def user_info(self, _):
            return {"id": "10", "username": "alice"}

    monkeypatch.setattr("bankai_tools.xfollow.TwitterApiClient", Fake)
    target = tmp_path / "snapshot.json"
    assert main(["fetch", "alice", "--max-pages", "1", "--output", str(target)]) == 2
    snapshot = json.loads(target.read_text(encoding="utf-8"))
    assert not snapshot["complete"] and "analysis" not in snapshot and "followers" not in snapshot


class Response(io.BytesIO):
    status = 200


def test_client_request_and_schema():
    calls = []

    def opener(req, **_):
        calls.append(req)
        return Response(
            json.dumps(
                {"followers": [{"id": "1", "userName": "alice"}], "has_next_page": False}
            ).encode()
        )

    client = TwitterApiClient("synthetic-key", opener=opener)
    result = client.page("followers", "alice", None)
    assert result["terminal"] and result["rows"][0]["id"] == "1"
    assert "synthetic-key" not in calls[0].full_url
    assert calls[0].get_header("X-api-key") == "synthetic-key"
    with pytest.raises(ApiError):
        client.page("following", "bad/handle", None)


def test_client_network_http_and_malformed_errors_hide_key():
    key = "synthetic-key"
    for error in (URLError(key), HTTPError("https://example.com", 401, key, {}, io.BytesIO(b"{}"))):

        def opener(*_, **__):
            raise error

        with pytest.raises(ApiError) as caught:
            TwitterApiClient(key, opener=opener).user_info("alice")
        assert key not in str(caught.value)
    with pytest.raises(ApiError):
        TwitterApiClient(key, opener=lambda *_, **__: Response(b"[]")).page(
            "followers", "alice", None
        )
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None
