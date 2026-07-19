import { create } from 'zustand';
import type { ConnectionConfig, ConnectionStatus, InterfaceType } from '../types/can';
import { apiConnect, apiDisconnect } from '../api/client';
import { useFrameStore } from './frameStore';

interface ConnectionStore {
  status: ConnectionStatus;
  config: ConnectionConfig;
  error: string | null;
  activeConnections: any[];

  // Actions
  setStatus: (s: ConnectionStatus) => void;
  setConfig: (patch: Partial<ConnectionConfig>) => void;
  setInterface: (iface: InterfaceType) => void;
  connect: () => Promise<void>;
  disconnect: (connId?: string) => Promise<void>;
}

export const useConnectionStore = create<ConnectionStore>((set, get) => ({
  status: 'idle',
  config: {
    interface: 'gs_usb',
    index: 0,
    bitrate: 500000,
    baudrate : 115200
  },
  error: null,
  activeConnections: [],

  setStatus: (status) => set({ status }),

  setConfig: (patch) =>
    set((s) => ({ config: { ...s.config, ...patch } })),

  setInterface: (iface) =>
    set((s) => ({
      config: {
        ...s.config,
        interface: iface,
        // Reset interface-specific fields when switching
        channel:        (iface === 'slcan' || iface === 'seeedstudio') ? (s.config.channel ?? 'COM3') : undefined,
        index:          (iface === 'gs_usb' || iface === 'kvaser') ? (s.config.index ?? 0) : undefined,
      },
    })),

  connect: async () => {
    const { config, activeConnections } = get();
    set({ status: 'connecting', error: null });
    
    // Clear stale frames only when opening the FIRST connection of the session
    if (activeConnections.length === 0) {
      useFrameStore.getState().clearFrames();
    }

    try {
      const res = await apiConnect(config);
      set({ 
        status: res.connected ? 'connected' : 'idle',
        activeConnections: res.connections || [] 
      });
    } catch (e) {
      set({ status: 'error', error: (e as Error).message });
    }
  },

  disconnect: async (connId?: string) => {
    set({ status: 'disconnecting', error: null });
    try {
      const res = await apiDisconnect(connId);
      set({
        status: res.connected ? 'connected' : 'idle',
        activeConnections: res.connections || []
      });
    } catch {
      set({ status: 'idle', activeConnections: [] });
    }
  },
}));