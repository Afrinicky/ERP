"""The authenticated API surface, end to end."""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from nickland.domain.money import ZERO
from nickland.domain.organisation import DocumentKind
from nickland.interface.dependencies import SESSION_HEADER
from tests.e2e.conftest import API, sign_in

STAFF_PASSWORD = "Tamale4Harbour"


class TestSession:
    def test_the_current_actor_carries_its_permissions(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/session/current").json()
        assert body["username"] == "proprietor"
        assert body["is_administrator"] is True
        assert "journal.post" in body["permissions"]
        assert body["role_codes"] == ["administrator"]

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
        assert {item["document_kind"] for item in sequences} == {
            kind.value for kind in DocumentKind
        }
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

    def test_lists_every_preset(self, admin_client: TestClient) -> None:
        presets = admin_client.get(f"{API}/appearance/presets").json()
        assert {item["code"] for item in presets} == {
            "nickland",
            "charcoal",
            "harbour",
            "navy",
        }

    def test_preview_measures_every_pairing(self, admin_client: TestClient) -> None:
        response = admin_client.post(
            f"{API}/appearance/preview",
            json={"preset_code": "charcoal", "overrides": {}},
        )
        body = response.json()
        assert body["passes"] is True
        declared = json.loads(Path("tokens/pairings.json").read_text(encoding="utf-8"))
        assert len(body["audit"]) == len(declared["pairings"])
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

    def test_a_mid_tone_accent_is_refused_with_a_suggestion(self, admin_client: TestClient) -> None:
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
                "username": "seller1",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        )
        assert response.status_code == 201
        assert response.json()["role_codes"] == ["sales_person"]

    def test_a_sales_person_cannot_reach_administration(self, admin_client: TestClient) -> None:
        """PRD 10: the service refuses, whatever the interface displays."""
        admin_client.post(
            f"{API}/users",
            json={
                "username": "seller1",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        )
        token = sign_in(admin_client, "seller1", STAFF_PASSWORD)
        admin_client.headers[SESSION_HEADER] = token

        refused = admin_client.post(
            f"{API}/users",
            json={
                "username": "accomplice",
                "full_name": "Accomplice",
                "password": STAFF_PASSWORD,
                "role_codes": ["administrator"],
            },
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["detail"]["permission"] == "user.manage"

    def test_a_sales_person_cannot_read_the_audit_trail(self, admin_client: TestClient) -> None:
        admin_client.post(
            f"{API}/users",
            json={
                "username": "seller1",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        )
        admin_client.headers[SESSION_HEADER] = sign_in(admin_client, "seller1", STAFF_PASSWORD)
        assert admin_client.get(f"{API}/audit").status_code == 403

    def test_a_sales_person_cannot_amend_the_theme(self, admin_client: TestClient) -> None:
        admin_client.post(
            f"{API}/users",
            json={
                "username": "seller1",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        )
        admin_client.headers[SESSION_HEADER] = sign_in(admin_client, "seller1", STAFF_PASSWORD)
        response = admin_client.put(
            f"{API}/appearance", json={"preset_code": "navy", "overrides": {}}
        )
        assert response.status_code == 403

    def test_the_permission_registry_is_published(self, admin_client: TestClient) -> None:
        permissions = admin_client.get(f"{API}/permissions").json()
        assert len(permissions) == 103
        assert any(item["sensitive"] for item in permissions)
        costing = next(item for item in permissions if item["code"] == "costing.view")
        assert costing["sensitive"] is True

    def test_the_seeded_roles_are_listed(self, admin_client: TestClient) -> None:
        roles = admin_client.get(f"{API}/roles").json()
        assert {item["code"] for item in roles} == {"administrator", "sales_person"}
        administrator = next(item for item in roles if item["code"] == "administrator")
        assert administrator["is_administrator"] is True

    def test_deactivation_requires_a_reason(self, admin_client: TestClient) -> None:
        created = admin_client.post(
            f"{API}/users",
            json={
                "username": "seller1",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        ).json()
        refused = admin_client.post(
            f"{API}/users/{created['id']}/active", json={"active": False, "reason": ""}
        )
        assert refused.status_code == 422

    def test_the_last_administrator_cannot_lose_the_role(self, admin_client: TestClient) -> None:
        current = admin_client.get(f"{API}/session/current").json()
        response = admin_client.delete(f"{API}/users/{current['user_id']}/roles/administrator")
        assert response.status_code == 409
        assert "only active administrator" in response.json()["error"]["message"]


class TestAudit:
    def test_provisioning_and_sign_in_are_recorded(self, admin_client: TestClient) -> None:
        body = admin_client.get(f"{API}/audit", params={"limit": 100}).json()
        actions = {item["action"] for item in body["items"]}
        assert {"business_created", "setup_completed", "user_created", "sign_in"} <= actions

    def test_records_the_fields_that_changed(self, admin_client: TestClient) -> None:
        admin_client.patch(f"{API}/business", json={"trading_name": "Nickland Meditech"})
        body = admin_client.get(f"{API}/audit", params={"action": "business_amended"}).json()
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
        body = admin_client.get(f"{API}/audit", params={"action": "custom_field_withdrawn"}).json()
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
        body = provisioned_client.get(f"{API}/audit", params={"action": "sign_in_failed"}).json()
        assert body["total"] == 1
        assert "Wrong9Password" not in str(body["items"][0])


class TestBackupAndHealth:
    def test_takes_and_verifies_a_backup(self, admin_client: TestClient) -> None:
        response = admin_client.post(f"{API}/backups", json={"include_attachments": True})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "verified"
        assert body["verified_at"] is not None
        assert body["size_bytes"] > 0
        assert (
            "Integrity check passed" in body["verification_note"]
            or "Dump readable" in body["verification_note"]
        )

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

    def test_restoring_an_unknown_archive_is_not_found(self, admin_client: TestClient) -> None:
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

    def test_offline_is_reported_as_unavailable_not_failed(self, admin_client: TestClient) -> None:
        """PRD 9.4: absence of a network is the expected standalone condition."""
        body = admin_client.get(f"{API}/health/detail").json()
        network = next(item for item in body["checks"] if item["name"] == "Network")
        assert network["state"] in ("ok", "unavailable")
        assert body["state"] != "failed"


class TestThemePreviewMatchesSave:
    """The preview must measure the same theme that saving would store.

    Dependent tokens are derived on save; a preview that skipped the derivation would
    report failures that saving does not produce, and would show the administrator a
    palette they will never get.
    """

    def test_preview_derives_the_same_tokens_as_saving(self, admin_client: TestClient) -> None:
        overrides = {"accent-primary": "#A85C00"}
        preview = admin_client.post(
            f"{API}/appearance/preview",
            json={"preset_code": "charcoal", "overrides": overrides},
        ).json()
        saved = admin_client.put(
            f"{API}/appearance", json={"preset_code": "charcoal", "overrides": overrides}
        )
        assert saved.status_code == 200, saved.text
        assert preview["passes"] is True
        assert preview["theme"]["colour"] == saved.json()["colour"]

    def test_a_preview_that_passes_can_always_be_saved(self, admin_client: TestClient) -> None:
        for accent in ("#A85C00", "#1C4FB8", "#7D5BA6", "#F7931E"):
            overrides = {"accent-primary": accent}
            preview = admin_client.post(
                f"{API}/appearance/preview",
                json={"preset_code": "charcoal", "overrides": overrides},
            ).json()
            saved = admin_client.put(
                f"{API}/appearance",
                json={"preset_code": "charcoal", "overrides": overrides},
            )
            assert preview["passes"] == (saved.status_code == 200), (
                f"{accent}: preview said passes={preview['passes']} "
                f"but saving returned {saved.status_code}"
            )

    def test_a_partially_typed_colour_does_not_abort_the_preview(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.post(
            f"{API}/appearance/preview",
            json={"preset_code": "charcoal", "overrides": {"accent-primary": "#A8"}},
        )
        assert response.status_code == 200
        assert response.json()["passes"] is True


class TestSalesOverHttp:
    """The whole Phase 5 sequence through the published API, not the service objects.

    The costing endpoint sits behind its own permission, which is the confidentiality
    boundary of PRD 14 expressed in the contract: a client that never calls it has
    never been sent a margin.
    """

    def _world(self, admin_client: TestClient) -> dict[str, str]:
        customer = admin_client.post(
            f"{API}/parties", json={"name": "Banhart Hospital", "is_customer": True}
        ).json()
        supplier = admin_client.post(
            f"{API}/parties", json={"name": "Gamma Labs", "is_supplier": True}
        ).json()
        unit = admin_client.get(f"{API}/units").json()[0]
        product = admin_client.post(
            f"{API}/products",
            json={
                "sku": "RGT-002",
                "name": "Reagent pack",
                "stock_unit_id": unit["id"],
                "weight_kg": "2",
            },
        ).json()
        return {
            "customer": customer["id"],
            "supplier": supplier["id"],
            "product": product["id"],
        }

    def test_a_request_is_sourced_compared_quoted_and_issued(
        self, admin_client: TestClient
    ) -> None:
        world = self._world(admin_client)

        created = admin_client.post(
            f"{API}/sourcing/requests",
            json={
                "customer_id": world["customer"],
                "customer_reference": "BH/2026/114",
                "lines": [
                    {
                        "description": "Reagent pack",
                        "quantity": "40",
                        "product_id": world["product"],
                    }
                ],
            },
        )
        assert created.status_code == 200, created.text
        request = created.json()
        assert request["number"].startswith("RFQ-")

        moved = admin_client.post(
            f"{API}/sourcing/requests/{request['id']}/state", json={"state": "open"}
        )
        assert moved.status_code == 200, moved.text

        recorded = admin_client.post(
            f"{API}/sourcing/supplier-quotations",
            json={
                "supplier_id": world["supplier"],
                "reference": "GL-Q1",
                "request_id": request["id"],
                "freight_amount": "400",
                "lines": [
                    {
                        "description": "Reagent pack",
                        "quantity_offered": "40",
                        "unit_cost": "172",
                        "request_line_id": request["lines"][0]["id"],
                        "product_id": world["product"],
                        "lead_time_days": 14,
                    }
                ],
            },
        )
        assert recorded.status_code == 200, recorded.text

        comparison = admin_client.get(f"{API}/sourcing/requests/{request['id']}/comparison").json()
        assert len(comparison) == 1
        offer = comparison[0]["offers"][0]
        assert comparison[0]["best_offer_id"] == offer["supplier_quotation_line_id"]
        assert Decimal(offer["comparable_unit_cost"]) > Decimal(offer["net_unit_cost"])

        quoted = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": world["customer"],
                "request_id": request["id"],
                "lines": [
                    {
                        "description": "Reagent pack",
                        "quantity": "40",
                        "product_id": world["product"],
                        "sources": [
                            {
                                "kind": "supplier",
                                "quantity": "40",
                                "supplier_quotation_line_id": offer["supplier_quotation_line_id"],
                            }
                        ],
                    }
                ],
                "expenses": [{"category": "clearing", "amount": "450", "method": "by_value"}],
            },
        )
        assert quoted.status_code == 200, quoted.text
        quotation = quoted.json()
        assert quotation["number"].startswith("QTN-")
        assert Decimal(quotation["total_payable"]) > ZERO
        # Nothing confidential crosses the quotation contract.
        for forbidden in ("landed", "margin", "commission", "supplier_cost"):
            assert forbidden not in quoted.text

        costing = admin_client.get(f"{API}/quotations/{quotation['id']}/costing")
        assert costing.status_code == 200, costing.text
        assert Decimal(costing.json()["landed_cost_total"]) > ZERO
        assert costing.json()["lines"][0]["steps"]

        issued = admin_client.post(
            f"{API}/quotations/{quotation['id']}/issue", json={"valid_until": "2026-12-31"}
        )
        assert issued.status_code == 200, issued.text
        assert issued.json()["state"] == "issued"

        snapshots = admin_client.get(f"{API}/quotations/{quotation['id']}/snapshots").json()
        assert len(snapshots) == 1
        assert snapshots[0]["content_hash"]

    def test_a_sales_person_sees_the_offer_and_not_the_costing(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        """The shipped sales role holds ``quotation.view`` and not ``costing.view``.

        So the boundary is tested as it ships, rather than against a role invented
        for the test: a sales officer can read the offer they are selling and cannot
        read the margin inside it.
        """
        world = self._world(admin_client)
        quotation = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": world["customer"],
                "lines": [{"description": "Reagent pack", "quantity": "1", "unit_cost": "100"}],
            },
        ).json()

        created = admin_client.post(
            f"{API}/users",
            json={
                "username": "seller",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
                "must_change_password": False,
            },
        )
        assert created.status_code == 201, created.text

        signed_in = client.post(
            f"{API}/session", json={"username": "seller", "password": STAFF_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        client.headers[SESSION_HEADER] = signed_in.json()["token"]

        offer = client.get(f"{API}/quotations/{quotation['id']}")
        assert offer.status_code == 200, offer.text
        assert client.get(f"{API}/quotations/{quotation['id']}/costing").status_code == 403
        assert client.get(f"{API}/quotations/{quotation['id']}/snapshots").status_code == 403

    def test_the_instalment_schedule_is_published(self, admin_client: TestClient) -> None:
        world = self._world(admin_client)
        plan = admin_client.get(f"{API}/quotations/policy/plans").json()[0]
        quotation = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": world["customer"],
                "payment_plan": "instalment",
                "instalment_plan_id": plan["id"],
                "instalment_months": 6,
                "first_instalment_on": "2026-12-01",
                "lines": [{"description": "Reagent pack", "quantity": "40", "unit_cost": "172"}],
            },
        )
        assert quotation.status_code == 200, quotation.text
        issued = admin_client.post(f"{API}/quotations/{quotation.json()['id']}/issue", json={})
        assert issued.status_code == 200, issued.text

        schedule = admin_client.get(f"{API}/quotations/{quotation.json()['id']}/schedule").json()
        assert schedule["duration_months"] == 6
        assert schedule["first_due_on"] == "2026-12-01"
        assert schedule["final_due_on"] == "2027-05-01"
        assert Decimal(schedule["finance_charge"]) > ZERO
        assert len(schedule["instalments"]) == 7


class TestDocumentsOverHttp:
    """Phase 6 through the published API: the designer, issue, reprint and receipt."""

    def _quotation(self, admin_client: TestClient) -> dict[str, object]:
        customer = admin_client.post(
            f"{API}/parties",
            json={
                "name": "Banhart Hospital",
                "is_customer": True,
                "telephone": "+233 30 222 3344",
                "tax_identifier": "C0009988776",
            },
        ).json()
        created = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": customer["id"],
                "customer_reference": "BH/2026/114",
                "lines": [
                    {"description": "Reagent pack", "quantity": "40", "unit_cost": "172"},
                    {"description": "Nitrile gloves, medium", "quantity": "12", "unit_cost": "85"},
                ],
            },
        )
        assert created.status_code == 200, created.text
        quotation = created.json()
        issued = admin_client.post(f"{API}/quotations/{quotation['id']}/issue", json={})
        assert issued.status_code == 200, issued.text
        return issued.json()

    def test_a_quotation_becomes_a_pro_forma_that_reprints_identically(
        self, admin_client: TestClient
    ) -> None:
        quotation = self._quotation(admin_client)

        created = admin_client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "pro_forma",
                "source_type": "quotation",
                "source_id": quotation["id"],
            },
        )
        assert created.status_code == 201, created.text
        document = created.json()
        assert document["number"].startswith("PFI-")
        assert document["media_type"] == "application/pdf"
        assert document["byte_count"] > 0
        assert document["template_version"] == 1

        listed = admin_client.get(f"{API}/documents/issued").json()
        assert [item["id"] for item in listed] == [document["id"]]

        first = admin_client.get(f"{API}/documents/issued/{document['id']}/file")
        assert first.status_code == 200, first.text
        assert first.headers["content-type"].startswith("application/pdf")
        assert first.content.startswith(b"%PDF")
        again = admin_client.get(f"{API}/documents/issued/{document['id']}/file")
        assert again.content == first.content

    def test_a_draft_quotation_cannot_be_put_on_paper(self, admin_client: TestClient) -> None:
        customer = admin_client.post(
            f"{API}/parties", json={"name": "Banhart Hospital", "is_customer": True}
        ).json()
        draft = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": customer["id"],
                "lines": [{"description": "Reagent pack", "quantity": "1", "unit_cost": "100"}],
            },
        ).json()
        refused = admin_client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "pro_forma",
                "source_type": "quotation",
                "source_id": draft["id"],
            },
        )
        assert refused.status_code == 409, refused.text

    def test_a_source_this_release_cannot_issue_from_is_refused(
        self, admin_client: TestClient
    ) -> None:
        quotation = self._quotation(admin_client)
        refused = admin_client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "pro_forma",
                "source_type": "sales_order",
                "source_id": quotation["id"],
            },
        )
        assert refused.status_code == 422, refused.text

    def test_proof_of_receipt_is_recorded_and_read_back(self, admin_client: TestClient) -> None:
        quotation = self._quotation(admin_client)
        document = admin_client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "delivery_note",
                "source_type": "quotation",
                "source_id": quotation["id"],
            },
        ).json()

        recorded = admin_client.post(
            f"{API}/documents/issued/{document['id']}/acknowledgements",
            json={
                "recipient_name": "A. Mensah",
                "recipient_role": "Stores officer",
                "location": "Korle Bu, Accra",
                "comments": "Two cartons short; balance to follow.",
                "evidence_kind": "signature",
                "evidence_base64": base64.b64encode(b"-- a signature --").decode("ascii"),
                "media_type": "image/png",
            },
        )
        assert recorded.status_code == 201, recorded.text
        receipt = recorded.json()
        assert receipt["recipient_name"] == "A. Mensah"
        assert receipt["evidence_hash"] is not None
        assert "evidence_base64" not in receipt

        read_back = admin_client.get(
            f"{API}/documents/issued/{document['id']}/acknowledgements"
        ).json()
        assert [item["id"] for item in read_back] == [receipt["id"]]

    def test_evidence_that_is_not_base64_is_refused(self, admin_client: TestClient) -> None:
        quotation = self._quotation(admin_client)
        document = admin_client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "delivery_note",
                "source_type": "quotation",
                "source_id": quotation["id"],
            },
        ).json()
        refused = admin_client.post(
            f"{API}/documents/issued/{document['id']}/acknowledgements",
            json={"recipient_name": "A. Mensah", "evidence_base64": "not base64 at all"},
        )
        assert refused.status_code == 422, refused.text

    def test_the_designer_drafts_amends_publishes_and_preserves(
        self, admin_client: TestClient
    ) -> None:
        types = admin_client.get(f"{API}/documents/types").json()
        pro_forma = next(item for item in types if item["code"] == "pro_forma")

        draft = admin_client.get(f"{API}/designer/types/{pro_forma['id']}/draft")
        assert draft.status_code == 200, draft.text
        state = draft.json()
        template_id = state["template_id"]
        assert state["version"] == 2
        items = next(
            section for section in state["definition"]["sections"] if section["kind"] == "items"
        )

        amended = admin_client.put(
            f"{API}/designer/templates/{template_id}/sections/{items['id']}"
            f"/columns/line_description",
            json={"changes": {"label": "Particulars of supply"}},
        )
        assert amended.status_code == 200, amended.text

        preview = admin_client.get(f"{API}/documents/templates/{template_id}/preview")
        assert preview.status_code == 200, preview.text
        assert "Particulars of supply" in preview.text

        published = admin_client.post(f"{API}/designer/templates/{template_id}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["version"] == 2

    def test_a_preview_is_drawn_from_a_named_quotation(self, admin_client: TestClient) -> None:
        quotation = self._quotation(admin_client)
        types = admin_client.get(f"{API}/documents/types").json()
        pro_forma = next(item for item in types if item["code"] == "pro_forma")
        state = admin_client.get(f"{API}/designer/types/{pro_forma['id']}/draft").json()
        preview = admin_client.get(
            f"{API}/documents/templates/{state['template_id']}/preview",
            params={"source_type": "quotation", "source_id": quotation["id"]},
        )
        assert preview.status_code == 200, preview.text
        assert "Reagent pack" in preview.text
        assert "Sample Customer" not in preview.text

    def test_an_internal_binding_is_refused_over_http(self, admin_client: TestClient) -> None:
        types = admin_client.get(f"{API}/documents/types").json()
        pro_forma = next(item for item in types if item["code"] == "pro_forma")
        state = admin_client.get(f"{API}/designer/types/{pro_forma['id']}/draft").json()
        items = next(
            section for section in state["definition"]["sections"] if section["kind"] == "items"
        )
        refused = admin_client.post(
            f"{API}/designer/templates/{state['template_id']}/sections/{items['id']}/columns",
            json={"binding": "internal.line_cost"},
        )
        assert refused.status_code == 422, refused.text

    def test_a_sales_person_may_issue_and_receipt_but_not_design(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        quotation = self._quotation(admin_client)
        types = admin_client.get(f"{API}/documents/types").json()
        pro_forma = next(item for item in types if item["code"] == "pro_forma")

        admin_client.post(
            f"{API}/users",
            json={
                "username": "seller",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
                "must_change_password": False,
            },
        )
        token = client.post(
            f"{API}/session", json={"username": "seller", "password": STAFF_PASSWORD}
        ).json()["token"]
        client.headers[SESSION_HEADER] = token

        document = client.post(
            f"{API}/documents/issue",
            json={
                "type_code": "delivery_note",
                "source_type": "quotation",
                "source_id": quotation["id"],
            },
        )
        assert document.status_code == 201, document.text
        receipt = client.post(
            f"{API}/documents/issued/{document.json()['id']}/acknowledgements",
            json={"recipient_name": "A. Mensah", "evidence_kind": "verbal"},
        )
        assert receipt.status_code == 201, receipt.text
        assert client.get(f"{API}/designer/types/{pro_forma['id']}/draft").status_code == 403


class TestProcurementOverHttp:
    """Phase 7 through the published API: acceptance, conversion, purchase, receipt,
    invoice with a variance, payment and the register."""

    def _issued_quotation(self, admin_client: TestClient) -> dict[str, str]:
        customer = admin_client.post(
            f"{API}/parties", json={"name": "Banhart Hospital", "is_customer": True}
        ).json()
        supplier = admin_client.post(
            f"{API}/parties", json={"name": "Gamma Laboratories", "is_supplier": True}
        ).json()
        unit = admin_client.get(f"{API}/units").json()[0]
        product = admin_client.post(
            f"{API}/products",
            json={"sku": "RGT-002", "name": "Reagent pack", "stock_unit_id": unit["id"]},
        ).json()
        quotation = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": customer["id"],
                "lines": [
                    {
                        "description": "Reagent pack",
                        "quantity": "40",
                        "unit_cost": "172",
                        "product_id": product["id"],
                        "sources": [
                            {
                                "kind": "supplier",
                                "quantity": "40",
                                "supplier_id": supplier["id"],
                                "unit_cost": "172",
                            }
                        ],
                    }
                ],
            },
        )
        assert quotation.status_code == 200, quotation.text
        issued = admin_client.post(f"{API}/quotations/{quotation.json()['id']}/issue", json={})
        assert issued.status_code == 200, issued.text
        return {
            "quotation": issued.json()["id"],
            "customer": customer["id"],
            "supplier": supplier["id"],
            "product": product["id"],
        }

    def test_the_whole_procurement_cycle(self, admin_client: TestClient) -> None:
        world = self._issued_quotation(admin_client)

        refused = admin_client.post(
            f"{API}/orders/quotations/{world['quotation']}/convert", json={}
        )
        assert refused.status_code == 409, refused.text

        evidence = admin_client.post(
            f"{API}/attachments",
            json={
                "filename": "BH-PO-2026-0441.pdf",
                "media_type": "application/pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 purchase order").decode("ascii"),
                "owner_type": "quotation",
                "owner_id": world["quotation"],
                "purpose": "acceptance",
                "immutable": True,
            },
        )
        assert evidence.status_code == 201, evidence.text
        accepted = admin_client.post(
            f"{API}/orders/quotations/{world['quotation']}/acceptance",
            json={"reference": "BH-PO-2026-0441", "evidence_attachment_id": evidence.json()["id"]},
        )
        assert accepted.status_code == 201, accepted.text
        converted = admin_client.post(
            f"{API}/orders/quotations/{world['quotation']}/convert", json={}
        )
        assert converted.status_code == 201, converted.text
        order = converted.json()
        assert order["number"].startswith("SO-")
        assert order["customer_name"] == "Banhart Hospital"

        suggested = admin_client.get(f"{API}/purchases/orders/suggest/{order['id']}").json()
        assert len(suggested) == 1 and suggested[0]["supplier_name"] == "Gamma Laboratories"
        raised = admin_client.post(
            f"{API}/purchases/orders",
            json={
                "supplier_id": suggested[0]["supplier_id"],
                "sales_order_id": order["id"],
                "lines": suggested[0]["lines"],
            },
        )
        assert raised.status_code == 201, raised.text
        purchase = raised.json()
        assert purchase["number"] is None
        assert (
            admin_client.post(f"{API}/purchases/orders/{purchase['id']}/approve").status_code == 200
        )
        purchase = admin_client.post(f"{API}/purchases/orders/{purchase['id']}/issue").json()
        assert purchase["number"].startswith("PO-")
        assert admin_client.get(f"{API}/orders/{order['id']}").json()["purchase_order_numbers"] == [
            purchase["number"]
        ]

        received = admin_client.post(
            f"{API}/purchases/receipts",
            json={
                "purchase_order_id": purchase["id"],
                "lines": [
                    {
                        "purchase_order_line_id": purchase["lines"][0]["id"],
                        "quantity_received": "40",
                        "batch": "B24-0417",
                    }
                ],
            },
        )
        assert received.status_code == 201, received.text
        assert received.json()["number"].startswith("GRN-")
        assert received.json()["journal_entry_fk"] is not None

        invoice = admin_client.post(
            f"{API}/purchases/invoices",
            json={
                "purchase_order_id": purchase["id"],
                "supplier_reference": "GL-7731",
                "invoiced_on": "2026-11-20",
                "lines": [
                    {
                        "purchase_order_line_id": purchase["lines"][0]["id"],
                        "quantity": "40",
                        "unit_cost": "175",
                    }
                ],
            },
        )
        assert invoice.status_code == 201, invoice.text
        body = invoice.json()
        assert body["state"] == "variance"
        assert body["variances"][0]["kind"] == "price"
        assert admin_client.post(f"{API}/purchases/invoices/{body['id']}/post").status_code == 409
        decided = admin_client.post(
            f"{API}/purchases/variances/{body['variances'][0]['id']}/decide",
            json={"approve": True, "reason": "Rise agreed on 18 Nov"},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["state"] == "matched"
        posted = admin_client.post(f"{API}/purchases/invoices/{body['id']}/post")
        assert posted.status_code == 200, posted.text
        assert posted.json()["state"] == "posted"
        assert posted.json()["journal_entry_fk"] is not None

        register = admin_client.get(f"{API}/purchases/payables").json()
        assert Decimal(register["total_outstanding"]) == Decimal("7000")
        assert register["suggested_amounts"][0]["exact"] is True

        paid = admin_client.post(
            f"{API}/purchases/payments",
            json={"supplier_id": world["supplier"], "amount": "7000", "method": "bank"},
        )
        assert paid.status_code == 201, paid.text
        assert paid.json()["number"].startswith("PV-")
        after = admin_client.get(f"{API}/purchases/payables").json()
        assert Decimal(after["total_outstanding"]) == ZERO

        variance = admin_client.get(f"{API}/orders/{order['id']}/cost-variance")
        assert variance.status_code == 200, variance.text
        assert variance.json()["lines_known"] == 1

    def test_a_sales_person_sees_orders_and_not_purchases(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        world = self._issued_quotation(admin_client)
        admin_client.post(
            f"{API}/orders/quotations/{world['quotation']}/acceptance",
            json={"reference": "BH-PO-2026-0441"},
        )
        order = admin_client.post(
            f"{API}/orders/quotations/{world['quotation']}/convert", json={}
        ).json()
        admin_client.post(
            f"{API}/users",
            json={
                "username": "seller",
                "full_name": "A. Seller",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
                "must_change_password": False,
            },
        )
        token = client.post(
            f"{API}/session", json={"username": "seller", "password": STAFF_PASSWORD}
        ).json()["token"]
        client.headers[SESSION_HEADER] = token
        assert client.get(f"{API}/orders/{order['id']}").status_code == 200
        assert client.get(f"{API}/orders/{order['id']}/cost-variance").status_code == 403
        assert client.get(f"{API}/purchases/orders").status_code == 403
        assert client.get(f"{API}/purchases/payables").status_code == 403


class TestInventoryOverHttp:
    """Phase 8 through the published API: opening stock, issue with expiry override,
    a transfer through transit, a count, reversal, and the two reconciliations."""

    def _product(self, admin_client: TestClient, sku: str, **extra: object) -> dict:
        unit = admin_client.get(f"{API}/units").json()[0]
        created = admin_client.post(
            f"{API}/products",
            json={"sku": sku, "name": f"Item {sku}", "stock_unit_id": unit["id"], **extra},
        )
        assert created.status_code in (200, 201), created.text
        return created.json()

    def test_stock_moves_and_the_records_agree(self, admin_client: TestClient) -> None:
        warehouses = admin_client.get(f"{API}/inventory/warehouses").json()
        main = warehouses[0]
        assert {location["code"] for location in main["locations"]} >= {"MAIN", "QUAR", "ASSY"}
        gloves = self._product(admin_client, "GLV-100")
        reagent = self._product(
            admin_client, "RGT-200", tracking_policy="batch", requires_expiry=True
        )

        refused = admin_client.post(
            f"{API}/inventory/opening",
            json={
                "warehouse_id": main["id"],
                "lines": [{"product_id": gloves["id"], "quantity": 10.5, "unit_cost": "2"}],
            },
        )
        assert refused.status_code == 422, "a JSON number is refused"
        opened = admin_client.post(
            f"{API}/inventory/opening",
            json={
                "warehouse_id": main["id"],
                "lines": [
                    {"product_id": gloves["id"], "quantity": "10", "unit_cost": "2"},
                    {
                        "product_id": reagent["id"],
                        "quantity": "4",
                        "unit_cost": "15",
                        "batch": "LOT-1",
                        "expiry": "2026-01-31",
                    },
                ],
            },
        )
        assert opened.status_code == 201, opened.text
        batches = admin_client.get(f"{API}/inventory/products/{reagent['id']}/batches").json()
        assert batches[0]["status"] == "expired"

        blocked = admin_client.post(
            f"{API}/inventory/issues",
            json={
                "product_id": reagent["id"],
                "warehouse_id": main["id"],
                "quantity": "1",
                "batch_id": batches[0]["batch_id"],
            },
        )
        assert blocked.status_code == 409, blocked.text
        overridden = admin_client.post(
            f"{API}/inventory/issues",
            json={
                "product_id": reagent["id"],
                "warehouse_id": main["id"],
                "quantity": "1",
                "batch_id": batches[0]["batch_id"],
                "override_reason": "Customer accepts for training",
            },
        )
        assert overridden.status_code == 201, overridden.text

        damage = admin_client.post(
            f"{API}/inventory/adjustments",
            json={
                "warehouse_id": main["id"],
                "kind": "damage",
                "reason": "Water damage",
                "lines": [{"product_id": gloves["id"], "quantity": "2"}],
            },
        ).json()
        reversed_ = admin_client.post(
            f"{API}/inventory/operations/{damage['operation_id']}/reverse",
            json={"reason": "Wrong product"},
        )
        assert reversed_.status_code == 200, reversed_.text

        count = admin_client.post(
            f"{API}/inventory/counts", json={"warehouse_id": main["id"]}
        ).json()
        line = next(item for item in count["lines"] if item["sku"] == "GLV-100")
        admin_client.put(
            f"{API}/inventory/counts/{count['id']}/lines",
            json={
                "lines": [
                    {"line_id": item["id"], "counted_quantity": item["system_quantity"]}
                    for item in count["lines"]
                    if item["id"] != line["id"]
                ]
                + [{"line_id": line["id"], "counted_quantity": "9", "reason": "One missing"}]
            },
        )
        assert admin_client.post(f"{API}/inventory/counts/{count['id']}/submit").status_code == 200
        approved = admin_client.post(f"{API}/inventory/counts/{count['id']}/approve")
        assert approved.json()["state"] == "posted", approved.text

        positions = admin_client.get(f"{API}/inventory/positions").json()
        row = next(item for item in positions if item["sku"] == "GLV-100")
        assert (row["usable"], row["value"]) == ("9.000000", "18.000000")

        valuation = admin_client.get(f"{API}/inventory/valuation").json()
        assert valuation["agrees"], valuation["accounts"]
        reconciliation = admin_client.get(f"{API}/inventory/reconciliation").json()
        assert reconciliation["agrees"], reconciliation["discrepancies"]
        movements = admin_client.get(f"{API}/inventory/movements").json()
        assert any(item["override_reason"] for item in movements)


class TestDispatchOverHttp:
    """Phase 9 through the published API: prepare, pack, dispatch with a waybill, a
    partial delivery signed for, the residual outstanding, and the reprint."""

    def test_a_partial_delivery_signed_for_on_the_waybill(self, admin_client: TestClient) -> None:
        warehouse = admin_client.get(f"{API}/inventory/warehouses").json()[0]
        customer = admin_client.post(
            f"{API}/parties", json={"name": "Ridge Hospital", "is_customer": True}
        ).json()
        unit = admin_client.get(f"{API}/units").json()[0]
        product = admin_client.post(
            f"{API}/products",
            json={"sku": "GLV-900", "name": "Nitrile gloves", "stock_unit_id": unit["id"]},
        ).json()
        opened = admin_client.post(
            f"{API}/inventory/opening",
            json={
                "warehouse_id": warehouse["id"],
                "lines": [{"product_id": product["id"], "quantity": "20", "unit_cost": "15"}],
            },
        )
        assert opened.status_code == 201, opened.text
        quotation = admin_client.post(
            f"{API}/quotations",
            json={
                "customer_id": customer["id"],
                "lines": [
                    {
                        "description": "Nitrile gloves",
                        "quantity": "20",
                        "product_id": product["id"],
                        "sources": [
                            {
                                "kind": "stock",
                                "quantity": "20",
                                "unit_cost": "15",
                                "warehouse_id": warehouse["id"],
                            }
                        ],
                    }
                ],
            },
        ).json()
        admin_client.post(f"{API}/quotations/{quotation['id']}/issue", json={})
        admin_client.post(
            f"{API}/orders/quotations/{quotation['id']}/acceptance",
            json={"reference": "RH-PO-77"},
        )
        order = admin_client.post(
            f"{API}/orders/quotations/{quotation['id']}/convert", json={}
        ).json()

        lines = admin_client.get(f"{API}/dispatch/orders/{order['id']}/lines").json()
        assert lines[0]["shippable"] == "20.000000"
        shipment = admin_client.post(
            f"{API}/dispatch/shipments",
            json={
                "sales_order_id": order["id"],
                "destination": "Stores, Ridge Hospital, Accra",
                "lines": [{"sales_order_line_id": lines[0]["line_id"], "quantity": "12"}],
            },
        )
        assert shipment.status_code == 201, shipment.text
        shipment_id = shipment.json()["id"]
        pick = admin_client.post(f"{API}/dispatch/shipments/{shipment_id}/documents/pick_list")
        assert pick.status_code == 200, pick.text
        early = admin_client.post(f"{API}/dispatch/shipments/{shipment_id}/documents/waybill")
        assert early.status_code == 409, "no waybill before the goods leave"
        assert (
            admin_client.post(
                f"{API}/dispatch/shipments/{shipment_id}/pack", json={"package_count": 2}
            ).status_code
            == 200
        )
        refused = admin_client.post(
            f"{API}/dispatch/shipments/{shipment_id}/dispatch",
            json={"mode": "carrier", "package_count": 2, "destination": "Ridge"},
        )
        assert refused.status_code == 422, "a carrier is named"
        sent = admin_client.post(
            f"{API}/dispatch/shipments/{shipment_id}/dispatch",
            json={
                "mode": "carrier",
                "package_count": 2,
                "carrier": "Speedline Logistics",
                "tracking_reference": "SPL-1",
            },
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["state"] == "dispatched"

        signature = base64.b64encode(b"\x89PNG signature").decode()
        delivered = admin_client.post(
            f"{API}/dispatch/shipments/{shipment_id}/deliver",
            json={
                "outcomes": [
                    {
                        "shipment_line_id": sent.json()["lines"][0]["id"],
                        "delivered": "9",
                        "remainder_as": "returned",
                        "reason": "Three boxes refused",
                    }
                ],
                "recipient_name": "E. Quaye",
                "evidence_base64": signature,
                "media_type": "image/png",
            },
        )
        assert delivered.status_code == 200, delivered.text
        record = delivered.json()
        assert record["state"] == "partially_delivered"
        assert record["proof"]["recipient_name"] == "E. Quaye"
        assert record["exceptions"][0]["kind"] == "returned"
        waybill = record["waybill"]
        assert waybill is not None

        after = admin_client.get(f"{API}/dispatch/orders/{order['id']}/lines").json()[0]
        assert (after["delivered"], after["outstanding"], after["shippable"]) == (
            "9.000000",
            "11.000000",
            "11.000000",
        )
        proofs = admin_client.get(f"{API}/documents/issued/{waybill['id']}/acknowledgements")
        assert [item["recipient_name"] for item in proofs.json()] == ["E. Quaye"]
        first = admin_client.get(f"{API}/documents/issued/{waybill['id']}/file").content
        second = admin_client.get(f"{API}/documents/issued/{waybill['id']}/file").content
        assert first == second and first.startswith(b"%PDF")
        listed = admin_client.get(f"{API}/dispatch/shipments", params={"order_id": order["id"]})
        assert [item["id"] for item in listed.json()] == [shipment_id]
        valuation = admin_client.get(f"{API}/inventory/valuation").json()
        assert valuation["agrees"], valuation["accounts"]


class TestRegisterOverHttp:
    """Phase 10 through the published API: a sales person opens a shift, scans, takes
    cash, completes with a receipt, is refused a refund alone, and has it authorised by
    the proprietor at the register; then the drawer is counted and closed."""

    def test_a_counter_sale_from_scan_to_receipt_and_an_authorised_refund(
        self, admin_client: TestClient
    ) -> None:
        warehouse = admin_client.get(f"{API}/inventory/warehouses").json()[0]
        unit = admin_client.get(f"{API}/units").json()[0]
        product = admin_client.post(
            f"{API}/products",
            json={
                "sku": "SWB-020",
                "name": "Alcohol swabs",
                "stock_unit_id": unit["id"],
                "list_price": "12.50",
            },
        ).json()
        admin_client.post(
            f"{API}/inventory/opening",
            json={
                "warehouse_id": warehouse["id"],
                "lines": [{"product_id": product["id"], "quantity": "40", "unit_cost": "5"}],
            },
        )
        admin_client.post(
            f"{API}/users",
            json={
                "username": "counter1",
                "full_name": "E. Mensah",
                "password": STAFF_PASSWORD,
                "role_codes": ["sales_person"],
            },
        )
        admin_token = admin_client.headers[SESSION_HEADER]
        admin_client.headers[SESSION_HEADER] = sign_in(admin_client, "counter1", STAFF_PASSWORD)

        assert admin_client.get(f"{API}/pos/shift").json() is None
        opened = admin_client.post(f"{API}/pos/shifts", json={"opening_float": "100"})
        assert opened.status_code == 201, opened.text
        shift_id = opened.json()["shift"]["id"]

        sale = admin_client.get(f"{API}/pos/sale").json()
        scanned = admin_client.post(
            f"{API}/pos/sales/{sale['id']}/scan", json={"code": "SWB-020", "quantity": "8"}
        )
        assert scanned.status_code == 200, scanned.text
        body = scanned.json()
        assert body["net_total"] == "100.000000" and body["total"] == "121.900000"
        assert body["suggested_cash"][0] == {"amount": "121.90", "exact": True}

        discount = admin_client.post(
            f"{API}/pos/sales/{sale['id']}/lines/{body['lines'][0]['id']}/discount",
            json={"rate": "0.1"},
        )
        assert discount.status_code == 403  # the sales person holds no discount

        tendered = admin_client.post(
            f"{API}/pos/sales/{sale['id']}/tenders",
            json={"method": "cash", "cash_received": "150"},
        ).json()
        assert tendered["due"] == "0.000000"
        completed = admin_client.post(f"{API}/pos/sales/{sale['id']}/complete")
        assert completed.status_code == 200, completed.text
        done = completed.json()
        assert done["state"] == "completed" and done["receipt_id"]
        assert done["change"] == "28.100000"
        receipt = admin_client.get(f"{API}/pos/sales/{sale['id']}/receipt.txt")
        assert done["number"] in receipt.text and "Change" in receipt.text

        refund = {
            "lines": [{"sale_line_id": done["lines"][0]["id"], "quantity": "2"}],
            "reason": "Two packets opened",
        }
        refused = admin_client.post(f"{API}/pos/sales/{sale['id']}/refunds", json=refund)
        assert refused.status_code == 403
        wrong = admin_client.post(
            f"{API}/pos/sales/{sale['id']}/refunds",
            json={**refund, "supervisor": {"username": "proprietor", "password": "wrong"}},
        )
        assert wrong.status_code == 401
        authorised = admin_client.post(
            f"{API}/pos/sales/{sale['id']}/refunds",
            json={
                **refund,
                "supervisor": {"username": "proprietor", "password": "Provision9Admin"},
            },
        )
        assert authorised.status_code == 201, authorised.text
        made = authorised.json()
        assert made["sale_id"] == sale["id"]
        assert made["authorised_by"] == "N. Nickland" and made["requested_by"] == "E. Mensah"
        assert made["total"] == "30.480000"  # 25.00 net and 5.48 tax, two of eight

        summary = admin_client.get(f"{API}/pos/shifts/{shift_id}").json()
        assert summary["drawer"]["expected"] == "191.420000"  # 100 + 121.90 - 30.48
        counted = admin_client.post(
            f"{API}/pos/shifts/{shift_id}/count",
            json={"denominations": {"100": 1, "50": 1, "20": 2}, "variance_reason": "Short"},
        ).json()
        assert counted["shift"]["state"] == "submitted"
        assert Decimal(counted["shift"]["variance"]) == Decimal("-1.42")

        admin_client.headers[SESSION_HEADER] = admin_token
        closed = admin_client.post(f"{API}/pos/shifts/{shift_id}/approve").json()
        assert closed["shift"]["state"] == "closed"
        assert closed["shift"]["approved_by"] == "N. Nickland"
