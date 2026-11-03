"""
Tests for restocking API endpoints.
"""
import pytest

from mock_data import submitted_orders


@pytest.fixture(autouse=True)
def clear_submitted_orders():
    """Submitted orders live in module-level memory, so reset them around every test."""
    submitted_orders.clear()
    yield
    submitted_orders.clear()


class TestRestockingRecommendations:
    """Test suite for the restocking recommendation endpoint."""

    def test_no_budget_returns_all_candidates(self, client):
        """Without a budget every item with a shortfall is returned."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 200

        data = response.json()
        assert data["budget"] is None
        assert len(data["items"]) > 0
        assert abs(data["total_cost"] - data["max_budget"]) < 0.01

        first = data["items"][0]
        for field in ["sku", "name", "category", "shortfall", "unit_cost", "line_cost", "lead_time_days"]:
            assert field in first

    def test_only_items_with_shortfall_are_recommended(self, client):
        """Items that are well stocked or have falling demand are excluded."""
        data = client.get("/api/restocking/recommendations").json()
        skus = [item["sku"] for item in data["items"]]

        assert "PSU-501" not in skus  # stock exceeds forecast
        assert "MTR-304" not in skus  # decreasing demand
        for item in data["items"]:
            assert item["shortfall"] > 0

    def test_items_sorted_by_shortfall_descending(self, client):
        """Candidates are ranked by largest shortfall first."""
        items = client.get("/api/restocking/recommendations").json()["items"]
        shortfalls = [item["shortfall"] for item in items]
        assert shortfalls == sorted(shortfalls, reverse=True)

    def test_line_cost_calculation(self, client):
        """Line cost equals shortfall times unit cost."""
        for item in client.get("/api/restocking/recommendations").json()["items"]:
            assert abs(item["line_cost"] - item["shortfall"] * item["unit_cost"]) < 0.01

    def test_zero_budget_returns_nothing(self, client):
        """A zero budget cannot afford any item."""
        data = client.get("/api/restocking/recommendations?budget=0").json()
        assert data["items"] == []
        assert data["total_cost"] == 0

    def test_total_never_exceeds_budget(self, client):
        """Selected items always fit within the budget."""
        max_budget = client.get("/api/restocking/recommendations").json()["max_budget"]

        for budget in [1000, 3000, 8000, max_budget / 2, max_budget]:
            data = client.get(f"/api/restocking/recommendations?budget={budget}").json()
            assert data["total_cost"] <= budget + 0.01

    def test_max_budget_buys_everything(self, client):
        """The derived maximum budget selects every candidate."""
        everything = client.get("/api/restocking/recommendations").json()
        data = client.get(f"/api/restocking/recommendations?budget={everything['max_budget']}").json()
        assert len(data["items"]) == len(everything["items"])

    def test_large_item_does_not_block_smaller_ones(self, client):
        """An item that does not fit is skipped and smaller affordable items are still added."""
        everything = client.get("/api/restocking/recommendations").json()["items"]
        largest = everything[0]
        # Budget just below the top-ranked item's cost: it must be skipped, but cheaper ones fit
        budget = largest["line_cost"] - 1
        data = client.get(f"/api/restocking/recommendations?budget={budget}").json()

        skus = [item["sku"] for item in data["items"]]
        assert largest["sku"] not in skus
        assert len(data["items"]) > 0

    def test_negative_budget_rejected(self, client):
        """A negative budget is a bad request."""
        response = client.get("/api/restocking/recommendations?budget=-5")
        assert response.status_code == 400
        assert "detail" in response.json()


class TestRestockingOrders:
    """Test suite for submitting and listing restocking orders."""

    def test_place_order_success(self, client):
        """A valid order is created with server-side pricing and lead time."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 100}]},
        )
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert order["items"][0]["sku"] == "WDG-001"
        assert order["items"][0]["quantity"] == 100
        assert abs(order["total_value"] - 100 * order["items"][0]["unit_price"]) < 0.01
        assert order["lead_time_days"] > 0
        assert "submitted_date" in order
        assert "expected_delivery" in order

    def test_lead_time_is_longest_item(self, client):
        """An order's lead time is the longest lead time among its items."""
        recs = client.get("/api/restocking/recommendations").json()["items"]
        longest = max(recs, key=lambda i: i["lead_time_days"])

        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": item["sku"], "quantity": 1} for item in recs]},
        )
        assert response.json()["lead_time_days"] == longest["lead_time_days"]

    def test_client_cannot_set_price(self, client):
        """Prices sent by the client are ignored in favour of server data."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 10, "unit_price": 0.01}]},
        )
        assert response.status_code == 201
        assert response.json()["items"][0]["unit_price"] > 0.01

    def test_empty_order_rejected(self, client):
        """An order with no items is a bad request."""
        response = client.post("/api/restocking/orders", json={"items": []})
        assert response.status_code == 400

    def test_unknown_sku_rejected(self, client):
        """An order for an unknown SKU returns 404."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "NOPE-999", "quantity": 5}]},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_duplicate_skus_rejected(self, client):
        """The same SKU may not appear twice in one order."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 1}, {"sku": "WDG-001", "quantity": 2}]},
        )
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"].lower()

    def test_budget_equal_to_total_cost_selects_same_items(self, client):
        """Feeding a response's total_cost back in as the budget keeps every item (float drift guard)."""
        for budget in [9555, 15000, 25000]:
            first = client.get(f"/api/restocking/recommendations?budget={budget}").json()
            again = client.get(f"/api/restocking/recommendations?budget={first['total_cost']}").json()
            assert len(again["items"]) == len(first["items"])

    @pytest.mark.parametrize("quantity", [0, -3])
    def test_non_positive_quantity_rejected(self, client, quantity):
        """Quantities must be positive."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": quantity}]},
        )
        assert response.status_code == 422

    def test_submitted_orders_listing(self, client):
        """Submitted orders are listed newest first."""
        assert client.get("/api/orders/submitted").json() == []

        client.post("/api/restocking/orders", json={"items": [{"sku": "WDG-001", "quantity": 1}]})
        client.post("/api/restocking/orders", json={"items": [{"sku": "GSK-203", "quantity": 1}]})

        data = client.get("/api/orders/submitted").json()
        assert len(data) == 2
        assert data[0]["items"][0]["sku"] == "GSK-203"
        assert data[0]["order_number"] != data[1]["order_number"]

    def test_submitted_orders_do_not_affect_main_orders(self, client):
        """Restocking orders stay out of the main orders list and dashboard totals."""
        before_orders = len(client.get("/api/orders").json())
        before_summary = client.get("/api/dashboard/summary").json()

        client.post("/api/restocking/orders", json={"items": [{"sku": "WDG-001", "quantity": 50}]})

        assert len(client.get("/api/orders").json()) == before_orders
        assert client.get("/api/dashboard/summary").json() == before_summary

    def test_submitted_route_not_captured_as_order_id(self, client):
        """/api/orders/submitted must not be treated as an order ID lookup."""
        response = client.get("/api/orders/submitted")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
