/** Devices that use the smart DNS without the VPN: each has an address of its own. */
import { create } from "zustand";

import { api, del, post } from "@/lib/api";
import { fail, toast } from "@/ui/toast";

export interface DnsDevice {
  name: string;
  /** The secret part of the device's DNS address. */
  token: string;
  /** Seconds; 0 while the device has never asked. */
  last?: number;
  queries?: number;
  /** Its requests are answered now. */
  admitted?: boolean;
}

interface DnsState {
  devices: DnsDevice[] | null;
  /** The device whose address is shown; stays while other sections are visited. */
  selected: string | null;
}

export const useDns = create<DnsState>(() => ({ devices: null, selected: null }));

const set = useDns.setState;

export const dnsUrl = (device: DnsDevice): string => location.origin + "/dns-query/" + device.token;

export async function loadDns(): Promise<void> {
  let devices: DnsDevice[];
  try {
    devices = (await api<{ devices?: DnsDevice[] }>("/api/dns")).devices ?? [];
  } catch (error) {
    fail(error);
    return;
  }
  set(state => ({ devices, selected: devices.some(device => device.name === state.selected) ? state.selected : null }));
}

export const selectDevice = (name: string | null): void => set({ selected: name });

/** Throws what the server refused. */
export async function addDevice(name: string): Promise<void> {
  await post("/api/dns", { name });
  set({ selected: name });
  await loadDns();
}

export async function revokeDevice(name: string): Promise<void> {
  if (!confirm("Отозвать адрес устройства «" + name + "»? DNS на нём перестанет работать.")) return;
  try {
    await del("/api/dns/" + encodeURIComponent(name));
    toast("Адрес отозван");
  } catch (error) {
    fail(error);
  }
  void loadDns();
}
