"""Custom-field definitions and values.

Fields are added to products, parties and transactions without source-code change,
per PRD 6.1.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.organisation import CustomFieldTarget, CustomFieldType, validate_code
from nickland.persistence.base import as_dict
from nickland.persistence.models import CustomFieldDefinition, CustomFieldValue
from nickland.platform.clock import Clock, SystemClock


class CustomFieldService:
    """Definition and capture of administrator-defined fields."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def definitions(
        self, actor: AnyActor, target: CustomFieldTarget | str, *, include_inactive: bool = False
    ) -> list[CustomFieldDefinition]:
        statement = select(CustomFieldDefinition).where(
            CustomFieldDefinition.business_id == actor.business_id,
            CustomFieldDefinition.target == str(target),
        )
        if not include_inactive:
            statement = statement.where(CustomFieldDefinition.is_active.is_(True))
        return list(
            self._db.scalars(
                statement.order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.label)
            )
        )

    def define(
        self,
        actor: AnyActor,
        *,
        target: CustomFieldTarget | str,
        code: str,
        label: str,
        field_type: CustomFieldType | str,
        is_required: bool = False,
        choices: list[str] | None = None,
        default_value: Any = None,
        help_text: str | None = None,
        display_order: int = 0,
        show_on_documents: bool = False,
        validation: dict[str, Any] | None = None,
    ) -> CustomFieldDefinition:
        """Define a custom field."""
        actor.require("custom_field.manage")
        field_code = validate_code(code)
        if not label.strip():
            raise ValidationError("Label is required.", field="label")

        kind = CustomFieldType(str(field_type))
        target_value = CustomFieldTarget(str(target))

        if kind in (CustomFieldType.CHOICE, CustomFieldType.MULTI_CHOICE):
            if not choices:
                raise ValidationError(
                    "A choice field requires at least one option.", field="choices"
                )
            cleaned = [option.strip() for option in choices if option.strip()]
            if len(set(cleaned)) != len(cleaned):
                raise ValidationError("Choice options must be distinct.", field="choices")
            choices = cleaned
        elif choices:
            raise ValidationError(f"A {kind.value} field does not take choices.", field="choices")

        existing = self._db.scalar(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.business_id == actor.business_id,
                CustomFieldDefinition.target == target_value.value,
                CustomFieldDefinition.code == field_code,
            )
        )
        if existing is not None:
            raise ConflictError(
                f"A field with code {field_code!r} already exists on {target_value.value}."
            )

        definition = CustomFieldDefinition(
            business_id=actor.business_id,
            target=target_value.value,
            code=field_code,
            label=label.strip(),
            field_type=kind.value,
            is_required=is_required,
            choices=choices,
            default_value=default_value,
            help_text=help_text or None,
            display_order=display_order,
            show_on_documents=show_on_documents,
            validation=validation,
        )
        self._db.add(definition)
        self._db.flush()

        self._audit.record(
            actor,
            AuditAction.CUSTOM_FIELD_CREATED,
            entity_type="custom_field_definition",
            entity_id=definition.id,
            entity_label=f"{target_value.value}.{field_code}",
            after={"label": definition.label, "type": kind.value, "required": is_required},
        )
        return definition

    def amend(
        self, actor: AnyActor, definition_id: uuid.UUID, changes: dict[str, Any]
    ) -> CustomFieldDefinition:
        """Amend a definition. The code and type are fixed once values exist."""
        actor.require("custom_field.manage")
        definition = self._require_definition(actor, definition_id)
        before = as_dict(definition)

        permitted = {
            "label",
            "help_text",
            "is_required",
            "display_order",
            "choices",
            "default_value",
            "show_on_documents",
            "validation",
        }
        unknown = set(changes) - permitted
        if unknown:
            raise ValidationError(
                f"These properties cannot be amended: {sorted(unknown)}. "
                "A field's code and type are fixed once it exists."
            )

        if "choices" in changes and definition.field_type in (
            CustomFieldType.CHOICE.value,
            CustomFieldType.MULTI_CHOICE.value,
        ):
            removed = set(definition.choices or []) - set(changes["choices"] or [])
            if removed and self._values_exist(definition.id):
                raise ConflictError(
                    f"These options are in use and cannot be removed: {sorted(removed)}. "
                    "Deactivate the field instead."
                )

        for key, value in changes.items():
            setattr(definition, key, value)

        self._audit.record_change(
            actor,
            AuditAction.CUSTOM_FIELD_AMENDED,
            entity_type="custom_field_definition",
            entity_id=definition.id,
            entity_label=f"{definition.target}.{definition.code}",
            before=before,
            after=as_dict(definition),
        )
        return definition

    def withdraw(
        self, actor: AnyActor, definition_id: uuid.UUID, *, reason: str
    ) -> CustomFieldDefinition:
        """Deactivate a definition.

        Captured values are retained. A definition in use is never removed, so historical
        records remain complete.
        """
        actor.require("custom_field.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        definition = self._require_definition(actor, definition_id)
        definition.is_active = False

        self._audit.record(
            actor,
            AuditAction.CUSTOM_FIELD_WITHDRAWN,
            entity_type="custom_field_definition",
            entity_id=definition.id,
            entity_label=f"{definition.target}.{definition.code}",
            reason=reason,
        )
        return definition

    def set_values(
        self,
        actor: AnyActor,
        *,
        target: CustomFieldTarget | str,
        owner_id: uuid.UUID,
        values: dict[str, Any],
    ) -> None:
        """Record custom-field values against a record.

        Every required active field must be supplied, and an unknown code is rejected
        rather than silently discarded.
        """
        target_value = CustomFieldTarget(str(target))
        definitions = {
            definition.code: definition for definition in self.definitions(actor, target_value)
        }

        unknown = set(values) - set(definitions)
        if unknown:
            raise ValidationError(
                f"Unknown custom field(s) for {target_value.value}: {sorted(unknown)}"
            )

        missing = [
            definition.label
            for code, definition in definitions.items()
            if definition.is_required and _is_blank(values.get(code))
        ]
        if missing:
            raise ValidationError(f"These fields are required: {missing}")

        for code, raw in values.items():
            definition = definitions[code]
            coerced = _coerce(definition, raw)
            record = self._db.scalar(
                select(CustomFieldValue).where(
                    CustomFieldValue.definition_fk == definition.id,
                    CustomFieldValue.owner_id == owner_id,
                )
            )
            if record is None:
                record = CustomFieldValue(
                    business_id=actor.business_id,
                    definition_fk=definition.id,
                    owner_type=target_value.value,
                    owner_id=owner_id,
                )
                self._db.add(record)
            for attribute in (
                "value_text",
                "value_number",
                "value_date",
                "value_boolean",
                "value_json",
            ):
                setattr(record, attribute, None)
            setattr(record, _column_for(definition), coerced)
        self._db.flush()

    def get_values(
        self, actor: AnyActor, *, target: CustomFieldTarget | str, owner_id: uuid.UUID
    ) -> dict[str, Any]:
        """Read the custom-field values of a record."""
        target_value = CustomFieldTarget(str(target))
        rows = self._db.execute(
            select(CustomFieldDefinition, CustomFieldValue)
            .join(
                CustomFieldValue,
                CustomFieldValue.definition_fk == CustomFieldDefinition.id,
            )
            .where(
                CustomFieldDefinition.business_id == actor.business_id,
                CustomFieldDefinition.target == target_value.value,
                CustomFieldValue.owner_id == owner_id,
            )
        ).all()
        return {
            definition.code: getattr(value, _column_for(definition)) for definition, value in rows
        }

    def _require_definition(
        self, actor: AnyActor, definition_id: uuid.UUID
    ) -> CustomFieldDefinition:
        definition = self._db.scalar(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.business_id == actor.business_id,
                CustomFieldDefinition.id == definition_id,
            )
        )
        if definition is None:
            raise NotFoundError("Custom field not found.")
        return definition

    def _values_exist(self, definition_id: uuid.UUID) -> bool:
        return (
            self._db.scalar(
                select(CustomFieldValue.id)
                .where(CustomFieldValue.definition_fk == definition_id)
                .limit(1)
            )
            is not None
        )


def _column_for(definition: CustomFieldDefinition) -> str:
    return {
        CustomFieldType.TEXT.value: "value_text",
        CustomFieldType.LONG_TEXT.value: "value_text",
        CustomFieldType.NUMBER.value: "value_number",
        CustomFieldType.AMOUNT.value: "value_number",
        CustomFieldType.DATE.value: "value_date",
        CustomFieldType.BOOLEAN.value: "value_boolean",
        CustomFieldType.CHOICE.value: "value_text",
        CustomFieldType.MULTI_CHOICE.value: "value_json",
    }[definition.field_type]


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def _coerce(definition: CustomFieldDefinition, raw: Any) -> Any:
    """Coerce and validate one value against its definition."""
    kind = CustomFieldType(definition.field_type)
    if _is_blank(raw):
        return None

    if kind in (CustomFieldType.TEXT, CustomFieldType.LONG_TEXT):
        text = str(raw)
        limit = (definition.validation or {}).get("max_length")
        if limit and len(text) > int(limit):
            raise ValidationError(
                f"{definition.label} must be at most {limit} characters.",
                field=definition.code,
            )
        return text

    if kind in (CustomFieldType.NUMBER, CustomFieldType.AMOUNT):
        if isinstance(raw, float):
            raise ValidationError(
                f"{definition.label} must be supplied as a decimal string, not a "
                "floating-point number.",
                field=definition.code,
            )
        try:
            number = Decimal(str(raw))
        except Exception as error:
            raise ValidationError(
                f"{definition.label} must be a number.", field=definition.code
            ) from error
        rules = definition.validation or {}
        if "minimum" in rules and number < Decimal(str(rules["minimum"])):
            raise ValidationError(
                f"{definition.label} must be at least {rules['minimum']}.",
                field=definition.code,
            )
        if "maximum" in rules and number > Decimal(str(rules["maximum"])):
            raise ValidationError(
                f"{definition.label} must be at most {rules['maximum']}.",
                field=definition.code,
            )
        return str(number)

    if kind is CustomFieldType.DATE:
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw))
        except ValueError as error:
            raise ValidationError(
                f"{definition.label} must be a date in YYYY-MM-DD form.",
                field=definition.code,
            ) from error

    if kind is CustomFieldType.BOOLEAN:
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in {"true", "yes", "1"}:
            return True
        if str(raw).lower() in {"false", "no", "0"}:
            return False
        raise ValidationError(f"{definition.label} must be yes or no.", field=definition.code)

    if kind is CustomFieldType.CHOICE:
        if str(raw) not in (definition.choices or []):
            raise ValidationError(
                f"{definition.label} must be one of: {definition.choices}",
                field=definition.code,
            )
        return str(raw)

    # Multiple choice
    selected = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    invalid = [item for item in selected if str(item) not in (definition.choices or [])]
    if invalid:
        raise ValidationError(
            f"{definition.label} contains invalid option(s): {invalid}",
            field=definition.code,
        )
    return [str(item) for item in selected]
