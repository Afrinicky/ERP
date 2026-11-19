/**
 * One shipment: where it stands, what it carries, the documents issued for it, and the
 * proof of delivery. The next step is always the one large action in the footer.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { DocumentLink, Shipment } from "@/api/types";
import {
  Alert,
  Button,
  Details,
  Dialog,
  Field,
  Skeleton,
  StatusChip,
  Stepper,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, dateTime, quantity } from "@/lib/format";
import { useSession } from "@/lib/session";

import { STATE_TONE } from "./Dispatch";
import { DeliveryDialog, DispatchDetailsDialog, ReturnDialog } from "./Steps";

type DocumentKindName = "pick_list" | "packing_list" | "waybill" | "delivery_note";

const DOCUMENTS: { kind: DocumentKindName; label: string; after: Shipment["state"][] }[] = [
  { kind: "pick_list", label: "Pick list", after: ["prepared", "packed", "dispatched"] },
  { kind: "packing_list", label: "Packing list", after: ["packed", "dispatched"] },
  { kind: "waybill", label: "Waybill", after: ["dispatched"] },
  { kind: "delivery_note", label: "Delivery note", after: ["dispatched"] },
];

const JOURNEY: { label: string; reached: (shipment: Shipment) => boolean }[] = [
  { label: "Prepared", reached: () => true },
  { label: "Packed", reached: (shipment) => shipment.packed_on !== null },
  { label: "Dispatched", reached: (shipment) => shipment.dispatched_on !== null },
  { label: "Delivered", reached: (shipment) => shipment.delivered_on !== null },
];

export function ShipmentDialog({
  shipmentId,
  onClose,
}: {
  shipmentId: string;
  onClose: () => void;
}) {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();
  const [packages, setPackages] = useState("1");
  const [step, setStep] = useState<"dispatch" | "deliver" | null>(null);
  const [returning, setReturning] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");

  const shipment = useQuery({
    queryKey: ["dispatch", "shipment", shipmentId],
    queryFn: () => api.dispatch.shipment(shipmentId),
  });

  const refresh = (next: Shipment) => {
    client.setQueryData(["dispatch", "shipment", shipmentId], next);
    void client.invalidateQueries({ queryKey: ["dispatch", "shipments"] });
    void client.invalidateQueries({ queryKey: ["inventory"] });
  };

  const pack = useMutation({
    mutationFn: () => api.dispatch.pack(shipmentId, Number(packages)),
    onSuccess: (next) => {
      refresh(next);
      toast.show("success", `${next.number} packed: ${next.package_count} packages.`);
    },
  });
  const cancel = useMutation({
    mutationFn: () => api.dispatch.cancel(shipmentId, reason),
    onSuccess: (next) => {
      refresh(next);
      setCancelling(false);
      toast.show("success", `${next.number} withdrawn.`);
    },
  });
  const issue = useMutation({
    mutationFn: async (kind: DocumentKindName) => {
      const issued = await api.dispatch.issueDocument(shipmentId, kind);
      const url = await api.documents.file(issued.id);
      window.open(url, "_blank", "noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      return issued;
    },
    onSuccess: () => void shipment.refetch(),
    onError: (error) => toast.show("error", describe(error).message),
  });

  const data = shipment.data;
  const failure = pack.error ?? cancel.error;

  return (
    <>
      <Dialog
        size="lg"
        title={data ? `Shipment ${data.number}` : "Shipment"}
        onClose={onClose}
        footer={data ? <Footer /> : undefined}
      >
        {!data ? (
          <Skeleton rows={6} />
        ) : (
          <div className="stack gap-5">
            {failure ? <Alert tone="error" title={describe(failure).message} /> : null}
            <ol className="journey" aria-label="Progress">
              {JOURNEY.map((stage) => (
                <li
                  key={stage.label}
                  className={`journey__stage${stage.reached(data) ? " journey__stage--done" : ""}`}
                >
                  {stage.label}
                </li>
              ))}
            </ol>
            <div className="row gap-3 wrap">
              <StatusChip tone={STATE_TONE[data.state]}>{data.state_label}</StatusChip>
              <span className="caption">
                {data.order_number} · {data.customer} · from {data.warehouse}
              </span>
            </div>

            <Details
              rows={[
                ["Deliver to", data.destination],
                [
                  "Carriage",
                  data.dispatched_on
                    ? [data.mode_label, data.carrier, data.driver, data.vehicle]
                        .filter(Boolean)
                        .join(" · ")
                    : null,
                ],
                ["Packages", data.package_count ? String(data.package_count) : null],
                ["Tracking reference", data.tracking_reference],
                ["Dispatched", data.dispatched_on ? formatDate(data.dispatched_on) : null],
              ]}
            />

            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th style={{ width: "11%" }} className="numeric">
                      Shipped
                    </th>
                    <th style={{ width: "11%" }} className="numeric">
                      Delivered
                    </th>
                    <th style={{ width: "24%" }}>Exceptions</th>
                    {data.state === "delivered" || data.state === "partially_delivered" ? (
                      <th style={{ width: "13%" }} />
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((line) => (
                    <tr key={line.id}>
                      <td>
                        <span className="cell-strong">{line.description}</span>
                        <span className="cell-note">
                          {line.sku}
                          {line.serials.length ? ` · ${line.serials.join(", ")}` : ""}
                        </span>
                      </td>
                      <td className="numeric">{quantity(line.quantity)}</td>
                      <td className="numeric">
                        {data.delivered_on ? quantity(line.delivered) : "—"}
                      </td>
                      <td>
                        {data.exceptions
                          .filter((entry) => entry.line_position === line.position)
                          .map((entry) => `${entry.kind_label} ${quantity(entry.quantity)}`)
                          .join("; ") || "—"}
                      </td>
                      {data.state === "delivered" || data.state === "partially_delivered" ? (
                        <td className="table__actions">
                          {can("sales_return.manage") && Number(line.delivered) > 0 ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => setReturning(line.id)}
                            >
                              Accept return
                            </Button>
                          ) : null}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {data.exceptions.length ? (
              <div className="stack gap-2">
                {data.exceptions.map((entry) => (
                  <p className="caption" key={entry.id}>
                    {formatDate(entry.occurred_on)} · Line {entry.line_position} ·{" "}
                    {entry.kind_label} {quantity(entry.quantity)}: {entry.reason}
                    {entry.kind === "customer_return"
                      ? entry.replacement_owed
                        ? " · replacement owed"
                        : " · cancelled from the order"
                      : ""}
                  </p>
                ))}
              </div>
            ) : null}

            <section className="stack gap-3">
              <h4 className="section-label">Documents</h4>
              <div className="row gap-3 wrap">
                {DOCUMENTS.map((entry) => {
                  const issued = data[entry.kind] as DocumentLink | null;
                  const available = entry.after.includes(data.state) || issued !== null;
                  return (
                    <Button
                      key={entry.kind}
                      tone="secondary"
                      size="touch"
                      disabled={!available || !can("waybill.issue")}
                      busy={issue.isPending && issue.variables === entry.kind}
                      onClick={() => issue.mutate(entry.kind)}
                    >
                      {issued
                        ? `${entry.label} ${issued.number}`
                        : `Issue ${entry.label.toLowerCase()}`}
                    </Button>
                  );
                })}
              </div>
            </section>

            {data.proof ? (
              <section className="stack gap-2">
                <h4 className="section-label">Proof of delivery</h4>
                <Details
                  rows={[
                    [
                      "Received by",
                      `${data.proof.recipient_name}${data.proof.recipient_role ? `, ${data.proof.recipient_role}` : ""}`,
                    ],
                    ["Received", dateTime(data.proof.acknowledged_at)],
                    ["Place", data.proof.location],
                    [
                      "Evidence",
                      data.proof.has_evidence
                        ? `${data.proof.evidence_kind === "signature" ? "Signature" : "Scanned copy"} on the waybill`
                        : "Recorded without an image",
                    ],
                  ]}
                />
              </section>
            ) : null}

            {cancelling ? (
              <Field label="Reason for withdrawing the shipment" required>
                {(id) => (
                  <TextInput
                    id={id}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Customer asked us to hold delivery"
                  />
                )}
              </Field>
            ) : null}
          </div>
        )}
      </Dialog>
      {data && step === "dispatch" ? (
        <DispatchDetailsDialog shipment={data} onClose={() => setStep(null)} onDone={refresh} />
      ) : null}
      {data && step === "deliver" ? (
        <DeliveryDialog shipment={data} onClose={() => setStep(null)} onDone={refresh} />
      ) : null}
      {data && returning ? (
        <ReturnDialog
          shipment={data}
          lineId={returning}
          onClose={() => setReturning(null)}
          onDone={refresh}
        />
      ) : null}
    </>
  );

  function Footer() {
    if (!data) return null;
    const manage = can("dispatch.manage");
    const withdraw =
      manage && (data.state === "prepared" || data.state === "packed") ? (
        cancelling ? (
          <Button
            tone="destructive"
            onClick={() => cancel.mutate()}
            busy={cancel.isPending}
            disabled={!reason.trim()}
          >
            Withdraw
          </Button>
        ) : (
          <Button tone="plain" onClick={() => setCancelling(true)}>
            Withdraw shipment
          </Button>
        )
      ) : null;
    return (
      <>
        <Button tone="plain" onClick={onClose}>
          Close
        </Button>
        {withdraw}
        {manage && data.state === "prepared" ? (
          <div className="row gap-3">
            <Stepper
              label="Packages"
              min={1}
              value={packages}
              onChange={setPackages}
              unit="packages"
            />
            <Button
              tone="primary"
              size="transactional"
              onClick={() => pack.mutate()}
              busy={pack.isPending}
              disabled={Number(packages) < 1}
            >
              Stage and pack
            </Button>
          </div>
        ) : null}
        {manage && data.state === "packed" ? (
          <Button tone="primary" size="transactional" onClick={() => setStep("dispatch")}>
            Dispatch
          </Button>
        ) : null}
        {can("proof_of_delivery.capture") && data.state === "dispatched" ? (
          <Button tone="primary" size="transactional" onClick={() => setStep("deliver")}>
            Confirm delivery
          </Button>
        ) : null}
      </>
    );
  }
}
