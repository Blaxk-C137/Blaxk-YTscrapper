import asyncio

import youtube_creator_finder as finder


def channel(letter: str) -> str:
    return "UC" + letter * 22


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class FakeRequest:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        return FakeResponse(self.payloads.pop(0))


def test_search_paginates_past_excluded_processed_channels(monkeypatch):
    first = [channel("a"), channel("b")]
    second = [channel("c"), channel("d")]
    request = FakeRequest([
        {
            "items": [{"browseId": value} for value in first],
            "continuationCommand": {"token": "next-page"},
        },
        {"items": [{"browseId": value} for value in second]},
    ])

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(finder.asyncio, "sleep", no_sleep)

    async def scenario():
        return await finder.search_channels(
            request,
            "vlog",
            "NG",
            2,
            asyncio.Semaphore(1),
            exclude_ids=set(first),
        )

    assert asyncio.run(scenario()) == second
    assert request.calls == 2
