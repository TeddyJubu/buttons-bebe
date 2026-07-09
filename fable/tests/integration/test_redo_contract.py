"""Integration: Redo emulator contract (STRATEGY §2.2)."""

STORE = "bb-store-1"
BEARER = {"Authorization": "Bearer test-redo-key"}


def test_bearer_auth_required(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns")
    assert r.status_code == 401
    assert r.json()["error"] == "Unauthorized"


def test_bad_bearer_is_401(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_list_returns_with_meta(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns", headers=BEARER)
    assert r.status_code == 200
    body = r.json()
    assert len(body["returns"]) == 8
    assert body["meta"]["total_resources"] == 8
    assert body["meta"]["store"] == STORE


def test_filter_by_order_name(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns",
                     params={"order_name": "#BB1022"}, headers=BEARER)
    items = r.json()["returns"]
    assert len(items) == 1
    assert items[0]["order_name"] == "#BB1022"
    assert items[0]["status"] == "approved"


def test_filter_by_order_name_without_hash(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns",
                     params={"order_name": "BB1022"}, headers=BEARER)
    assert len(r.json()["returns"]) == 1


def test_limit_param(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns",
                     params={"limit": 3}, headers=BEARER)
    assert len(r.json()["returns"]) == 3


def test_get_single_return(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns/ret_1001", headers=BEARER)
    assert r.status_code == 200
    ret = r.json()["return"]
    assert ret["id"] == "ret_1001"
    assert ret["order_name"] == "#BB1022"
    assert isinstance(ret["items"], list)


def test_unknown_return_is_404(env):
    r = env.redo.get(f"/v2.2/stores/{STORE}/returns/ret_nope", headers=BEARER)
    assert r.status_code == 404


def test_return_shape_fields(env):
    ret = env.redo.get(f"/v2.2/stores/{STORE}/returns/ret_1003", headers=BEARER).json()["return"]
    for field in ("id", "order_name", "status", "items", "created_at", "refund_amount"):
        assert field in ret
    assert ret["status"] in ("requested", "approved", "in_transit", "refunded", "rejected")


def test_emulator_state(env):
    st = env.redo.get("/emulator/state").json()
    assert st["returns"] == 8
