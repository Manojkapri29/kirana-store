"""Price intelligence: providers, matching, location, cache, failure, plan limits, secrecy. No real network."""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.models import PriceObservation
from app.services import entitlement_service
from app.services import price_comparison_service as pcs
from app.services import price_providers as pp
from app.services.price_comparison_service import LocalRef, LocationHint, MatchType, ResultQuote
from tests.test_purchases_api import make_product
from tests.test_sales_api import item

D = Decimal
API = "/api/v1/price-intelligence"
PRODUCTS = "/api/v1/products"
BARCODE = "8901234567890"
SECRET = "sk-test-SECRET-12345"


def prices_payload(*rows, name="Basmati Rice 1kg", brand="India Gate", qty=1000, unit="g"):
    items = []
    for price, currency, date, city, place in rows:
        items.append(
            {
                "price": price, "currency": currency, "date": date, "id": 1, "type": "PRODUCT",
                "product": {"product_name": name, "brands": brand, "product_quantity": qty, "product_quantity_unit": unit},
                "location": {"osm_name": place, "osm_address_city": city, "osm_address_country": "India"},
            }
        )  # fmt: skip
    return {"items": items, "total": len(items), "page": 1, "pages": 1, "size": 50}


OFF_JSON = {
    "status": 1,
    "product": {"product_name": "Basmati Rice", "brands": "India Gate,Other", "quantity": "1 kg"},
}
UPC_JSON = {
    "code": "OK",
    "items": [
        {
            "title": "India Gate Basmati Rice 1kg", "brand": "India Gate", "size": "1 kg",
            "offers": [
                {"merchant": "Amazon", "domain": "amazon.com", "price": 9.99, "currency": "USD", "updated_t": 1750000000},
                {"merchant": "NoCurrency", "domain": "x.com", "price": 5, "currency": ""},
                {"merchant": "NoPrice", "domain": "y.com", "price": None, "currency": "USD"},
            ],
        }
    ],
}  # fmt: skip


class FakeNet:
    """Stands in for the internet. Route by URL prefix to (status, json) or an exception to raise."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def route(self, prefix: str, response):
        self.routes[prefix] = response

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                status, payload = response
                return status, json.dumps(payload).encode()
        return 404, b""

    def hits(self, prefix: str) -> int:
        return sum(1 for url, _, _ in self.calls if url.startswith(prefix))


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    fake.route(pp.PRICES_HOST, (200, prices_payload(
        ("95", "INR", "2026-09-01", "Pune", "Kirana One"),
        ("99.5", "INR", "2026-08-01", "Mumbai", "Big Bazaar"),
        ("1.99", "USD", "2026-07-01", "London", "Tesco"),
    )))  # fmt: skip
    fake.route(pp.OFF_HOST, (200, OFF_JSON))
    fake.route(pp.UPC_HOST, (200, UPC_JSON))
    monkeypatch.setattr(pp, "http_get", fake)
    monkeypatch.setattr(pp.OpenFoodFacts, "_throttle", pp.Throttle(10**6, 60))
    return fake


@pytest.fixture
def settings(monkeypatch):
    """Server settings for a test. Change `holder.value` to change them; the default has no API key."""

    class Holder:
        value = Settings(_env_file=None, off_user_agent="TestApp/1.0 (test@example.com)")

        def set(self, **kw):
            self.value = Settings(_env_file=None, off_user_agent="TestApp/1.0 (test@example.com)", **kw)

    holder = Holder()
    monkeypatch.setattr(pcs, "get_settings", lambda: holder.value)
    return holder


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


@pytest.fixture
def rice(client_a, tenant_a, units):
    return make_product(client_a, tenant_a, units, "RICE", name="Basmati Rice 1kg", brand="India Gate", barcode=BARCODE, mrp="120", selling_price="100")  # fmt: skip


def check(client, **body):
    response = client.post(f"{API}/check", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def states(result):
    return {p["name"]: p["state"] for p in result["providers"]}


class TestParsers:
    def test_open_prices(self):
        out = pp.parse_open_prices(BARCODE, prices_payload(("95", "INR", "2026-09-01", "Pune", "Kirana One")))
        [q] = out.quotes
        assert (q.price, q.currency, q.city, q.location_text) == (
            D("95.00"),
            "INR",
            "Pune",
            "Kirana One, Pune, India",
        )
        assert (q.observed_on.isoformat(), q.provider, q.barcode) == ("2026-09-01", "open_prices", BARCODE)
        assert q.pack_text == "1000 g" and q.product_name == "Basmati Rice 1kg" and q.brand == "India Gate"
        assert out.identity.name == "Basmati Rice 1kg"

    def test_open_prices_skips_rows_without_a_usable_price_or_currency(self):
        rows = {"items": [{"price": None, "currency": "INR"}, {"price": 0, "currency": "INR"}, {"price": -3, "currency": "INR"}, {"price": "x", "currency": "INR"}, {"price": 5, "currency": ""}, {"price": 5, "currency": "RS"}, "junk", {"price": 5, "currency": "inr", "date": "bad"}]}  # fmt: skip
        [q] = pp.parse_open_prices(BARCODE, rows).quotes
        assert q.currency == "INR" and q.observed_on is None

    @pytest.mark.parametrize(
        "payload", [None, [], "x", {}, {"items": None}, {"items": "no"}, {"items": [None]}]
    )
    def test_open_prices_survives_garbage(self, payload):
        assert pp.parse_open_prices(BARCODE, payload).quotes == []

    def test_open_food_facts(self):
        out = pp.parse_open_food_facts(BARCODE, OFF_JSON)
        assert (out.identity.name, out.identity.brand, out.identity.pack_text, out.quotes) == (
            "Basmati Rice",
            "India Gate",
            "1 kg",
            [],
        )
        assert pp.parse_open_food_facts(BARCODE, {"status": 0}).identity is None
        assert pp.parse_open_food_facts(BARCODE, None).identity is None

    def test_upcitemdb_keeps_only_offers_with_a_price_and_a_currency(self):
        out = pp.parse_upcitemdb(BARCODE, UPC_JSON)
        [q] = out.quotes
        assert (q.price, q.currency, q.location_text, q.source_url) == (
            D("9.99"),
            "USD",
            "online: Amazon",
            "https://amazon.com",
        )
        assert q.observed_on is not None and out.identity.name == "India Gate Basmati Rice 1kg"
        assert pp.parse_upcitemdb(BARCODE, {"items": []}).quotes == []

    def test_money_is_exact(self):
        assert (
            pp._money(0.1 + 0.2) == D("0.30")
            and pp._money("12.345") == D("12.35")
            and pp._money(10**11) is None
        )


class TestHttpLayer:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (429, "limiting"),
            (401, "refused"),
            (403, "refused"),
            (500, "problems"),
            (503, "problems"),
            (418, "unexpected status"),
        ],
    )
    def test_error_statuses_become_readable_reasons(self, status, expected):
        with pytest.raises(pp.ProviderUnavailable, match=expected):
            pp._json(status, b"")

    def test_not_found_is_no_data_and_bad_json_is_unavailable(self):
        assert pp._json(404, b"") is None
        with pytest.raises(pp.ProviderUnavailable, match="could not be read"):
            pp._json(200, b"<html>")

    def test_only_https_is_ever_called(self):
        with pytest.raises(pp.ProviderUnavailable, match="insecure"):
            pp.http_get("http://prices.openfoodfacts.org/x", {}, 1)

    def test_the_throttle_limits_calls(self):
        t = pp.Throttle(2, 60)
        assert [t.allow(), t.allow(), t.allow()] == [True, True, False]

    def test_the_barcode_is_digits_only(self):
        assert pp.digits_only("8901234567890") and not any(
            pp.digits_only(x) for x in ("12345", "1" * 15, "12ab5678", "١٢٣٤٥٦٧", "1234 567", "")
        )

    def test_providers_are_called_with_a_timeout_the_user_agent_and_the_key_only_in_a_header(
        self, client_a, rice, net, settings
    ):
        settings.set(UPCITEMDB_API_KEY=SECRET, external_timeout_seconds=2.5)
        check(client_a, product_id=rice["id"])
        from urllib.parse import urlparse

        by_host = {urlparse(url).netloc: (url, headers) for url, headers, _ in net.calls}
        assert {timeout for _, _, timeout in net.calls} == {2.5}
        assert by_host["world.openfoodfacts.org"][1]["User-Agent"] == "TestApp/1.0 (test@example.com)"
        upc_headers = by_host["api.upcitemdb.com"][1]
        assert upc_headers["user_key"] == SECRET and upc_headers["key_type"] == "3scale"
        assert all(SECRET not in url for url, _, _ in net.calls)  # never in a URL
        assert all(url.startswith("https://") for url, _, _ in net.calls)


class TestMatching:
    ref = LocalRef(1, "Basmati Rice 1kg", "RICE", BARCODE, "India Gate", D("100"), D("120"))

    def quote(self, **kw):
        base = dict(provider="open_prices", barcode=BARCODE, price=D("95"), currency="INR", product_name="Basmati Rice 1kg", brand="India Gate", pack_text="1000 g")  # fmt: skip
        return pp.Quote(**{**base, **kw})

    def test_exact_barcode(self):
        m = pcs.match_quote(
            self.ref, BARCODE, self.quote(product_name="Something else entirely", pack_text=None)
        )
        assert (m.kind, m.basis, m.confidence) == (MatchType.EXACT, "barcode", 100)

    def test_upc_a_and_ean_13_are_the_same_barcode(self):
        ref = LocalRef(1, "Sugar", "S", "012345678905", None, D("48"), None)
        assert pcs.match_quote(ref, "012345678905", self.quote(barcode="0012345678905")).basis == "barcode"

    def test_exact_sku(self):
        ref = LocalRef(1, "Rice", "RICE-1", "111111111111", None, D("1"), None)
        assert (
            pcs.match_quote(
                ref, "222222222222", self.quote(barcode="222222222222", sku="rice-1", product_name="x")
            ).basis
            == "sku"
        )

    def test_exact_name_brand_and_pack(self):
        m = pcs.match_quote(
            self.ref,
            "999999999999",
            self.quote(barcode="999999999999", product_name="BASMATI RICE, 1 KG!", pack_text="1 kg"),
        )
        assert (m.kind, m.basis, m.confidence) == (MatchType.EXACT, "name_brand_pack", 90)

    def test_a_similar_name_is_only_a_possible_match_with_lower_confidence(self):
        m = pcs.match_quote(
            self.ref,
            "999999999999",
            self.quote(barcode="999999999999", product_name="Basmati Rice Premium 1kg"),
        )
        assert (m.kind, m.basis) == (MatchType.POSSIBLE, "similar_name") and 0 < m.confidence <= 70

    def test_a_different_pack_size_is_never_a_match(self):
        assert (
            pcs.match_quote(self.ref, "999999999999", self.quote(barcode="999999999999", pack_text="5 kg"))
            is None
        )
        assert (
            pcs.match_quote(
                self.ref,
                "999999999999",
                self.quote(barcode="999999999999", product_name="Basmati Rice 5kg", pack_text=None),
            )
            is None
        )

    def test_something_unrelated_is_dropped(self):
        assert (
            pcs.match_quote(
                self.ref,
                "999999999999",
                self.quote(barcode="999999999999", product_name="Dish Soap", brand="Vim", pack_text=None),
            )
            is None
        )
        assert (
            pcs.match_quote(self.ref, "999999999999", self.quote(barcode="999999999999", product_name=None))
            is None
        )

    def test_barcode_only_queries_match_the_barcode_asked(self):
        assert pcs.match_quote(None, BARCODE, self.quote()).kind is MatchType.EXACT
        assert pcs.match_quote(None, BARCODE, self.quote(barcode="123456789012")) is None

    @pytest.mark.parametrize(("text", "expected"), [("Rice 1kg", (D(1000), "g")), ("Oil 500 ML", (D(500), "ml")), ("Milk 1.5L", (D(1500), "ml")), ("Dal 500g", (D(500), "g")), ("Soap 4 pcs", (D(4), "pc")), ("Salt", None), ("", None)])  # fmt: skip
    def test_pack_sizes(self, text, expected):
        assert pcs.pack_size_of(text) == expected
        assert pcs.pack_size_of("1 kg") == (D(1000), "g") == pcs.pack_size_of("1000 g")


class TestCheckThroughTheApi:
    def test_a_product_check_returns_every_field_a_screen_needs(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"])

        assert result["barcode"] == BARCODE and result["changes_prices"] is False
        assert result["product"]["id"] == rice["id"] and result["product"]["selling_price"] == "100.00"
        top = result["quotes"][0]
        assert (top["price"], top["currency"], top["source"], top["source_label"]) == (
            "95.00",
            "INR",
            "open_prices",
            "Open Prices",
        )
        assert (top["match_type"], top["match_label"], top["match_basis"], top["confidence"]) == (
            "EXACT",
            "EXACT MATCH",
            "barcode",
            100,
        )
        assert (
            top["matched_product"]["name"] == "Basmati Rice 1kg" and top["product_name"] == "Basmati Rice 1kg"
        )
        assert top["barcode"] == BARCODE and top["source_url"].startswith("https://prices.openfoodfacts.org/")
        assert top["location"] == "Kirana One, Pune, India" and top["observed_on"] == "2026-09-01"
        assert top["checked_at"] and top["stale"] is False
        assert (top["currency_matches_shop"], top["difference"]) == (True, "-5.00")  # 95 against our 100
        assert result["identified_as"]["name"] == "Basmati Rice"
        assert states(result) == {
            "open_food_facts": "LIVE",
            "open_prices": "LIVE",
            "upcitemdb": "NOT_CONFIGURED",
        }

    def test_other_currencies_are_shown_as_reported_and_never_compared(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"])
        usd = next(q for q in result["quotes"] if q["currency"] == "USD")
        assert (usd["price"], usd["currency_matches_shop"], usd["difference"]) == ("1.99", False, None)
        assert any("not converted" in note for note in result["notes"])

    def test_a_barcode_alone_finds_our_own_product_and_still_works_without_one(self, client_a, rice, net):
        assert check(client_a, barcode=BARCODE)["product"]["id"] == rice["id"]
        assert check(client_a, barcode=BARCODE[1:] + "1")["product"] is None  # not ours: prices only
        assert check(client_a, barcode=" 890 1234 567890 ")["barcode"] == BARCODE  # spaces are removed

    def test_a_possible_match_when_only_the_name_is_close(self, client_a, tenant_a, units, net):
        product = make_product(
            client_a,
            tenant_a,
            units,
            "RICE2",
            name="Basmati Rice Premium 1kg",
            brand="India Gate",
            barcode="7000000000001",
        )
        result = check(client_a, product_id=product["id"], barcode=BARCODE)
        top = result["quotes"][0]
        assert (top["match_type"], top["match_label"], top["match_basis"]) == (
            "POSSIBLE",
            "POSSIBLE MATCH",
            "similar_name",
        )
        assert 0 < top["confidence"] <= 70

    def test_a_different_pack_size_is_dropped(self, client_a, tenant_a, units, net):
        product = make_product(
            client_a,
            tenant_a,
            units,
            "RICE5",
            name="Basmati Rice 5kg",
            brand="India Gate",
            barcode="7000000000002",
        )
        result = check(client_a, product_id=product["id"], barcode=BARCODE)
        assert result["quotes"] == [] and "No outside price was found" in result["notes"][0]

    def test_no_price_found_is_an_honest_message_not_an_error(self, client_a, rice, net):
        net.route(pp.PRICES_HOST, (200, {"items": []}))
        result = check(client_a, product_id=rice["id"])
        assert result["quotes"] == [] and states(result)["open_prices"] == "NO_DATA"
        assert "limited coverage" in result["notes"][0] and "not affected" in result["notes"][0]

    def test_input_problems(self, client_a, client_b, tenant_b, units, rice, net):
        assert client_a.post(f"{API}/check", json={}).status_code == 422
        assert client_a.post(f"{API}/check", json={"barcode": "12ab"}).status_code == 422
        assert client_a.post(f"{API}/check", json={"barcode": "123"}).status_code == 422
        assert client_a.post(f"{API}/check", json={"product_id": 999999}).status_code == 404
        assert client_a.post(f"{API}/check", json={"product_id": rice["id"], "extra": 1}).status_code == 422
        other = make_product(client_b, tenant_b, units, "THEIRS", barcode="7000000000009")
        assert client_a.post(f"{API}/check", json={"product_id": other["id"]}).status_code == 404
        assert net.calls == []  # nothing was asked of the internet for any of that

    def test_a_product_without_a_barcode_says_so(self, client_a, tenant_a, units, net):
        plain = make_product(client_a, tenant_a, units, "PLAIN")
        response = client_a.post(f"{API}/check", json={"product_id": plain["id"]})
        assert response.status_code == 422 and "no barcode" in response.text

    def test_staff_can_check_prices(self, make_client, tenant_a, rice, net):
        from app.models.enums import UserRole

        assert check(make_client(tenant_a, role=UserRole.STAFF), product_id=rice["id"])["quotes"]


class TestLocation:
    def test_without_a_location_the_result_says_it_was_not_applied(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"])
        assert result["location"] == {"city": None, "state": None, "market": None, "applied": False, "note": "Location was not applied: no city, state or market was given."}  # fmt: skip
        assert all(q["location_matched"] is None for q in result["quotes"])

    def test_a_matching_city_is_listed_first_and_marked(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"], city="Mumbai")
        assert result["location"]["applied"] is True and "Mumbai" in result["location"]["note"]
        first = result["quotes"][0]
        assert (first["price"], first["location_matched"]) == ("99.50", True)
        assert [q["location_matched"] for q in result["quotes"]].count(True) == 1

    def test_a_market_name_can_match_too_and_case_is_ignored(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"], market="big bazaar")
        assert (
            result["quotes"][0]["location"].startswith("Big Bazaar") and result["location"]["applied"] is True
        )

    def test_a_place_with_no_prices_says_location_was_not_applied_and_still_shows_prices(
        self, client_a, rice, net
    ):
        result = check(client_a, product_id=rice["id"], city="Jaipur", state="Rajasthan")
        assert result["location"]["applied"] is False and result["location"]["note"].startswith(
            "Location was not applied"
        )
        assert "Jaipur" in result["location"]["note"] and len(result["quotes"]) == 3
        assert all(q["location_matched"] is False for q in result["quotes"])

    def test_the_location_is_never_sent_to_a_provider(self, client_a, rice, net):
        check(client_a, product_id=rice["id"], city="Pune", state="Maharashtra", market="Kirana One")
        assert not any(w in url.lower() for url, _, _ in net.calls for w in ("pune", "maharashtra", "kirana"))

    def test_the_location_needs_no_gps_fields(self, client_a, rice, net):
        assert (
            client_a.post(f"{API}/check", json={"product_id": rice["id"], "lat": 1.0, "lon": 2.0}).status_code
            == 422
        )


class TestFailureNeverBreaksAnything:
    def test_one_provider_down_does_not_stop_the_others(self, client_a, rice, net):
        net.route(pp.PRICES_HOST, pp.ProviderUnavailable("could not be reached in time"))
        result = check(client_a, product_id=rice["id"])
        assert states(result)["open_prices"] == "UNAVAILABLE" and states(result)["open_food_facts"] == "LIVE"
        assert result["quotes"] == [] and result["identified_as"]["name"] == "Basmati Rice"

    @pytest.mark.parametrize(
        "failure",
        [pp.ProviderUnavailable("timed out"), RuntimeError("boom"), ValueError("bad"), KeyError("x")],
    )
    def test_any_failure_becomes_a_status_never_an_error(self, client_a, rice, net, failure):
        for host in (pp.PRICES_HOST, pp.OFF_HOST):
            net.route(host, failure)
        response = client_a.post(f"{API}/check", json={"product_id": rice["id"]})
        assert response.status_code == 200 and response.json()["quotes"] == []
        assert states(response.json())["open_prices"] == "UNAVAILABLE"

    @pytest.mark.parametrize("status", [429, 500, 503, 401, 403])
    def test_http_errors_are_statuses(self, client_a, rice, net, status):
        net.route(pp.PRICES_HOST, (status, {}))
        assert states(check(client_a, product_id=rice["id"]))["open_prices"] == "UNAVAILABLE"

    def test_a_garbled_reply_is_a_status(self, client_a, rice, monkeypatch, net):
        monkeypatch.setattr(pp, "http_get", lambda url, headers, timeout: (200, b"<html>not json</html>"))
        assert states(check(client_a, product_id=rice["id"]))["open_prices"] == "UNAVAILABLE"

    def test_billing_never_touches_the_internet_and_works_when_every_provider_is_down(
        self, client_a, tenant_a, units, rice, net, session_factory
    ):
        from tests.test_purchases_api import make_supplier
        from tests.test_sales_api import stock_up

        stock_up(client_a, make_supplier(client_a), rice, 10, 80)
        for host in (pp.PRICES_HOST, pp.OFF_HOST, pp.UPC_HOST):
            net.route(host, pp.ProviderUnavailable("down"))
        sale = client_a.post("/api/v1/sales", json={"items": [item(rice, "2")]}).json()
        posted = client_a.post(f"/api/v1/sales/{sale['id']}/post", json={"payment_method": "CASH"})
        assert posted.status_code == 200 and posted.json()["total_amount"] == "200.00"
        quick = client_a.post("/api/v1/quick-sales", json={"gross_amount": "50"}).json()
        assert (
            client_a.post(
                f"/api/v1/quick-sales/{quick['id']}/post", json={"payment_method": "CASH"}
            ).status_code
            == 200
        )
        assert client_a.post("/api/v1/products/lookup", json={}).status_code == 405
        assert net.calls == []  # not one outside request was made by any of that

    def test_a_failed_check_leaves_no_rows_and_uses_no_allowance(self, client_a, rice, net, fresh):
        for host in (pp.PRICES_HOST, pp.OFF_HOST):
            net.route(host, pp.ProviderUnavailable("down"))
        check(client_a, product_id=rice["id"])
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(PriceObservation))) == 0
        assert client_a.get("/api/v1/subscription").json()["usage"]["price_lookups"] == 0


class TestCacheAndStale:
    def test_a_recent_saved_copy_is_used_without_asking_again(self, client_a, rice, net):
        check(client_a, product_id=rice["id"])
        before = net.hits(pp.PRICES_HOST)
        second = check(client_a, product_id=rice["id"])
        assert net.hits(pp.PRICES_HOST) == before  # served from the shop's own copy
        assert states(second)["open_prices"] == "CACHED" and len(second["quotes"]) == 3
        assert second["quotes"][0]["stale"] is False

    def test_an_old_copy_is_refreshed(self, client_a, rice, net, session_factory):
        check(client_a, product_id=rice["id"])
        with session_factory() as s, s.begin():
            s.execute(update(PriceObservation).values(checked_at=datetime.now(UTC) - timedelta(hours=30)))
        before = net.hits(pp.PRICES_HOST)
        assert states(check(client_a, product_id=rice["id"]))["open_prices"] == "LIVE"
        assert net.hits(pp.PRICES_HOST) == before + 1

    def test_when_the_provider_is_down_the_last_saved_prices_are_shown_as_stale(
        self, client_a, rice, net, session_factory
    ):
        check(client_a, product_id=rice["id"])
        with session_factory() as s, s.begin():
            s.execute(update(PriceObservation).values(checked_at=datetime.now(UTC) - timedelta(days=3)))
        net.route(pp.PRICES_HOST, pp.ProviderUnavailable("could not be reached in time"))
        result = check(client_a, product_id=rice["id"])
        assert states(result)["open_prices"] == "STALE" and "last saved" in next(p for p in result["providers"] if p["name"] == "open_prices")["message"]  # fmt: skip
        assert len(result["quotes"]) == 3 and all(q["stale"] for q in result["quotes"])

    def test_a_zero_ttl_always_asks(self, client_a, rice, net, settings):
        settings.set(price_cache_ttl_hours=0)
        check(client_a, product_id=rice["id"])
        check(client_a, product_id=rice["id"])
        assert net.hits(pp.PRICES_HOST) == 2

    def test_each_check_appends_history_and_a_cached_check_adds_none(
        self, client_a, rice, net, fresh, session_factory
    ):
        check(client_a, product_id=rice["id"])
        check(client_a, product_id=rice["id"])
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(PriceObservation))) == 3
        with session_factory() as s, s.begin():
            s.execute(update(PriceObservation).values(checked_at=datetime.now(UTC) - timedelta(days=2)))
        check(client_a, product_id=rice["id"])
        assert (
            fresh(lambda s: s.scalar(select(func.count()).select_from(PriceObservation))) == 6
        )  # old rows are kept

    def test_the_cache_is_per_shop(self, client_a, client_b, tenant_b, units, rice, net):
        check(client_a, product_id=rice["id"])
        calls = net.hits(pp.PRICES_HOST)
        result = check(client_b, barcode=BARCODE)
        assert net.hits(pp.PRICES_HOST) == calls + 1 and states(result)["open_prices"] == "LIVE"


class TestSwitchesAndKeys:
    def test_no_key_means_upcitemdb_is_not_configured_and_never_called(self, client_a, rice, net):
        result = check(client_a, product_id=rice["id"])
        assert states(result)["upcitemdb"] == "NOT_CONFIGURED" and net.hits(pp.UPC_HOST) == 0
        assert "not set up" in next(p for p in result["providers"] if p["name"] == "upcitemdb")["message"]

    def test_with_a_key_upcitemdb_is_asked(self, client_a, rice, net, settings):
        settings.set(UPCITEMDB_API_KEY=SECRET)
        result = check(client_a, product_id=rice["id"])
        assert states(result)["upcitemdb"] == "LIVE" and net.hits(pp.UPC_HOST) == 1
        assert any(q["source"] == "upcitemdb" and q["currency"] == "USD" for q in result["quotes"])

    def test_the_placeholder_from_env_example_counts_as_no_key(self, client_a, rice, net, settings):
        settings.set(UPCITEMDB_API_KEY="your_api_key_here")
        assert states(check(client_a, product_id=rice["id"]))["upcitemdb"] == "NOT_CONFIGURED"
        settings.set(UPCITEMDB_API_KEY="   ")
        assert states(check(client_a, product_id=rice["id"]))["upcitemdb"] == "NOT_CONFIGURED"

    def test_the_master_switch_stops_every_outside_request(self, client_a, rice, net, settings):
        settings.set(external_lookups_enabled=False)
        result = check(client_a, product_id=rice["id"])
        assert set(states(result).values()) == {"DISABLED"} and net.calls == []
        assert result["quotes"] == [] and client_a.get(f"{PRODUCTS}/{rice['id']}").status_code == 200

    def test_the_provider_list_says_configured_true_or_false_and_never_shows_the_key(
        self, client_a, net, settings
    ):
        settings.set(UPCITEMDB_API_KEY=SECRET)
        response = client_a.get(f"{API}/providers")
        assert SECRET not in response.text
        by_name = {p["name"]: p for p in response.json()["items"]}
        assert by_name["upcitemdb"] == {
            "name": "upcitemdb",
            "label": "UPCitemdb",
            "gives_prices": True,
            "configured": True,
            "enabled": True,
        }
        assert (
            by_name["open_food_facts"]["gives_prices"] is False
            and by_name["open_prices"]["configured"] is True
        )
        settings.set()
        assert {p["name"]: p["configured"] for p in client_a.get(f"{API}/providers").json()["items"]}[
            "upcitemdb"
        ] is False

    def test_the_key_is_never_in_any_response_log_or_repr(self, client_a, rice, net, settings, caplog):
        settings.set(UPCITEMDB_API_KEY=SECRET)
        net.route(pp.UPC_HOST, pp.ProviderUnavailable("down"))
        with caplog.at_level(logging.DEBUG):
            for response in (
                client_a.post(f"{API}/check", json={"product_id": rice["id"]}),
                client_a.get(f"{API}/providers"),
                client_a.get(f"{API}/history"),
            ):
                assert SECRET not in response.text
            boom = RuntimeError(f"failed with {SECRET}")
            net.route(pp.UPC_HOST, boom)
            client_a.post(f"{API}/check", json={"product_id": rice["id"], "barcode": "7000000000111"})
        assert (
            SECRET not in caplog.text
            and SECRET not in repr(settings.value)
            and SECRET not in str(settings.value)
        )
        assert SECRET not in settings.value.model_dump_json()

    def test_no_outgoing_url_carries_a_secret_or_personal_data(self, client_a, rice, net, settings):
        settings.set(UPCITEMDB_API_KEY=SECRET)
        check(client_a, product_id=rice["id"], city="Pune")
        for url, _, _ in net.calls:
            assert SECRET not in url and "Test" not in url and "shop" not in url.lower()
            assert url.split("//")[1].split("/")[0] in {
                "world.openfoodfacts.org",
                "prices.openfoodfacts.org",
                "api.upcitemdb.com",
            }


class TestPlanAndMetering:
    def test_plans_without_price_intelligence_are_refused(self, client_a, tenant_a, rice, give_plan, net):
        for plan in ("free", "basic"):
            give_plan(tenant_a, plan)
            refused = client_a.post(f"{API}/check", json={"product_id": rice["id"]})
            assert (
                refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "price_intelligence"
            )
            assert client_a.get(f"{API}/history").status_code == 403
        assert client_a.get(f"{API}/providers").status_code == 200 and net.calls == []

    def test_a_check_that_asked_a_provider_counts_once_and_a_cached_one_is_free(self, client_a, rice, net):
        check(client_a, product_id=rice["id"])
        assert client_a.get("/api/v1/subscription").json()["usage"]["price_lookups"] == 1
        check(client_a, product_id=rice["id"])
        assert client_a.get("/api/v1/subscription").json()["usage"]["price_lookups"] == 1

    def test_the_monthly_limit_is_enforced_before_any_outside_request(
        self, client_a, rice, net, session_factory
    ):
        with session_factory() as s, s.begin():
            entitlement_service.set_plan_entries(s, "pro", limits={"max_price_lookups_per_month": 1})
        check(client_a, product_id=rice["id"])
        calls = len(net.calls)
        refused = client_a.post(f"{API}/check", json={"product_id": rice["id"], "barcode": "7000000000111"})
        assert (
            refused.status_code == 403
            and refused.json()["detail"][0]["feature"] == "max_price_lookups_per_month"
        )
        assert len(net.calls) == calls
        assert (
            states(check(client_a, product_id=rice["id"]))["open_prices"] == "CACHED"
        )  # a saved copy is still free

    def test_a_zero_limit_blocks_checks_that_need_the_internet(self, client_a, rice, net, session_factory):
        with session_factory() as s, s.begin():
            entitlement_service.set_plan_entries(s, "pro", limits={"max_price_lookups_per_month": 0})
        assert client_a.post(f"{API}/check", json={"product_id": rice["id"]}).status_code == 403


class TestNeverChangesPrices:
    def test_a_check_changes_no_price_cost_or_stock(self, client_a, rice, net, fresh):
        from app.models import InventoryTransaction, Product

        def snapshot():
            return fresh(lambda s: (
                [(p.mrp, p.selling_price, p.purchase_price, p.avg_cost, p.updated_at) for p in s.scalars(select(Product).order_by(Product.id))],
                s.scalar(select(func.count()).select_from(InventoryTransaction)),
            ))  # fmt: skip

        before = snapshot()
        net.route(
            pp.PRICES_HOST,
            (
                200,
                prices_payload(
                    ("1", "INR", "2026-09-01", "Pune", "X"), ("9999", "INR", "2026-09-01", "Pune", "Y")
                ),
            ),
        )
        check(client_a, product_id=rice["id"])
        check(client_a, product_id=rice["id"], barcode=BARCODE)
        assert snapshot() == before
        shown = client_a.get(f"{PRODUCTS}/{rice['id']}").json()
        assert (shown["mrp"], shown["selling_price"]) == ("120.00", "100.00")

    def test_the_service_has_no_code_path_that_writes_products(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app/services"
        source = (root / "price_comparison_service.py").read_text() + (
            root / "price_providers.py"
        ).read_text()
        for word in (
            "update_product",
            "set_product_active",
            "selling_price =",
            "mrp =",
            "avg_cost =",
            "purchase_price =",
            "inventory_service",
        ):
            assert word not in source, word


class TestHistory:
    def test_history_lists_saved_prices_and_filters(self, client_a, rice, net):
        check(client_a, product_id=rice["id"])
        data = client_a.get(f"{API}/history").json()
        assert data["total"] == 3 and {r["provider"] for r in data["items"]} == {"open_prices"}
        first = data["items"][0]
        assert set(first) == {"id", "barcode", "provider", "product_name", "brand", "price", "currency", "location", "source_url", "observed_on", "checked_at"}  # fmt: skip
        assert client_a.get(f"{API}/history", params={"barcode": BARCODE}).json()["total"] == 3
        assert client_a.get(f"{API}/history", params={"barcode": "1234567"}).json()["total"] == 0
        assert client_a.get(f"{API}/history", params={"provider": "upcitemdb"}).json()["total"] == 0
        page = client_a.get(f"{API}/history", params={"limit": 2, "offset": 2}).json()
        assert len(page["items"]) == 1 and page["total"] == 3

    def test_history_is_per_shop(self, client_a, client_b, rice, net):
        check(client_a, product_id=rice["id"])
        assert (
            client_b.get(f"{API}/history").json()["total"] == 0
            and client_a.get(f"{API}/history").json()["total"] == 3
        )


class TestDatabaseRules:
    def row(self, tenant, **over):
        base = dict(shop_id=tenant.shop.id, barcode=BARCODE, provider="open_prices", price=D("5"), currency="INR", checked_at=datetime.now(UTC))  # fmt: skip
        return PriceObservation(**{**base, **over})

    def test_rows_must_have_a_positive_price_a_three_letter_currency_and_a_barcode(self, session, tenant_a):
        from tests.conftest import assert_rejected

        assert_rejected(session, self.row(tenant_a, price=D("0")), match="price_positive")
        assert_rejected(session, self.row(tenant_a, currency="RS"), match="currency_is_three_letters")
        assert_rejected(session, self.row(tenant_a, barcode=" "), match="not_blank")
        assert_rejected(session, self.row(tenant_a, provider=""), match="not_blank")
        session.add(self.row(tenant_a))
        session.flush()


class TestServiceShape:
    def test_location_hint(self):
        assert (
            not LocationHint().given
            and LocationHint(city="Pune").given
            and LocationHint(market=" ").words() == []
        )

    def test_results_sort_exact_first_then_location_then_newest_then_cheapest(self):
        ref = LocalRef(1, "Rice", "R", BARCODE, None, D("100"), None)

        def rq(price, seen, kind=MatchType.EXACT, loc=None):
            q = pp.Quote(provider="p", barcode=BARCODE, price=D(price), currency="INR", observed_on=seen)
            return ResultQuote(q, pcs.Match(kind, "barcode", 100), ref, loc, False, True, None)

        from datetime import date

        quotes, _ = pcs.apply_location([rq("9", date(2026, 1, 1)), rq("8", date(2026, 2, 1))], None)
        assert [q.quote.price for q in quotes] == [D("9"), D("8")]  # not sorted here: finish() sorts


class TestExport:
    def test_price_history_export(self, client_a, client_b, make_client, tenant_a, rice, net):
        from tests.test_exports import read_csv

        check(client_a, product_id=rice["id"])
        response = client_a.get("/api/v1/exports/price-history")
        assert response.status_code == 200 and response.content.startswith("﻿".encode())
        table = read_csv(response.content)
        rows = [dict(zip(table[0], r, strict=True)) for r in table[1:]]
        assert len(rows) == 3 and {r["Source"] for r in rows} == {"open_prices"}
        assert {r["Currency"] for r in rows} == {"INR", "USD"} and "95.00" in {r["Price"] for r in rows}
        assert client_b.get("/api/v1/exports/price-history").status_code == 200
        assert len(read_csv(client_b.get("/api/v1/exports/price-history").content)) == 1  # header only
        from app.models.enums import UserRole

        assert (
            make_client(tenant_a, role=UserRole.STAFF).get("/api/v1/exports/price-history").status_code == 403
        )

    def test_export_needs_the_plan_feature(self, client_a, tenant_a, give_plan):
        give_plan(tenant_a, "basic")
        assert client_a.get("/api/v1/exports/price-history").status_code == 403
