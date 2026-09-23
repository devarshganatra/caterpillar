import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import { useMachineStream } from "./useMachineStream";
import type { ConnectionStatus, UiPush } from "./useMachineStream";

export type { ConnectionStatus, UiPush };

interface MachineContextType {
  machineId: string | null;
  setMachineId: (id: string | null) => void;
  status: ConnectionStatus;
  snapshotLoaded: boolean;
  currentState: string;
  uiMode: "HUD" | "IDLE_HUB";
  riskLevel: string;
  riskScore: number | null;
  alerts: any[];
  envelope: any | null;
  eta: any | null;
  idleAttribution: any | null;
  anomaly: any | null;
  openIncidents: any[];
}

const MachineContext = createContext<MachineContextType | undefined>(undefined);

export function MachineProvider({ children }: { children: ReactNode }) {
  const [machineId, setMachineId] = useState<string | null>(null);
  const stream = useMachineStream(machineId);

  return (
    <MachineContext.Provider value={{ machineId, setMachineId, ...stream }}>
      {children}
    </MachineContext.Provider>
  );
}

export function useMachine() {
  const context = useContext(MachineContext);
  if (context === undefined) {
    throw new Error("useMachine must be used within a MachineProvider");
  }
  return context;
}
