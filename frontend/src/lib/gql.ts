import { gql } from "@apollo/client";

const SAGA_FIELDS = `id status fleetId stationId connectorId kwh amountCents createdAt steps { name status at detail }`;

export const CONSOLE = gql`
  query Console($fleetId: ID!) {
    health { env version services { service status version } }
    stations { id name city connectors { id stationId type powerKw status sagaId } }
    flags { name enabled description updatedAt }
    sagas(limit: 20) { ${SAGA_FIELDS} }
    wallet(fleetId: $fleetId) { fleetId balanceCents transactions { sagaId kind amountCents createdAt } }
  }
`;

export const SAGA = gql`
  query Saga($id: ID!) {
    saga(id: $id) { ${SAGA_FIELDS} }
  }
`;

export const SAGA_WALLET = gql`
  query SagaWallet($fleetId: ID!) {
    wallet(fleetId: $fleetId) { fleetId balanceCents transactions { sagaId kind amountCents createdAt } }
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

export type Step = { name: string; status: string; at: string; detail: string | null };
export type Saga = {
  id: string; status: string; fleetId: string; stationId: string; connectorId: string;
  kwh: number; amountCents: number | null; createdAt: string; steps: Step[];
};
export type Connector = { id: string; stationId: string; type: "FAST" | "ULTRA"; powerKw: number; status: string; sagaId: string | null };
export type Station = { id: string; name: string; city: string; connectors: Connector[] };
export type Flag = { name: string; enabled: boolean; description: string | null; updatedAt: string | null };
export type Txn = { sagaId: string | null; kind: string; amountCents: number; createdAt: string };
export type Wallet = { fleetId: string; balanceCents: number; transactions: Txn[] };
export type Health = { env: string; version: string; services: { service: string; status: string; version: string }[] };

export const FLEETS = ["FLEET-ANDINA", "FLEET-LOWFUNDS"];
export const TERMINAL = new Set(["COMPLETED", "FAILED", "COMPENSATED", "COMPENSATION_FAILED", "STOPPED"]);

export const HEALTH = gql`
  query Health { health { env version services { service status version } } }
`;
