def test_other_orgs_asset_is_hidden(client_logged_in_as_acme_admin):
    r = client_logged_in_as_acme_admin.get("/api/assets/2")
    assert r.status_code == 404