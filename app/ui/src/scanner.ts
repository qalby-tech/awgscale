// The phone's camera reading a QR code (tauri-plugin-barcode-scanner; only a phone's build has it, and
// only there the join screen offers it). The camera's picture is under the page: while it reads, the page
// is transparent but for its own frame and Cancel (the plugin's "windowed" mode, which iOS and Android
// draw alike).
import { onBackButtonPress } from "@tauri-apps/api/app";
import { type PluginListener } from "@tauri-apps/api/core";
import {
  cancel,
  checkPermissions,
  Format,
  type PermissionState,
  requestPermissions,
  scan,
} from "@tauri-apps/plugin-barcode-scanner";

const GRANTED: PermissionState = "granted";

// Whether the app may use the camera; Android and iOS ask the person the first time.
export async function cameraAllowed(): Promise<boolean> {
  if ((await checkPermissions()) === GRANTED) {
    return true;
  }
  return (await requestPermissions()) === GRANTED;
}

// The text of the first QR code the camera sees. Rejects when the reading is cancelled (stopReading).
export async function readCode(): Promise<string> {
  const scanned = await scan({ windowed: true, formats: [Format.QRCode] });
  return scanned.content;
}

export async function stopReading(): Promise<void> {
  await cancel();
}

// Android's back button stops the reading instead of leaving the app; the returned listener is removed
// when the reading ends.
export function onBack(handler: () => void): Promise<PluginListener> {
  return onBackButtonPress(handler);
}
