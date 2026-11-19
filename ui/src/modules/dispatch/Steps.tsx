/**
 * The three steps that change what a shipment owes: dispatching it, confirming what was
 * received, and accepting goods back after delivery.
 */

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { DeliveryOutcomeInput, DispatchModeName, Shipment } from "@/api/types";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  Field,
  RadioGroup,
  Select,
  Stepper,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { editable, quantity } from "@/lib/format";
import { useSession } from "@/lib/session";
import { SignaturePad, type SignaturePadHandle } from "@/modules/documents/ReceiptDialog";

// -- dispatch ---------------------------------------------------------------------------

const MODES: { value: DispatchModeName; label: string; hint: string }[] = [
  { value: "own_vehicle", label: "Own vehicle", hint: "Our driver and vehicle." },
  { value: "carrier", label: "Carrier", hint: "A haulage company." },
  { value: "courier", label: "Courier", hint: "A parcel service." },
  { value: "collection", label: "Customer collection", hint: "Collected at our premises." },
];

export function DispatchDetailsDialog({
  shipment,
  onClose,
  onDone,
}: {
  shipment: Shipment;
  onClose: () => void;
  onDone: (next: Shipment) => void;
}) {
  const toast = useToast();
  const { can } = useSession();
  const [mode, setMode] = useState<DispatchModeName>("own_vehicle");
  const [carrier, setCarrier] = useState("");
  const [driver, setDriver] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [destination, setDestination] = useState(shipment.destination ?? "");
  const [tracking, setTracking] = useState("");
  const [packages, setPackages] = useState(String(shipment.package_count || 1));
  const [override, setOverride] = useState("");
  const [expired, setExpired] = useState(false);

  const send = useMutation({
    mutationFn: async () => {
      const next = await api.dispatch.dispatch(shipment.id, {
        mode,
        package_count: Number(packages),
        carrier: carrier || null,
        driver: driver || null,
        vehicle: vehicle || null,
        destination: destination || null,
        tracking_reference: tracking || null,
        override_reason: override || null,
      });
      // The waybill leaves with the goods.
      await api.dispatch.issueDocument(shipment.id, "waybill");
      return api.dispatch.shipment(next.id);
    },
    onSuccess: (next) => {
      onDone(next);
      toast.show(
        "success",
        `${next.number} dispatched. Waybill ${next.waybill?.number ?? ""} issued.`,
      );
      onClose();
    },
    onError: (error) => {
      if (describe(error).message.toLowerCase().includes("expired")) setExpired(true);
    },
  });

  const named = mode === "carrier" || mode === "courier";
  const ready =
    Number(packages) >= 1 &&
    (!named || carrier.trim()) &&
    (mode !== "own_vehicle" || (driver.trim() && vehicle.trim())) &&
    (mode === "collection" || destination.trim()) &&
    (!expired || override.trim());

  return (
    <Dialog
      title={`Dispatch ${shipment.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="counter"
            onClick={() => send.mutate()}
            busy={send.isPending}
            disabled={!ready}
          >
            Dispatch and issue waybill
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {send.isError ? <Alert tone="error" title={describe(send.error).message} /> : null}
        <RadioGroup legend="Carriage" value={mode} onChange={setMode} options={MODES} />
        <div className="form-grid">
          {named ? (
            <Field label={mode === "courier" ? "Courier" : "Carrier"} required>
              {(id) => (
                <TextInput
                  id={id}
                  value={carrier}
                  onChange={(event) => setCarrier(event.target.value)}
                  placeholder="Speedline Logistics"
                />
              )}
            </Field>
          ) : null}
          {mode !== "collection" ? (
            <>
              <Field
                label="Driver"
                required={mode === "own_vehicle"}
                optional={mode !== "own_vehicle"}
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={driver}
                    onChange={(event) => setDriver(event.target.value)}
                  />
                )}
              </Field>
              <Field
                label="Vehicle"
                required={mode === "own_vehicle"}
                optional={mode !== "own_vehicle"}
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={vehicle}
                    onChange={(event) => setVehicle(event.target.value)}
                    placeholder="GR 2214-24"
                  />
                )}
              </Field>
            </>
          ) : null}
          <Field label="Packages">
            {(id) => <Stepper id={id} min={1} value={packages} onChange={setPackages} />}
          </Field>
          {named ? (
            <Field label="Tracking reference" optional>
              {(id) => (
                <TextInput
                  id={id}
                  value={tracking}
                  onChange={(event) => setTracking(event.target.value)}
                />
              )}
            </Field>
          ) : null}
          {mode !== "collection" ? (
            <Field label="Deliver to" required full>
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={destination}
                  onChange={(event) => setDestination(event.target.value)}
                />
              )}
            </Field>
          ) : null}
          {expired ? (
            can("expiry.override") ? (
              <Field label="Reason for sending expired stock" required full>
                {(id) => (
                  <TextInput
                    id={id}
                    value={override}
                    onChange={(event) => setOverride(event.target.value)}
                  />
                )}
              </Field>
            ) : (
              <Alert
                tone="warning"
                title="An authorised override is required to send expired stock."
              />
            )
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}

// -- delivery ---------------------------------------------------------------------------

type Remainder = "returned" | "damaged" | "lost";

interface LineEntry {
  delivered: string;
  remainder: Remainder;
  reason: string;
  serials: string;
}

export function DeliveryDialog({
  shipment,
  onClose,
  onDone,
}: {
  shipment: Shipment;
  onClose: () => void;
  onDone: (next: Shipment) => void;
}) {
  const toast = useToast();
  const [failed, setFailed] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [drawn, setDrawn] = useState(false);
  const pad = useRef<SignaturePadHandle | null>(null);
  const [entries, setEntries] = useState<Record<string, LineEntry>>(() =>
    Object.fromEntries(
      shipment.lines.map((line) => [
        line.id,
        { delivered: editable(line.quantity), remainder: "returned", reason: "", serials: "" },
      ]),
    ),
  );

  const amend = (id: string, patch: Partial<LineEntry>) =>
    setEntries((rows) => ({ ...rows, [id]: { ...rows[id]!, ...patch } }));

  const short = (id: string) => {
    const line = shipment.lines.find((item) => item.id === id)!;
    return failed || Number(entries[id]!.delivered) < Number(line.quantity);
  };

  const confirm = useMutation({
    mutationFn: () => {
      const outcomes: DeliveryOutcomeInput[] = shipment.lines.map((line) => {
        const entry = entries[line.id]!;
        return {
          shipment_line_id: line.id,
          delivered: failed ? "0" : entry.delivered,
          remainder_as: short(line.id) ? entry.remainder : null,
          reason: short(line.id) ? entry.reason || null : null,
          serials_delivered: entry.serials
            .split(/[\n,]/)
            .map((item) => item.trim())
            .filter(Boolean),
        };
      });
      return api.dispatch.deliver(shipment.id, {
        outcomes,
        recipient_name: failed ? null : name,
        recipient_role: failed ? null : role || null,
        evidence_kind: "signature",
        evidence_base64: failed ? null : (pad.current?.toBase64() ?? null),
        media_type: failed ? null : "image/png",
      });
    },
    onSuccess: (next) => {
      onDone(next);
      toast.show("success", `${next.number}: ${next.state_label.toLowerCase()}.`);
      onClose();
    },
  });

  const reasonsGiven = shipment.lines.every(
    (line) => !short(line.id) || entries[line.id]!.reason.trim(),
  );
  const ready = reasonsGiven && (failed || (name.trim() && drawn));

  return (
    <Dialog
      size="lg"
      title={`Confirm delivery of ${shipment.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="counter"
            onClick={() => confirm.mutate()}
            busy={confirm.isPending}
            disabled={!ready}
          >
            {failed ? "Record failed delivery" : "Confirm delivery"}
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {confirm.isError ? <Alert tone="error" title={describe(confirm.error).message} /> : null}
        <Checkbox
          label="Nothing was delivered"
          hint="Every unit comes back to the store, held again for the order."
          checked={failed}
          onChange={setFailed}
        />
        {shipment.lines.map((line) => {
          const entry = entries[line.id]!;
          return (
            <div className="receive-line" key={line.id}>
              <div className="receive-line__item">
                <span className="receive-line__name">{line.description}</span>
                <span className="caption">
                  {line.sku} · {quantity(line.quantity)} shipped
                </span>
              </div>
              {!failed ? (
                <Field label="Received">
                  {(id) => (
                    <Stepper
                      id={id}
                      min={0}
                      max={Number(line.quantity)}
                      value={entry.delivered}
                      onChange={(value) => amend(line.id, { delivered: value })}
                    />
                  )}
                </Field>
              ) : null}
              {short(line.id) ? (
                <>
                  <Field label="The rest">
                    {(id) => (
                      <Select
                        id={id}
                        value={entry.remainder}
                        onChange={(event) =>
                          amend(line.id, { remainder: event.target.value as Remainder })
                        }
                      >
                        <option value="returned">Returned to store</option>
                        <option value="damaged">Returned damaged</option>
                        <option value="lost">Lost in transit</option>
                      </Select>
                    )}
                  </Field>
                  <Field label="Reason" required>
                    {(id) => (
                      <TextInput
                        id={id}
                        value={entry.reason}
                        onChange={(event) => amend(line.id, { reason: event.target.value })}
                        placeholder="Refused at goods-inwards"
                      />
                    )}
                  </Field>
                  {line.tracking === "serial" && !failed ? (
                    <Field label="Serials received" required hint="One per line">
                      {(id) => (
                        <TextArea
                          id={id}
                          rows={2}
                          value={entry.serials}
                          onChange={(event) => amend(line.id, { serials: event.target.value })}
                        />
                      )}
                    </Field>
                  ) : null}
                </>
              ) : null}
            </div>
          );
        })}
        {!failed ? (
          <div className="form-grid">
            <Field label="Received by" required>
              {(id) => (
                <TextInput
                  id={id}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Full name"
                />
              )}
            </Field>
            <Field label="Position" optional>
              {(id) => (
                <TextInput
                  id={id}
                  value={role}
                  onChange={(event) => setRole(event.target.value)}
                  placeholder="Stores supervisor"
                />
              )}
            </Field>
            <Field label="Signature" required full>
              {(id) => <SignaturePad id={id} handle={pad} onDrawnChange={setDrawn} />}
            </Field>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

// -- return after delivery --------------------------------------------------------------

export function ReturnDialog({
  shipment,
  lineId,
  onClose,
  onDone,
}: {
  shipment: Shipment;
  lineId: string;
  onClose: () => void;
  onDone: (next: Shipment) => void;
}) {
  const toast = useToast();
  const line = shipment.lines.find((item) => item.id === lineId)!;
  const [amount, setAmount] = useState("1");
  const [reason, setReason] = useState("");
  const [replace, setReplace] = useState(true);

  const accept = useMutation({
    mutationFn: () =>
      api.dispatch.customerReturn(shipment.id, line.id, {
        quantity: amount,
        reason,
        replacement_owed: replace,
      }),
    onSuccess: (next) => {
      onDone(next);
      toast.show("success", `Return of ${line.sku} recorded; held in quarantine for inspection.`);
      onClose();
    },
  });

  return (
    <Dialog
      size="sm"
      title={`Accept a return of ${line.sku}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => accept.mutate()}
            busy={accept.isPending}
            disabled={Number(amount) <= 0 || !reason.trim()}
          >
            Accept the return
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {accept.isError ? <Alert tone="error" title={describe(accept.error).message} /> : null}
        <Field label="Quantity returned">
          {(id) => (
            <Stepper
              id={id}
              min={0}
              max={Number(line.delivered)}
              value={amount}
              onChange={setAmount}
            />
          )}
        </Field>
        <Field label="Reason" required>
          {(id) => (
            <TextInput
              id={id}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Ordered in error"
            />
          )}
        </Field>
        <Checkbox
          label="A replacement is owed"
          hint="Otherwise the quantity is cancelled from the order."
          checked={replace}
          onChange={setReplace}
        />
      </div>
    </Dialog>
  );
}
