import { create } from 'zustand';
import {
  apiGetFlasherProtocols,
  apiParseFirmware,
  apiStartFlash,
  apiRunFlasherAction,
  apiAbortFlash,
  apiClearFlasherLogs,
  getFlasherWsUrl,
} from '../api/client';
import type {
  FlashingProtocolInfo,
  FirmwareParsedInfo,
  FlasherStatus,
  FlasherLogEntry,
} from '../types/can';

let ws: WebSocket | null = null;

interface FlasherStoreState {
  protocols: FlashingProtocolInfo[];
  selectedProtocolId: string;
  loadingProtocols: boolean;

  // Target Settings
  txId: string;
  rxId: string;
  isExtended: boolean;
  isFd: boolean;
  bitrateSwitch: boolean;
  baseAddress: string;
  chunkSize: number;
  protocolOptions: Record<string, any>;

  // Firmware File
  firmwareInfo: FirmwareParsedInfo | null;
  parsingFirmware: boolean;

  // Execution Telemetry
  status: FlasherStatus;
  logs: FlasherLogEntry[];

  // Actions
  loadProtocols: () => Promise<void>;
  selectProtocol: (id: string) => void;
  setTargetConfig: (patch: Partial<{
    txId: string;
    rxId: string;
    isExtended: boolean;
    isFd: boolean;
    bitrateSwitch: boolean;
    baseAddress: string;
    chunkSize: number;
  }>) => void;
  setProtocolOption: (key: string, value: any) => void;
  parseFirmwareFile: (file: File) => Promise<void>;
  clearFirmware: () => void;
  startFlash: () => Promise<void>;
  runAction: (action: 'erase' | 'verify' | 'reset_ecu') => Promise<void>;
  abortFlash: () => Promise<void>;
  clearLogs: () => Promise<void>;
  connectWebSocket: () => void;
  disconnectWebSocket: () => void;
}

const defaultStatus: FlasherStatus = {
  status: 'idle',
  stage: 'IDLE',
  progress: 0,
  bytes_transferred: 0,
  total_bytes: 0,
  speed_kbps: 0,
  eta_seconds: 0,
  error: null,
  active: false,
};

export const useFlasherStore = create<FlasherStoreState>((set, get) => ({
  protocols: [],
  selectedProtocolId: '',
  loadingProtocols: false,

  txId: '0x7E0',
  rxId: '0x7E8',
  isExtended: false,
  isFd: true,
  bitrateSwitch: true,
  baseAddress: '0x08000000',
  chunkSize: 64,
  protocolOptions: {},

  firmwareInfo: null,
  parsingFirmware: false,

  status: { ...defaultStatus },
  logs: [],

  loadProtocols: async () => {
    set({ loadingProtocols: true });
    try {
      const res = await apiGetFlasherProtocols();
      const protos = res.protocols || [];
      const currentId = get().selectedProtocolId;
      const selected = protos.find((p) => p.id === currentId) || protos[0];

      const initialOpts: Record<string, any> = {};
      if (selected) {
        for (const opt of selected.options) {
          initialOpts[opt.key] = opt.default;
        }
      }

      set({
        protocols: protos,
        selectedProtocolId: selected ? selected.id : '',
        txId: selected ? selected.default_tx_id : '0x7E0',
        rxId: selected ? selected.default_rx_id : '0x7E8',
        isExtended: selected ? selected.is_extended_id : false,
        isFd: selected ? selected.supports_fd : true,
        chunkSize: selected ? selected.default_chunk_size : 64,
        protocolOptions: initialOpts,
        loadingProtocols: false,
      });
    } catch (e) {
      set({ loadingProtocols: false });
    }
  },

  selectProtocol: (id: string) => {
    const proto = get().protocols.find((p) => p.id === id);
    if (!proto) return;

    const initialOpts: Record<string, any> = {};
    for (const opt of proto.options) {
      initialOpts[opt.key] = opt.default;
    }

    set({
      selectedProtocolId: id,
      txId: proto.default_tx_id,
      rxId: proto.default_rx_id,
      isExtended: proto.is_extended_id,
      isFd: proto.supports_fd,
      chunkSize: proto.default_chunk_size,
      protocolOptions: initialOpts,
    });
  },

  setTargetConfig: (patch) => {
    set((state) => ({ ...state, ...patch }));
  },

  setProtocolOption: (key: string, value: any) => {
    set((state) => ({
      protocolOptions: {
        ...state.protocolOptions,
        [key]: value,
      },
    }));
  },

  parseFirmwareFile: async (file: File) => {
    set({ parsingFirmware: true });
    try {
      const parsed = await apiParseFirmware(file);
      set({
        firmwareInfo: parsed,
        baseAddress: parsed.base_address,
        parsingFirmware: false,
      });
    } catch (e) {
      set({ parsingFirmware: false });
      throw e;
    }
  },

  clearFirmware: () => set({ firmwareInfo: null }),

  startFlash: async () => {
    const {
      selectedProtocolId,
      firmwareInfo,
      txId,
      rxId,
      baseAddress,
      isExtended,
      isFd,
      bitrateSwitch,
      chunkSize,
      protocolOptions,
    } = get();

    if (!selectedProtocolId) throw new Error('No protocol selected.');
    if (!firmwareInfo) throw new Error('No firmware file loaded. Please choose a .bin, .hex, or .srec file.');

    get().connectWebSocket();

    set({
      status: {
        ...defaultStatus,
        status: 'connecting',
        stage: 'STARTING',
        total_bytes: firmwareInfo.total_bytes,
        active: true,
      },
    });

    await apiStartFlash({
      protocol_id: selectedProtocolId,
      firmware_base64: firmwareInfo.firmware_base64,
      tx_id: txId,
      rx_id: rxId,
      base_address: baseAddress,
      is_extended_id: isExtended,
      is_fd: isFd,
      bitrate_switch: bitrateSwitch,
      chunk_size: chunkSize,
      options: protocolOptions,
    });
  },

  runAction: async (action: 'erase' | 'verify' | 'reset_ecu') => {
    const {
      selectedProtocolId,
      firmwareInfo,
      txId,
      rxId,
      baseAddress,
      isExtended,
      isFd,
      bitrateSwitch,
      protocolOptions,
    } = get();

    if (!selectedProtocolId) throw new Error('No protocol selected.');

    get().connectWebSocket();

    set({
      status: {
        ...defaultStatus,
        status: 'running',
        stage: action.toUpperCase(),
        active: true,
      },
    });

    await apiRunFlasherAction({
      action,
      protocol_id: selectedProtocolId,
      tx_id: txId,
      rx_id: rxId,
      base_address: baseAddress,
      is_extended_id: isExtended,
      is_fd: isFd,
      bitrate_switch: bitrateSwitch,
      options: protocolOptions,
    });
  },

  abortFlash: async () => {
    await apiAbortFlash();
  },

  clearLogs: async () => {
    await apiClearFlasherLogs();
    set({ logs: [] });
  },

  connectWebSocket: () => {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      ws = new WebSocket(getFlasherWsUrl());

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'init') {
            set({
              status: msg.status || get().status,
              logs: msg.logs || [],
            });
          } else if (msg.type === 'progress') {
            set((state) => ({
              status: {
                ...state.status,
                progress: msg.progress,
                bytes_transferred: msg.bytes_transferred,
                total_bytes: msg.total_bytes,
                speed_kbps: msg.speed_kbps,
                eta_seconds: msg.eta_seconds,
                stage: msg.stage,
                active: true,
              },
            }));
          } else if (msg.type === 'stage') {
            set((state) => ({
              status: { ...state.status, stage: msg.stage },
            }));
          } else if (msg.type === 'log') {
            set((state) => ({
              logs: [...state.logs.slice(-4999), {
                timestamp: msg.timestamp,
                level: msg.level,
                message: msg.message,
              }],
            }));
          } else if (msg.type === 'complete') {
            set((state) => ({
              status: {
                ...state.status,
                status: 'completed',
                stage: 'COMPLETED',
                progress: 100,
                active: false,
              },
            }));
          } else if (msg.type === 'aborted') {
            set((state) => ({
              status: {
                ...state.status,
                status: 'aborted',
                stage: 'ABORTED',
                active: false,
              },
            }));
          } else if (msg.type === 'error') {
            set((state) => ({
              status: {
                ...state.status,
                status: 'error',
                stage: 'ERROR',
                error: msg.error,
                active: false,
              },
            }));
          } else if (msg.type === 'clear_logs') {
            set({ logs: [] });
          }
        } catch { /* ignore */ }
      };

      ws.onerror = () => {};
      ws.onclose = () => {
        ws = null;
      };
    } catch { /* ignore */ }
  },

  disconnectWebSocket: () => {
    if (ws) {
      ws.close();
      ws = null;
    }
  },
}));
