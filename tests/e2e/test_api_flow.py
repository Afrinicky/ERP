"""The authenticated API surface, end to end."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nickland.interface.dependencies import SESSION_HEADER
from tests.e2e.conftest import API, sign_in

STAFF_PASSWORD = "Tamale4Harbour"


class TestSession:
    def test_the_current_actor_carries_its_permissions(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/session/current").json()
        assert body["username"] == "proprietor"
        assert body["is_administrator"] is True
        assert "journal.post" in body["permissions"]
        assert body["role_codes"] == ["owner_administrator"]

    def test_sign_out_invalidates_the_token(self, admin_client: TestClient) -> None:
        assert admin_client.delete(f"{API}/session").status_code == 204
        assert admin_client.get(f"{API}/session/current").status_code == 401

    def test_incorrect_credentials_are_refused_identically(
        self, provisioned_client: TestClient
    ) -> None:
        for payload in (
            {"username": "proprietor", "password": "Wrong9Password"},
            {"username": "ghost", "password": "Wrong9Password"},
        ):
            response = provisioned_client.post(f"{API}/session", json=payload)
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "Username or password is incorrect."

    def test_lockout_returns_423(self, provisioned_client: TestClient) -> None:
        for _ in range(5):
            provisioned_client.post(
                f"{API}/session",
                json={"username": "proprietor", "password": "Wrong9Password"},
            )
        response = provisioned_client.post(
            f"{API}/session", json={"username": "proprietor", "password": "Provision9Admin"}
        )
        assert response.status_code == 423
        assert response.json()["error"]["code"] == "account_locked"


class TestBusiness:
    def test_reads_the_business(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/business").json()
        assert body["name"] == "Nicholas Nickland Enterprise"
        assert body["base_currency"] == "GHS"
        assert body["currency_symbol"] == "GH₵"
        assert body["timezone"] == "Africa/Accra"

    def test_amends_the_business(self, admin_client: TestClient) -> None:
        response = admin_client.patch(
            f"{API}/business",
            json={"trading_name": "Nickland Meditech", "telephone": "+233300000000"},
        )
        assert response.status_code == 200
        assert response.json()["trading_name"] == "Nickland Meditech"

    def test_the_base_currency_cannot_be_changed(self, admin_client: TestClient) -> None:
        """All accounting is conducted in the base currency, per ADR-0006."""
        response = admin_client.patch(f"{API}/business", json={"base_currency": "USD"})
        assert response.status_code == 422

    def test_branches_and_warehouses(self, admin_client: TestClient) -> None:
        branches = admin_client.get(f"{API}/branches").json()
        assert len(branches) == 1 and branches[0]["is_primary"] is True

        created = admin_client.post(
            f"{API}/branches", json={"code": "tamale", "name": "Tamale depot"}
        )
        assert created.status_code == 201

        warehouse = admin_client.post(
            f"{API}/warehouses",
            json={
                "branch_id": created.json()["id"],
                "code": "tamale_store",
                "name": "Tamale store",
            },
        )
        assert warehouse.status_code == 201
        assert len(admin_client.get(f"{API}/warehouses").json()) == 2

    def test_a_duplicate_branch_code_is_refused(self, admin_client: TestClient) -> None:
        admin_client.post(f"{API}/branches", json={"code": "tamale", "name": "Tamale"})
        again = admin_client.post(f"{API}/branches", json={"code": "tamale", "name": "Other"})
        assert again.status_code == 409


class TestNumbering:
    def test_every_document_kind_has_a_sequence_with_a_preview(
        self, admin_client: TestClient
    ) -> None:
        sequences = admin_client.get(f"{API}/numbering").json()
        assert len(sequences) == 22
        quotation = next(item for item in sequences if item["document_kind"] == "quotation")
        assert quotation["preview"] == "QTN-2026-0001"
        assert quotation["document_label"] == "Quotation"

    def test_amending_a_pattern_changes_the_preview(self, admin_client: TestClient) -> None:
        response = admin_client.patch(
            f"{API}/numbering/quotation",
            json={"pattern": "{prefix}/{year2}{month}/{counter:5}", "prefix": "QT"},
        )
        assert response.status_code == 200
        assert response.json()["preview"] == "QT/2611/00001"

    def test_an_invalid_pattern_is_refused(self, admin_client: TestClient) -> None:
        response = admin_client.patch(
            f"{API}/numbering/quotation", json={"pattern": "{prefix}-{quarter}"}
        )
        assert response.status_code == 422

    def test_the_next_number_cannot_be_lowered(self, admin_client: TestClient) -> None:
        """PRD 6.7: a document number is never reused."""
        admin_client.patch(f"{API}/numbering/quotation", json={"next_value": 500})
        response = admin_client.patch(f"{API}/numbering/quotation", json={"next_value": 100})
        assert response.status_code == 409
        assert "never reused" in response.json()["error"]["message"]


class TestAppearance:
    def test_reports_the_chosen_preset(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/appearance").json()
        assert body["preset_code"] == "charcoal"
        assert body["colour"]["accent-primary"] == "#F7931E"
        assert body["colour"]["text-on-accent"] == "#252525"

    def test_lists_both_presets(self, admin_client: TestClient) -> None:
        presets = admin_client.get(f"{API}/appearance/presets").json()
        assert {item["code"] for item in presets} == {"charcoal", "navy"}

    def test_preview_measures_every_pairing(self, admin_client: TestClient) -> None:
        response = admin_client.post(
            f"{API}/appearance/preview",
            json={"preset_code": "charcoal", "overrides": {}},
        )
        body = response.json()
        assert body["passes"] is True
        assert len(body["audit"]) == 26
        assert all(line["ratio"] >= 1.0 for line in body["audit"])

    def test_preview_reports_a_failing_combination_without_refusing(
        self, admin_client: TestClient
    ) -> None:
        """The editor needs to show the failure while the administrator is still working."""
        response = admin_client.post(
            f"{API}/appearance/preview",
            json={"preset_code": "charcoal", "overrides": {"text-primary": "#EEEEEE"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["passes"] is False
        assert any(not line["passes"] for line in body["audit"])

    def test_applying_a_legible_theme_succeeds(self, admin_client: TestClient) -> None:
        response = admin_client.put(
            f"{API}/appearance",
            json={"preset_code": "navy", "overrides": {"accent-primary": "#1C4FB8"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["preset_code"] == "navy"
        assert body["colour"]["accent-primary"] == "#1C4FB8"
        assert body["colour"]["text-on-accent"] == "#FFFFFF"

    def test_applying_an_illegible_theme_is_refused(self, admin_client: TestClient) -> None:
        """PRD 28.2: a failing combination is refused, not merely flagged."""
        response = admin_client.put(
            f"{API}/appearance",
            json={"preset_code": "charcoal", "overrides": {"text-primary": "#EEEEEE"}},
        )
        assert response.status_code == 422
        assert "not legible enough" in response.json()["error"]["message"]

    def test_a_mid_tone_accent_is_refused_with_a_suggestion(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.put(
            f"{API}/appearance",
            json={"preset_code": "charcoal", "overrides": {"accent-primary": "#B8640A"}},
        )
        assert response.status_code == 422
        assert "mid-tone" in response.json()["error"]["message"]

    def test_reset_restores_the_preset(self, admin_client: TestClient) -> None:
        admin_client.put(
            f"{API}/appearance",
            json={"preset_code": "charcoal", "overrides": {"accent-primary": "#A85C00"}},
        )
        body = admin_client.post(f"{API}/appearance/reset").json()
        assert body["overrides"] == {}
        assert body["colour"]["accent-primary"] == "#F7931E"


class TestCustomFields:
    def test_defines_and_lists_a_field(self, admin_client: TestClient) -> None:
        response = admin_client.post(
            f"{API}/custom-fields",
            json={
                "target": "product",
                "code": "manufacturer",
                "label": "Manufacturer",
                "field_type": "text",
                "show_on_documents": True,
            },
        )
        assert response.status_code == 201
        listed = admin_client.get(f"{API}/custom-fields/product").json()
        assert [item["code"] for item in listed] == ["manufacturer"]

    def test_a_choice_field_requires_options(self, admin_client: TestClient) -> None:
        response = admin_client.post(
            f"{API}/custom-fields",
            json={
                "target": "product",
                "code": "storage",
                "label": "Storage",
                "field_type": "choice",
            },
        )
        assert response.status_code == 422
        assert "at least one option" in response.json()["error"]["message"]

    def test_a_duplicate_code_is_refused(self, admin_client: TestClient) -> None:
        payload = {
            "target": "customer",
            "code": "sector",
            "label": "Sector",
            "field_type": "text",
        }
        assert admin_client.post(f"{API}/custom-fields", json=payload).status_code == 201
        assert admin_client.post(f"{API}/custom-fields", json=payload).status_code == 409

    def test_withdrawing_requires_a_reason(self, admin_client: TestClient) -> None:
        created = admin_client.post(
            f"{API}/custom-fields",
            json={
                "target": "product",
                "code": "model_code",
                "label": "Model",
                "field_type": "text",
            },
        ).json()
        refused = admin_client.post(
            f"{API}/custom-fields/{created['id']}/withdraw", json={"reason": ""}
        )
        assert refused.status_code == 422
        accepted = admin_client.post(
            f"{API}/custom-fields/{created['id']}/withdraw",
            json={"reason": "Superseded by the catalogue attribute"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["is_active"] is False


class TestUsersAndPermissions:
    def test_creates_a_user_whose_permissions_match_its_role(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.post(
            f"{API}/users",
            json={
                "username": "cashier1",
                "full_name": "A. Cashier",
                "password": STAFF_PASSWORD,
                "role_codes": ["cashier"],
            },
        )
        assert response.status_code == 201
        assert response.json()["role_codes"] == ["cashier"]

    def test_a_cashier_cannot_reach_administration(self, admin_client: TestClient) -> None:
        """PRD 10: the service refuses, whatever the interface displays."""
        admin_client.post(
            f"{API}/users",
            json={
                "username": "cashier1",
                "full_name": "A. Cashier",
                "password": STAFF_PASSWORD,
                "role_codes": ["cashier"],
            },
        )
        token = sign_in(admin_client, "cashier1", STAFF_PASSWORD)
        admin_client.headers[SESSION_HEADER] = token

        refused = admin_client.post(
            f"{API}/users",
            json={
                "username": "accomplice",
                "full_name": "Accomplice",
                "password": STAFF_PASSWORD,
                "role_codes": ["owner_administrator"],
            },
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["detail"]["permission"] == "user.manage"

    def test_a_cashier_cannot_read_the_audit_trail(self, admin_client: TestClient) -> None:
        admin_client.post(
            f"{API}/users",
            json={
                "username": "cashier1",
                "full_name": "A. Cashier",
                "password": STAFF_PASSWORD,
                "role_codes": ["cashier"],
            },
        )
        admin_client.headers[SESSION_HEADER] = sign_in(
            admin_client, "cashier1", STAFF_PASSWORD
        )
        assert admin_client.get(f"{API}/audit").status_code == 403

    def test_a_cashier_cannot_amend_the_theme(self, admin_client: TestClient) -> None:
        admin_client.post(
            f"{API}/users",
            json={
                "username": "cashier1",
                "full_name": "A. Cashier",
                "password": STAFF_PASSWORD,
                "role_codes": ["cashier"],
            },
        )
        admin_client.headers[SESSION_HEADER] = sign_in(
            admin_client, "cashier1", STAFF_PASSWORD
        )
        response = admin_client.put(
            f"{API}/appearance", json={"preset_code": "navy", "overrides": {}}
        )
        assert response.status_code == 403

    def test_the_permission_registry_is_published(self, admin_client: TestClient) -> None:
        permissions = admin_client.get(f"{API}/permissions").json()
        assert len(permissions) == 97
        assert any(item["sensitive"] for item in permissions)
        costing = next(item for item in permissions if item["code"] == "costing.view")
        assert costing["sensitive"] is True

    def test_the_eight_roles_are_listed(self, admin_client: TestClient) -> None:
        roles = admin_client.get(f"{API}/roles").json()
        assert len(roles) == 8
        auditor = next(item for item in roles if item["code"] == "auditor")
        assert auditor["is_read_only"] is True

    def test_deactivation_requires_a_reason(self, admin_client: TestClient) -> None:
        created = admin_client.post(
            f"{API}/users",
            json={
                "username": "cashier1",
                "full_name": "A. Cashier",
                "password": STAFF_PASSWORD,
                "role_codes": ["cashier"],
            },
        ).json()
        refused = admin_client.post(
            f"{API}/users/{created['id']}/active", json={"active": False, "reason": ""}
        )
        assert refused.status_code == 422

    def test_the_last_administrator_cannot_lose_the_role(
        self, admin_client: TestClient
    ) -> None:
        current = admin_client.get(f"{API}/session/current").json()
        response = admin_client.delete(
            f"{API}/users/{current['user_id']}/roles/owner_administrator"
        )
        assert response.status_code == 409
        assert "only active administrator" in response.json()["error"]["message"]


class TestAudit:
    def test_provisioning_and_sign_in_are_recorded(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/audit", params={"limit": 100}).json()
        actions = {item["action"] for item in body["items"]}
        assert {"business_created", "setup_completed", "user_created", "sign_in"} <= actions

    def test_records_the_fields_that_changed(self, admin_client: TestClient) -> None:
        admin_client.patch(f"{API}/business", json={"trading_name": "Nickland Meditech"})
        body = admin_client.get(
            f"{API}/audit", params={"action": "business_amended"}
        ).json()
        assert body["total"] == 1
        entry = body["items"][0]
        assert entry["after_values"]["trading_name"] == "Nickland Meditech"
        assert "name" not in entry["after_values"]

    def test_a_reason_is_retained(self, admin_client: TestClient) -> None:
        created = admin_client.post(
            f"{API}/custom-fields",
            json={
                "target": "product",
                "code": "model_code",
                "label": "Model",
                "field_type": "text",
            },
        ).json()
        admin_client.post(
            f"{API}/custom-fields/{created['id']}/withdraw",
            json={"reason": "Superseded by the catalogue attribute"},
        )
        body = admin_client.get(
            f"{API}/audit", params={"action": "custom_field_withdrawn"}
        ).json()
        assert body["items"][0]["reason"] == "Superseded by the catalogue attribute"

    def test_a_failed_sign_in_is_recorded_without_the_password(
        self, provisioned_client: TestClient
    ) -> None:
        provisioned_client.post(
            f"{API}/session", json={"username": "proprietor", "password": "Wrong9Password"}
        )
        provisioned_client.headers[SESSION_HEADER] = sign_in(
            provisioned_client, "proprietor", "Provision9Admin"
        )
        body = provisioned_client.get(
            f"{API}/audit", params={"action": "sign_in_failed"}
        ).json()
        assert body["total"] == 1
        assert "Wrong9Password" not in str(body["items"][0])


class TestBackupAndHealth:
    @pytest.fixture(autouse=True)
    def _standalone_only(self, sqlite_only: None) -> None:
        """Backup here is the standalone SQLite path."""

    def test_takes_and_verifies_a_backup(self, admin_client: TestClient) -> None:
        response = admin_client.post(f"{API}/backups", json={"include_attachments": True})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "verified"
        assert body["verified_at"] is not None
        assert body["size_bytes"] > 0
        assert "Integrity check passed" in body["verification_note"]

    def test_the_backup_appears_in_the_history(self, admin_client: TestClient) -> None:
        admin_client.post(f"{API}/backups", json={})
        history = admin_client.get(f"{API}/backups").json()
        assert len(history) == 1

    def test_retention_prunes_older_archives(self, admin_client: TestClient) -> None:
        """Three are retained, per the test settings."""
        for _ in range(5):
            admin_client.post(f"{API}/backups", json={})
        history = admin_client.get(f"{API}/backups").json()
        retained = [item for item in history if item["storage_key"]]
        assert len(retained) == 3

    def test_restore_requires_confirmation(self, admin_client: TestClient) -> None:
        archive = admin_client.post(f"{API}/backups", json={}).json()["storage_key"]
        refused = admin_client.post(
            f"{API}/backups/restore",
            json={"archive_name": archive, "reason": "Drill", "confirm": False},
        )
        assert refused.status_code == 409
        assert "Confirm the action" in refused.json()["error"]["message"]

    def test_restore_is_staged_rather_than_applied_immediately(
        self, admin_client: TestClient
    ) -> None:
        archive = admin_client.post(f"{API}/backups", json={}).json()["storage_key"]
        response = admin_client.post(
            f"{API}/backups/restore",
            json={"archive_name": archive, "reason": "Restore drill", "confirm": True},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "staged"

    def test_restoring_an_unknown_archive_is_not_found(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.post(
            f"{API}/backups/restore",
            json={"archive_name": "nothing.zip", "reason": "Drill", "confirm": True},
        )
        assert response.status_code == 404

    def test_the_detailed_report_includes_the_backup_position(
        self, admin_client: TestClient
    ) -> None:
        before = admin_client.get(f"{API}/health/detail").json()
        backup_check = next(item for item in before["checks"] if item["name"] == "Backup")
        assert backup_check["state"] == "degraded"
        assert "No verified backup" in backup_check["detail"]

        admin_client.post(f"{API}/backups", json={})
        after = admin_client.get(f"{API}/health/detail").json()
        backup_check = next(item for item in after["checks"] if item["name"] == "Backup")
        assert backup_check["state"] == "ok"

    def test_offline_is_reported_as_unavailable_not_failed(
        self, admin_client: TestClient
    ) -> None:
        """PRD 9.4: absence of a network is the expected standalone condition."""
        body = admin_client.get(f"{API}/health/detail").json()
        network = next(item for item in body["checks"] if item["name"] == "Network")
        assert network["state"] in ("ok", "unavailable")
        assert body["state"] != "failed"
