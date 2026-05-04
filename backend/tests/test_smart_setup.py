import json

from openpulse.smart_setup import (
    GoogleAIStudioAdvisor,
    NetworkRecord,
    SmartSetupService,
    extract_network_recipe,
)


def search_record(inventories, *, sequence=1):
    return NetworkRecord(
        sequence=sequence,
        captured_at="2026-05-03T12:00:00Z",
        url="https://www.redbus.in/rpw/api/searchResults?fromCity=130&toCity=122&DOJ=04-May-2026",
        method="POST",
        status=200,
        resource_type="fetch",
        content_type="application/json",
        post_data_sha256="abc",
        post_data_preview="{}",
        json_body={"data": {"inventories": inventories}},
        scalar_count=10,
    )


class FakeAdvisor:
    def __init__(self, decision):
        self.decision = decision
        self.packets = []

    async def choose_recipe(self, packet):
        self.packets.append(packet)
        return self.decision


async def test_smart_setup_builds_network_recipe_that_survives_reordering():
    records = [
        search_record(
            [
                {
                    "routeId": 111,
                    "operatorId": 1,
                    "travelsName": "Other Bus",
                    "departureTime": "2026-05-04 17:40:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2769]}]},
                },
                {
                    "routeId": 38086756,
                    "operatorId": 20218,
                    "travelsName": "zingbus plus",
                    "busType": "Bharat Benz A/C Sleeper (2+1)",
                    "departureTime": "2026-05-04 18:25:00",
                    "arrivalTime": "2026-05-05 09:50:00",
                    "availableSeats": 15,
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2586], "OriginalPrices": [2955]}]},
                },
            ]
        )
    ]
    selection = {
        "url": "https://www.redbus.in/bus-tickets/pune-to-bangalore",
        "semanticType": "price",
        "initialValue": "₹2,586",
        "nearbyText": "zingbus plus 18:25 09:50 15 Seats ₹2,955 ₹2,586",
        "selector": "html > body > div:nth-of-type(3)",
    }
    advisor = FakeAdvisor(
        {
            "source": "network",
            "candidateId": "net-1",
            "identityFields": ["routeId", "operatorId", "departureTime"],
            "valuePath": "$.operatorOfferCampaign.CmpgList[0].DiscountedPrices[0]",
            "label": "zingbus plus discounted price",
            "confidence": 0.93,
        }
    )

    enriched = await SmartSetupService(advisor).prepare_selection(selection, records)

    assert enriched["sourceType"] == "network"
    assert enriched["networkRecipe"]["collectionPath"] == "$.data.inventories[*]"
    assert enriched["networkRecipe"]["identity"] == {
        "routeId": 38086756,
        "operatorId": 20218,
        "departureTime": "2026-05-04 18:25:00",
    }
    assert enriched["networkRecipe"]["valuePath"] == "$.operatorOfferCampaign.CmpgList[0].DiscountedPrices[0]"
    assert enriched["initialValue"] == "2586"
    assert enriched["smartSetup"]["verification"]["status"] == "verified"
    assert advisor.packets[0]["networkCandidates"][0]["candidateId"] == "net-1"

    changed_records = [
        search_record(
            [
                {
                    "routeId": 999,
                    "operatorId": 9,
                    "travelsName": "Newly Added Bus",
                    "departureTime": "2026-05-04 18:00:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [1999]}]},
                },
                {
                    "routeId": 111,
                    "operatorId": 1,
                    "travelsName": "Other Bus",
                    "departureTime": "2026-05-04 17:40:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2769]}]},
                },
                {
                    "routeId": 38086756,
                    "operatorId": 20218,
                    "travelsName": "zingbus plus",
                    "departureTime": "2026-05-04 18:25:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2632]}]},
                },
            ],
            sequence=2,
        )
    ]

    result = extract_network_recipe(changed_records, enriched["networkRecipe"])

    assert result.found is True
    assert result.value == "2632"
    assert result.details["matchedIndex"] == 2


async def test_network_recipe_reports_missing_instead_of_using_new_item_at_old_index():
    original_records = [
        search_record(
            [
                {
                    "routeId": 38086756,
                    "operatorId": 20218,
                    "departureTime": "2026-05-04 18:25:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2586]}]},
                }
            ]
        )
    ]
    selection = {"semanticType": "price", "initialValue": "₹2,586", "nearbyText": "zingbus plus ₹2,586"}
    advisor = FakeAdvisor(
        {
            "source": "network",
            "candidateId": "net-1",
            "identityFields": ["routeId", "operatorId", "departureTime"],
            "valuePath": "$.operatorOfferCampaign.CmpgList[0].DiscountedPrices[0]",
            "label": "discounted price",
            "confidence": 0.9,
        }
    )
    enriched = await SmartSetupService(advisor).prepare_selection(selection, original_records)
    missing_records = [
        search_record(
            [
                {
                    "routeId": 123,
                    "operatorId": 456,
                    "departureTime": "2026-05-04 18:25:00",
                    "operatorOfferCampaign": {"CmpgList": [{"DiscountedPrices": [2586]}]},
                }
            ],
            sequence=2,
        )
    ]

    result = extract_network_recipe(missing_records, enriched["networkRecipe"])

    assert result.found is False
    assert result.value is None
    assert result.details["reason"] == "identity_not_found"


async def test_smart_setup_keeps_dom_selection_when_advisor_selects_dom():
    selection = {
        "semanticType": "text",
        "initialValue": "Registration closes on May 10",
        "selector": "#deadline",
        "nearbyText": "Admissions Registration closes on May 10",
    }
    advisor = FakeAdvisor({"source": "dom", "label": "registration deadline", "confidence": 0.88})

    enriched = await SmartSetupService(advisor).prepare_selection(selection, [])

    assert enriched["sourceType"] == "dom"
    assert "networkRecipe" not in enriched
    assert enriched["smartSetup"]["decision"]["source"] == "dom"


def test_google_ai_studio_advisor_sends_structured_json_request():
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "source": "network",
                                                "candidateId": "net-1",
                                                "identityFields": ["id"],
                                                "valuePath": "$.price",
                                                "label": "price",
                                                "confidence": 0.8,
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    advisor = GoogleAIStudioAdvisor(api_key="test-key", model="gemini-test", urlopen=fake_urlopen)

    decision = advisor.choose_recipe_sync({"clickedText": "$10", "networkCandidates": [], "domCandidate": {}})

    assert decision["candidateId"] == "net-1"
    request = calls[0][0]
    assert "models/gemini-test:generateContent" in request.full_url
    assert "key=test-key" in request.full_url
    body = json.loads(request.data.decode())
    assert body["generationConfig"]["response_mime_type"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0


def test_google_ai_studio_advisor_accepts_gemma_fenced_json_response():
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": (
                                            ' {\n  "source": "dom",\n  "label": "price",\n'
                                            '  "confidence": 1.0\n}\n```'
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode()

    advisor = GoogleAIStudioAdvisor(api_key="test-key", model="gemma-test", urlopen=lambda *_args, **_kw: FakeResponse())

    decision = advisor.choose_recipe_sync({"clickedText": "$10"})

    assert decision == {"source": "dom", "label": "price", "confidence": 1.0}
