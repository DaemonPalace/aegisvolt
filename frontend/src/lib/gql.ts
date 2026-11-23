import { gql } from "@apollo/client";

const SAGA_FIELDS = `id status fleetId stationId connectorId kwh amountCents createdAt powerKw endsAt steps { name status at detail }`;
const STATION_FIELDS = `id name city connectors { id stationId type powerKw status sagaId note statusSince }`;
const WALLET_FIELDS = `fleetId balanceCents transactions { sagaId kind amountCents createdAt }`;

/** The charge screen: stations to pick from, the price inputs, the flags, and the fleet's sagas and wallet. */
export const CHARGE = gql`
  query Charge($fleetId: ID!) {
    stations { ${STATION_FIELDS} }
    flags { name enabled description updatedAt }
    pricing { rateCentsPerKwh surgeMultiplier surge }
    sagas(limit: 100) { ${SAGA_FIELDS} }
    wallet(fleetId: $fleetId) { ${WALLET_FIELDS} }
  }
`;

/** Transactions tab and the toasts: sagas (filtered to the fleet client-side) plus the fleet wallet. */
export const SAGAS = gql`
  query Sagas($fleetId: ID!) {
    sagas(limit: 100) { ${SAGA_FIELDS} }
    wallet(fleetId: $fleetId) { ${WALLET_FIELDS} }
  }
`;

export const SAGA = gql`
  query Saga($id: ID!) {
    saga(id: $id) { ${SAGA_FIELDS} }
  }
`;

export const SAGA_WALLET = gql`
  query SagaWallet($fleetId: ID!) {
    wallet(fleetId: $fleetId) { ${WALLET_FIELDS} }
  }
`;

export const TELEMETRY = gql`
  query Telemetry($stationId: ID!) {
    telemetry(stationId: $stationId) { connectorId sagaId ts powerKw energyKwh voltage }
  }
`;

export const START_CHARGE = gql`
  mutation StartCharge($input: StartChargeInput!) {
    startCharge(input: $input) { ${SAGA_FIELDS} }
  }
`;

export const STOP_CHARGE = gql`
  mutation StopCharge($sagaId: ID!) {
    stopCharge(sagaId: $sagaId) { ${SAGA_FIELDS} }
  }
`;

export const SET_FLAG = gql`
  mutation SetFlag($name: String!, $enabled: Boolean!) {
    setFlag(name: $name, enabled: $enabled) { name enabled description updatedAt }
  }
`;

/** Station owner screen: the station's connectors, today's sessions with kWh delivered, live samples. */
export const OWNER = gql`
  query Owner($stationId: ID!, $since: String!) {
    stations { ${STATION_FIELDS} }
    stationSessions(stationId: $stationId, since: $since) { sagaId connectorId fleetId status startedAt endedAt kwhRequested kwhDelivered powerKw }
    telemetry(stationId: $stationId) { connectorId sagaId ts powerKw energyKwh voltage }
  }
`;

export const LIFECYCLE = gql`
  mutation Lifecycle($stationId: ID!, $connectorId: ID!, $action: String!, $note: String!) {
    connectorLifecycle(stationId: $stationId, connectorId: $connectorId, action: $action, note: $note) { ${STATION_FIELDS} }
  }
`;

export const ADD_CONNECTOR = gql`
  mutation AddConnector($stationId: ID!, $type: ConnectorType!) {
    addConnector(stationId: $stationId, type: $type) { ${STATION_FIELDS} }
  }
`;

export const REMOVE_CONNECTOR = gql`
  mutation RemoveConnector($stationId: ID!, $connectorId: ID!) {
    removeConnector(stationId: $stationId, connectorId: $connectorId) { ${STATION_FIELDS} }
  }
`;

export const HEALTH = gql`
  query Health { health { env version services { service status version } } }
`;

export type Step = { name: string; status: string; at: string; detail: string | null };
export type Saga = {
  id: string; status: string; fleetId: string; stationId: string; connectorId: string;
  kwh: number; amountCents: number | null; createdAt: string; powerKw: number | null; endsAt: string | null; steps: Step[];
};
export type Connector = {
  id: string; stationId: string; type: "FAST" | "ULTRA"; powerKw: number; status: string; sagaId: string | null;
  note: string | null; statusSince: string | null;
};
export type Session = {
  sagaId: string; connectorId: string; fleetId: string | null; status: string; startedAt: string; endedAt: string | null;
  kwhRequested: number; kwhDelivered: number; powerKw: number;
};
export type Station = { id: string; name: string; city: string; connectors: Connector[] };
export type Flag = { name: string; enabled: boolean; description: string | null; updatedAt: string | null };
export type Pricing = { rateCentsPerKwh: number; surgeMultiplier: number; surge: boolean };
export type Txn = { sagaId: string | null; kind: string; amountCents: number; createdAt: string };
export type Wallet = { fleetId: string; balanceCents: number; transactions: Txn[] };
export type Health = { env: string; version: string; services: { service: string; status: string; version: string }[] };
export type Point = { connectorId: string; sagaId: string; ts: string; powerKw: number; energyKwh: number; voltage: number };

export const FLEETS = gql`
  query Fleets { fleets { fleetId balanceCents } }
`;

export const CREATE_FLEET = gql`
  mutation CreateFleet($fleetId: ID!, $initialCents: Int!) {
    createFleet(fleetId: $fleetId, initialCents: $initialCents) { fleetId balanceCents }
  }
`;
/** COMPLETED is the charging phase (energy flowing); it ends FINISHED (auto) or STOPPED (by hand). */
export const TERMINAL = new Set(["FAILED", "COMPENSATED", "COMPENSATION_FAILED", "STOPPED", "FINISHED"]);
export const LIVE = new Set(["STARTED", "COMPLETED"]);

/** Same formula as billing price(): ceil(kWh × rate × surge). toFixed trims float noise (0.1 × 25 × 1.5). */
export const quote = (kwh: number, p: Pricing) =>
  Math.ceil(Number((kwh * p.rateCentsPerKwh * (p.surge ? p.surgeMultiplier : 1)).toFixed(6)));

/** 0..1 share of the charge done, by the dispatch clock (activate step → endsAt). */
export function progress(s: Saga, now: number): number {
  const from = s.steps.find((x) => x.name === "activate" && x.status === "OK")?.at;
  if (s.status === "FINISHED") return 1;
  if (!from || !s.endsAt) return 0;
  const a = Date.parse(from), b = Date.parse(s.endsAt);
  return b > a ? Math.min(1, Math.max(0, (now - a) / (b - a))) : 1;
}

export const TOP_UP = gql`
  mutation TopUp($fleetId: ID!, $amountCents: Int!) {
    topUp(fleetId: $fleetId, amountCents: $amountCents) { fleetId balanceCents transactions { sagaId kind amountCents createdAt } }
  }
`;
