import type { ConnectionConfig, ApiStatus } from '../types/can';

// In dev: Vite proxies /api/* → localhost:8080/* (stripping /api prefix)
// In prod: FastAPI serves at same origin with no prefix — use empty base
const BASE = import.meta.env.DEV ? '/api' : '';

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? 'Request failed');
  }
  return res.json() as Promise<T>;
}

// ============================================================
// Connection
// ============================================================

export interface ConnectionStatusResponse {
  connected: boolean;
  interface: string;
  channel: string;
  bitrate: number;
  index: number;
  fd?: boolean;
  data_bitrate?: number;
  error: string | null;
}

export function apiConnect(config: ConnectionConfig) {
  const body = {
    interface:        config.interface,
    channel:          config.channel ?? '',
    bitrate:          config.bitrate,
    baudrate:         config.baudrate,
    index:            config.index ?? 0,
    fd:               config.fd ?? false,
    data_bitrate:     config.data_bitrate ?? 2000000,
  };

  return request<ConnectionStatusResponse>('/connect', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function apiDisconnect() {
  return request<ConnectionStatusResponse>('/disconnect', { method: 'POST' });
}

export function apiGetStatus() {
  return request<ApiStatus>('/status');
}

// ============================================================
// Send frame
// ============================================================

export interface SendFramePayload {
  id: number;
  dlc: number;
  data: number[];
  is_extended_id: boolean;
  is_fd?: boolean;
  bitrate_switch?: boolean;
}

export function apiSendFrame(payload: SendFramePayload) {
  return request<{ message: string; ok: boolean; id: string; data: number[]; is_fd?: boolean; bitrate_switch?: boolean }>('/send', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ============================================================
// DBC
// ============================================================

export async function apiLoadDbc(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/dbc/load`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? 'DBC upload failed');
  }
  return res.json();
}

export function apiGetDbcMessages() {
  return request<{ messages: any[] }>('/dbc/messages');
}

export function apiEncodeDbcMessage(messageId: number, signals: Record<string, number>) {
  return request<{ data: number[]; dlc: number }>('/dbc/encode', {
    method: 'POST',
    body: JSON.stringify({ message_id: messageId, signals }),
  });
}

// ============================================================
// Logging
// ============================================================

export interface LogStartResponse {
  ok: boolean;
  base: string;   // e.g. "canvaz_20260412_095000"
}

export interface LogStopResponse {
  ok: boolean;
  frames: number;
  asc_file: string;  // e.g. "logs/canvaz_20260412_095000.asc"
  csv_file: string;
  mf4_file?: string; // e.g. "logs/canvaz_20260412_095000.mf4"
}

export function apiLogStart() {
  return request<LogStartResponse>('/log/start', { method: 'POST' });
}

export function apiLogStop() {
  // Backend uses a global session — no body needed
  return request<LogStopResponse>('/log/stop', { method: 'POST' });
}

export function getLogDownloadUrl(filename: string) {
  // filename = basename only, e.g. "canvaz_20260412_095000.asc"
  return `${BASE}/log/download/${filename}`;
}

// ============================================================
// Replay
// ============================================================

export interface ReplayStartPayload {
  filename: string;
  speed: number;
}

export function apiReplayStart(payload: ReplayStartPayload) {
  return request<{ message: string }>('/replay/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function apiReplayStop() {
  return request<{ message: string }>('/replay/stop', { method: 'POST' });
}

export function apiReplayPause() {
  return request<{ message: string }>('/replay/pause', { method: 'POST' });
}

export function apiReplayResume() {
  return request<{ message: string }>('/replay/resume', { method: 'POST' });
}

export async function apiUploadReplayFile(file: File): Promise<string> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/replay/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? 'Upload failed');
  }
  const data = await res.json();
  return data.filename as string;
}

// ============================================================
// Universal CAN Flasher
// ============================================================

import type { FlashingProtocolInfo, FirmwareParsedInfo, FlasherStatus, FlasherLogEntry } from '../types/can';

export function apiGetFlasherProtocols() {
  return request<{ protocols: FlashingProtocolInfo[] }>('/flasher/protocols');
}

export function apiGetFlasherTemplate() {
  return request<{ code: string }>('/flasher/protocols/template');
}

export function apiGetProtocolCode(protocolId: string) {
  return request<{ id: string; code: string }>(`/flasher/protocols/${protocolId}/code`);
}

export async function apiUploadProtocolFile(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/flasher/protocols/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? 'Protocol upload failed');
  }
  return res.json();
}

export function apiSubmitProtocolCode(filename: string, code: string) {
  return request<{ ok: boolean; message: string; registered: string[] }>('/flasher/protocols/submit_code', {
    method: 'POST',
    body: JSON.stringify({ filename, code }),
  });
}

export async function apiParseFirmware(file: File): Promise<FirmwareParsedInfo> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/flasher/firmware/parse`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? 'Firmware parse failed');
  }
  return res.json() as Promise<FirmwareParsedInfo>;
}

export interface StartFlashPayload {
  protocol_id: string;
  firmware_base64: string;
  tx_id: string | number;
  rx_id: string | number;
  base_address: string | number;
  is_extended_id: boolean;
  is_fd: boolean;
  bitrate_switch: boolean;
  chunk_size: number;
  options: Record<string, any>;
}

export function apiStartFlash(payload: StartFlashPayload) {
  return request<{ ok: boolean; message: string }>('/flasher/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export interface FlasherActionPayload {
  action: 'erase' | 'verify' | 'reset_ecu';
  protocol_id: string;
  tx_id: string | number;
  rx_id: string | number;
  base_address: string | number;
  is_extended_id: boolean;
  is_fd: boolean;
  bitrate_switch: boolean;
  options: Record<string, any>;
}

export function apiRunFlasherAction(payload: FlasherActionPayload) {
  return request<{ ok: boolean; message: string }>('/flasher/action', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function apiAbortFlash() {
  return request<{ ok: boolean; message: string }>('/flasher/abort', {
    method: 'POST',
  });
}

export function apiGetFlasherStatus() {
  return request<FlasherStatus>('/flasher/status');
}

export function apiGetFlasherLogs() {
  return request<{ logs: FlasherLogEntry[] }>('/flasher/logs');
}

export function apiClearFlasherLogs() {
  return request<{ ok: boolean }>('/flasher/logs/clear', {
    method: 'POST',
  });
}

export function getFlasherWsUrl(): string {
  const loc = window.location;
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = import.meta.env.DEV ? 'localhost:8080' : loc.host;
  return `${proto}//${host}/flasher/ws`;
}