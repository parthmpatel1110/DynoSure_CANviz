import { useState, useEffect, useRef } from 'react';
import { useConnectionStore } from '../../store/connectionStore';
import { useFlasherStore } from '../../store/flasherStore';
import {
  apiGetProtocolCode,
  apiGetProtocolTemplate,
  apiUploadProtocolFile,
} from '../../api/client';

const CHUNK_SIZES = [8, 16, 32, 64, 128, 256, 512, 1024, 2048];

export function FlasherPanel() {
  const isConnected = useConnectionStore((s) => s.status === 'connected');

  const {
    protocols,
    selectedProtocolId,
    loadingProtocols,
    txId,
    rxId,
    isExtended,
    isFd,
    bitrateSwitch,
    baseAddress,
    chunkSize,
    protocolOptions,
    firmwareInfo,
    parsingFirmware,
    status,
    logs,
    loadProtocols,
    selectProtocol,
    setTargetConfig,
    setProtocolOption,
    parseFirmwareFile,
    clearFirmware,
    startFlash,
    runAction,
    abortFlash,
    clearLogs,
    connectWebSocket,
  } = useFlasherStore();

  const [modalOpen, setModalOpen] = useState(false);
  const [modalTab, setModalTab] = useState<'list' | 'upload' | 'template'>('list');
  const [viewingCode, setViewingCode] = useState<{ id: string; code: string } | null>(null);
  const [templateCode, setTemplateCode] = useState<string>('');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const protoFileInputRef = useRef<HTMLInputElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Load protocols and connect WS on mount
  useEffect(() => {
    loadProtocols();
    connectWebSocket();
  }, [loadProtocols, connectWebSocket]);

  // Autoscroll logs
  useEffect(() => {
    if (autoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const selectedProto = protocols.find((p) => p.id === selectedProtocolId);

  const handleFirmwareFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      try {
        await parseFirmwareFile(file);
      } catch (err: any) {
        alert(err.message || 'Failed to parse firmware file.');
      }
    }
    e.target.value = '';
  };

  const handleFirmwareDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      try {
        await parseFirmwareFile(file);
      } catch (err: any) {
        alert(err.message || 'Failed to parse firmware file.');
      }
    }
  };

  const handleProtoUpload = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);
    try {
      const res = await apiUploadProtocolFile(file);
      setUploadSuccess(`Protocol registered successfully: ${res.registered.join(', ')}`);
      await loadProtocols();
      if (res.registered.length > 0) {
        selectProtocol(res.registered[0]);
      }
    } catch (err: any) {
      setUploadError(err.message || 'Failed to upload protocol.');
    } finally {
      setUploading(false);
    }
  };

  const openViewCode = async (protoId: string) => {
    try {
      const res = await apiGetProtocolCode(protoId);
      setViewingCode(res);
    } catch (err: any) {
      alert('Failed to load code: ' + err.message);
    }
  };

  const loadTemplate = async () => {
    try {
      const res = await apiGetProtocolTemplate();
      setTemplateCode(res.code);
    } catch (err: any) {
      alert('Failed to load template: ' + err.message);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const downloadFile = (filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={styles.container}>
      {/* Top Bar: Protocol Selector & Mode Info */}
      <div style={styles.topBar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1 }}>
          <span style={styles.sectionLabel}>FLASHING PROTOCOL:</span>
          <select
            style={styles.select}
            value={selectedProtocolId}
            disabled={status.active || loadingProtocols}
            onChange={(e) => selectProtocol(e.target.value)}
          >
            {protocols.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.version ? ` (v${p.version})` : ''}{p.author ? ` - ${p.author}` : ''}
              </option>
            ))}
          </select>

          {selectedProto && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              {selectedProto.supports_fd && (
                <span className="badge badge-blue" title="Protocol supports high-speed CAN FD frames">
                  CAN FD
                </span>
              )}
              {selectedProto.is_custom && (
                <span className="badge badge-amber" title="Custom user-defined Python protocol plugin">
                  User Plugin
                </span>
              )}
            </div>
          )}
        </div>

        <button
          className="btn btn-ghost btn-sm"
          style={{ display: 'flex', alignItems: 'center', gap: 5 }}
          onClick={() => {
            setModalOpen(true);
            loadTemplate();
          }}
        >
          <span>⚙ Manage Protocols</span>
        </button>
      </div>

      {/* Main 3-Column Content Layout */}
      <div style={styles.threeColumnGrid}>

        {/* ===================================================================
            COLUMN 1: Target CAN Configuration & Protocol Parameters
            =================================================================== */}
        <div style={styles.columnCard}>
          <div style={styles.cardHeader}>
            <span style={styles.cardTitle}>1. Target CAN Bus Settings</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 8 }}>
            {/* CAN IDs: Tx & Rx */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <label style={styles.fieldLabel}>Tx CAN ID</label>
                <input
                  style={styles.input}
                  value={txId}
                  disabled={status.active}
                  onChange={(e) => setTargetConfig({ txId: e.target.value })}
                  placeholder="0x7E0"
                />
              </div>
              <div>
                <label style={styles.fieldLabel}>Rx CAN ID</label>
                <input
                  style={styles.input}
                  value={rxId}
                  disabled={status.active}
                  onChange={(e) => setTargetConfig({ rxId: e.target.value })}
                  placeholder="0x7E8"
                />
              </div>
            </div>

            {/* Base Flash Memory Address */}
            <div>
              <label style={styles.fieldLabel}>Base Address (Hex)</label>
              <input
                style={styles.input}
                value={baseAddress}
                disabled={status.active}
                onChange={(e) => setTargetConfig({ baseAddress: e.target.value })}
                placeholder="0x08000000"
              />
            </div>

            {/* Chunk Size */}
            <div>
              <label style={styles.fieldLabel}>Block Chunk Size (Bytes)</label>
              <select
                style={styles.select}
                value={chunkSize}
                disabled={status.active}
                onChange={(e) => setTargetConfig({ chunkSize: parseInt(e.target.value, 10) || 64 })}
              >
                {CHUNK_SIZES.map((cs) => (
                  <option key={cs} value={cs}>
                    {cs} bytes {cs === 64 ? '(CAN FD Standard)' : cs <= 8 ? '(Classical CAN)' : ''}
                  </option>
                ))}
              </select>
            </div>

            {/* Flags: Extended, FD, BRS */}
            <div style={{ display: 'flex', gap: 12, marginTop: 4, padding: '6px 8px', background: 'rgba(255,255,255,0.02)', borderRadius: 4, border: '1px solid var(--border-subtle)' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={isExtended}
                  disabled={status.active}
                  onChange={(e) => setTargetConfig({ isExtended: e.target.checked })}
                  style={{ accentColor: 'var(--accent-green)' }}
                />
                <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)' }}>29-bit EXT</span>
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={isFd}
                  disabled={status.active}
                  onChange={(e) => setTargetConfig({ isFd: e.target.checked, bitrateSwitch: e.target.checked ? bitrateSwitch : false })}
                  style={{ accentColor: '#38bdf8' }}
                />
                <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: isFd ? '#38bdf8' : 'inherit' }}>CAN FD</span>
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: isFd ? 'pointer' : 'not-allowed', opacity: isFd ? 1 : 0.4 }}>
                <input
                  type="checkbox"
                  checked={bitrateSwitch}
                  disabled={!isFd || status.active}
                  onChange={(e) => setTargetConfig({ bitrateSwitch: e.target.checked })}
                  style={{ accentColor: '#f59e0b' }}
                />
                <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: bitrateSwitch ? '#f59e0b' : 'inherit' }}>BRS</span>
              </label>
            </div>

            {/* Dynamic Protocol-Specific Options */}
            {selectedProto && selectedProto.options.length > 0 && (
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase' }}>
                  {selectedProto.name} Options
                </span>
                {selectedProto.options.map((opt) => {
                  const val = protocolOptions[opt.key] ?? opt.default;
                  return (
                    <div key={opt.key} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <label style={{ ...styles.fieldLabel, display: 'flex', justifyContent: 'space-between' }}>
                        <span>{opt.label}</span>
                        {(opt.type === 'hex' || opt.key.endsWith('_hex')) && <span style={{ color: 'var(--text-muted)' }}>Hex</span>}
                      </label>
                      {opt.type === 'bool' || opt.type === 'boolean' ? (
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={Boolean(val)}
                            disabled={status.active}
                            onChange={(e) => setProtocolOption(opt.key, e.target.checked)}
                            style={{ accentColor: 'var(--accent-green)' }}
                          />
                          <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{opt.description}</span>
                        </label>
                      ) : (opt.type === 'choice' || opt.type === 'select') && (opt.choices || opt.options) ? (
                        <select
                          style={styles.select}
                          value={String(val)}
                          disabled={status.active}
                          onChange={(e) => setProtocolOption(opt.key, e.target.value)}
                        >
                          {(opt.choices || opt.options || []).map((c: string) => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          style={styles.input}
                          type={opt.type === 'int' || opt.type === 'number' ? 'number' : 'text'}
                          value={String(val)}
                          disabled={status.active}
                          onChange={(e) => {
                            const raw = e.target.value;
                            const parsed = (opt.type === 'int' || opt.type === 'number') ? (opt.type === 'int' ? parseInt(raw, 10) || 0 : parseFloat(raw) || 0) : raw;
                            setProtocolOption(opt.key, parsed);
                          }}
                          placeholder={String(opt.default)}
                        />
                      )}
                      {opt.type !== 'bool' && opt.type !== 'boolean' && opt.description && (
                        <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{opt.description}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ===================================================================
            COLUMN 2: Firmware Upload & Flash Action Controls
            =================================================================== */}
        <div style={styles.columnCard}>
          <div style={styles.cardHeader}>
            <span style={styles.cardTitle}>2. Firmware & Flashing</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 8, flex: 1 }}>
            {/* Firmware File Box */}
            {!firmwareInfo ? (
              <div
                style={styles.dropZone}
                onClick={() => fileInputRef.current?.click()}
                onDrop={handleFirmwareDrop}
                onDragOver={(e) => e.preventDefault()}
              >
                <div style={{ fontSize: 22, color: 'var(--text-muted)' }}>📦</div>
                <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-primary)' }}>
                  {parsingFirmware ? 'Parsing Firmware...' : 'Drop .bin, .hex, or .srec'}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Click or drag firmware file</div>
              </div>
            ) : (
              <div style={styles.firmwareCard}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className="badge badge-green">.{(firmwareInfo.file_type || firmwareInfo.format || 'BIN').toUpperCase()}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-all' }}>
                      {firmwareInfo.filename}
                    </span>
                  </div>
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={status.active}
                    onClick={clearFirmware}
                    style={{ padding: '0 4px', fontSize: 11 }}
                    title="Remove firmware file"
                  >
                    ✕
                  </button>
                </div>

                <div style={styles.firmwareDetailsGrid}>
                  <div><span style={styles.metaLabel}>Size:</span> <span className="mono">{firmwareInfo.size_formatted || `${(firmwareInfo.total_bytes / 1024).toFixed(1)} KB`} ({firmwareInfo.total_bytes.toLocaleString()} B)</span></div>
                  <div><span style={styles.metaLabel}>Base Addr:</span> <span className="mono">{firmwareInfo.base_address}</span></div>
                  <div><span style={styles.metaLabel}>CRC32:</span> <span className="mono">{firmwareInfo.crc32}</span></div>
                  <div><span style={styles.metaLabel}>MD5:</span> <span className="mono" style={{ fontSize: 9 }}>{firmwareInfo.md5}</span></div>
                </div>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".bin,.hex,.srec"
              style={{ display: 'none' }}
              onChange={handleFirmwareFile}
            />

            {/* Error Banner if any */}
            {status.error && (
              <div style={styles.errorBox}>
                ⚠ {status.error}
              </div>
            )}

            {/* Action Buttons */}
            <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {status.active ? (
                <button
                  className="btn btn-danger btn-full"
                  onClick={abortFlash}
                  style={{ height: 36, fontSize: 13, fontWeight: 600 }}
                >
                  ■ Abort Flashing Process
                </button>
              ) : (
                <button
                  className="btn btn-primary btn-full"
                  disabled={!isConnected || !firmwareInfo || !selectedProtocolId}
                  onClick={startFlash}
                  style={{
                    height: 38,
                    fontSize: 13,
                    fontWeight: 600,
                    background: (!isConnected || !firmwareInfo) ? undefined : 'var(--accent-green)',
                    borderColor: (!isConnected || !firmwareInfo) ? undefined : 'var(--accent-green)',
                  }}
                >
                  ⚡ Start Flashing Target
                </button>
              )}

              {/* Standalone Action Buttons */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 5 }}>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={!isConnected || status.active || !(selectedProto?.supported_actions ?? ['erase', 'verify', 'reset_ecu']).includes('erase')}
                  onClick={() => runAction('erase')}
                  title="Erase flash memory on target"
                >
                  Erase
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={!isConnected || status.active || !firmwareInfo || !(selectedProto?.supported_actions ?? ['erase', 'verify', 'reset_ecu']).includes('verify')}
                  onClick={() => runAction('verify')}
                  title="Verify flash memory checksum"
                >
                  Verify
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={!isConnected || status.active || !(selectedProto?.supported_actions ?? ['erase', 'verify', 'reset_ecu']).includes('reset_ecu')}
                  onClick={() => runAction('reset_ecu')}
                  title="Send ECU Software Reset command"
                >
                  Reset ECU
                </button>
              </div>

              {!isConnected && (
                <div style={{ fontSize: 10, color: 'var(--accent-amber)', textAlign: 'center', marginTop: 2 }}>
                  * Connect CAN bus in top bar first to flash
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ===================================================================
            COLUMN 3: Telemetry, Progress & Diagnostic Terminal
            =================================================================== */}
        <div style={styles.columnCard}>
          <div style={styles.cardHeader}>
            <span style={styles.cardTitle}>3. Real-time Telemetry & Log Console</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={`badge ${
                status.status === 'success' || status.status === 'completed' ? 'badge-green' :
                status.status === 'failed' || status.status === 'error' ? 'badge-red' :
                status.active ? 'badge-blue' : 'badge-muted'
              }`}>
                {status.stage || 'IDLE'}
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 8, flex: 1, minHeight: 0 }}>
            {/* Progress Bar & Telemetry Stats */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <span>
                  {status.bytes_transferred.toLocaleString()} / {status.total_bytes ? status.total_bytes.toLocaleString() : 0} bytes
                </span>
                <span style={{ fontWeight: 600, color: status.progress === 100 ? 'var(--accent-green)' : '#38bdf8' }}>
                  {status.progress.toFixed(1)}%
                </span>
              </div>

              {/* Progress Track */}
              <div style={styles.progressTrack}>
                <div
                  style={{
                    ...styles.progressFill,
                    width: `${Math.min(100, Math.max(0, status.progress))}%`,
                    background: status.status === 'failed' || status.status === 'error' ? '#ef4444' : status.progress === 100 ? 'var(--accent-green)' : '#38bdf8',
                  }}
                />
              </div>

              {/* Speed & ETA */}
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                <span>Speed: <strong style={{ color: 'var(--text-secondary)' }}>{status.speed_kbps.toFixed(1)} KB/s</strong></span>
                <span>ETA: <strong style={{ color: 'var(--text-secondary)' }}>{status.eta_seconds > 0 ? `${status.eta_seconds}s` : '—'}</strong></span>
              </div>
            </div>

            {/* Diagnostic Terminal View */}
            <div style={styles.terminalHeader}>
              <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>DIAGNOSTIC CONSOLE</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer', fontSize: 9, color: 'var(--text-muted)' }}>
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                    style={{ margin: 0 }}
                  />
                  Auto-scroll
                </label>
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ padding: '0 4px', fontSize: 9 }}
                  onClick={() => copyToClipboard(logs.map((l) => `[${new Date(l.timestamp * 1000).toISOString()}] [${l.level.toUpperCase()}] ${l.message}`).join('\n'))}
                  title="Copy terminal output"
                >
                  Copy
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ padding: '0 4px', fontSize: 9 }}
                  onClick={clearLogs}
                  title="Clear terminal output"
                >
                  Clear
                </button>
              </div>
            </div>

            <div style={styles.terminalContainer}>
              {logs.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', padding: 8 }}>
                  Ready. Protocol output and CAN exchanges will appear here.
                </div>
              ) : (
                logs.map((l, i) => {
                  const d = new Date(l.timestamp * 1000);
                  const timeStr = `${d.toTimeString().slice(0, 8)}.${String(d.getMilliseconds()).padStart(3, '0')}`;
                  let color = 'var(--text-secondary)';
                  if (l.level === 'tx') color = '#38bdf8';
                  else if (l.level === 'rx') color = '#a855f7';
                  else if (l.level === 'success') color = '#22c55e';
                  else if (l.level === 'warn' || l.level === 'warning') color = '#f59e0b';
                  else if (l.level === 'error') color = '#ef4444';

                  return (
                    <div key={i} style={styles.logRow}>
                      <span style={styles.logTime}>{timeStr}</span>
                      <span style={{ ...styles.logLevel, color }}>[{l.level.toUpperCase()}]</span>
                      <span style={{ color, wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>{l.message}</span>
                    </div>
                  );
                })
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>

      {/* ===================================================================
          PROTOCOL MANAGEMENT MODAL
          =================================================================== */}
      {modalOpen && (
        <div style={styles.modalBackdrop}>
          <div style={styles.modalBox}>
            <div style={styles.modalHeader}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                  Universal CAN Flasher - Protocol Plugins
                </span>
              </div>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setModalOpen(false);
                  setViewingCode(null);
                }}
                style={{ fontSize: 13 }}
              >
                ✕
              </button>
            </div>

            {/* Modal Tabs */}
            <div style={styles.modalTabBar}>
              <button
                className={`tab-btn ${modalTab === 'list' ? 'active' : ''}`}
                onClick={() => setModalTab('list')}
              >
                Installed Protocols ({protocols.length})
              </button>
              <button
                className={`tab-btn ${modalTab === 'upload' ? 'active' : ''}`}
                onClick={() => setModalTab('upload')}
              >
                Upload Custom Protocol (.py)
              </button>
              <button
                className={`tab-btn ${modalTab === 'template' ? 'active' : ''}`}
                onClick={() => setModalTab('template')}
              >
                Starter Template & API Reference
              </button>
            </div>

            {/* Tab Content */}
            <div style={styles.modalContent}>
              {/* TAB 1: INSTALLED PROTOCOLS */}
              {modalTab === 'list' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    CANviz dynamically loads Python protocol plugins from <code className="mono">backend/canviz/flasher/protocols/</code> and registered plugins.
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {protocols.map((p) => (
                      <div key={p.id} style={styles.protocolCard}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                              {p.name}
                            </span>
                            <span className="mono text-xs" style={{ color: 'var(--text-muted)' }}>
                              {p.version ? `v${p.version}` : ''}{p.author ? ` by ${p.author}` : ''}
                            </span>
                            {p.supports_fd && <span className="badge badge-blue">CAN FD</span>}
                            {p.is_custom && <span className="badge badge-amber">User Script</span>}
                          </div>

                          <div style={{ display: 'flex', gap: 6 }}>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => openViewCode(p.id)}
                              style={{ fontSize: 11 }}
                            >
                              View Code
                            </button>
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                selectProtocol(p.id);
                                setModalOpen(false);
                              }}
                              style={{ fontSize: 11 }}
                            >
                              Select
                            </button>
                          </div>
                        </div>

                        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
                          {p.description}
                        </div>

                        <div style={{ display: 'flex', gap: 12, marginTop: 6, fontSize: 10, color: 'var(--text-muted)' }}>
                          <span>Default Tx/Rx: <strong className="mono">{p.default_tx_id} / {p.default_rx_id}</strong></span>
                          <span>Chunk: <strong className="mono">{p.default_chunk_size} B</strong></span>
                          <span>Actions: <strong className="mono">{(p.supported_actions ?? ['erase', 'verify', 'reset_ecu']).join(', ')}</strong></span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Code Viewer Drawer if selected */}
                  {viewingCode && (
                    <div style={{ marginTop: 10, borderTop: '1px solid var(--border-subtle)', paddingTop: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                          Source Code: <code className="mono">{viewingCode.id}.py</code>
                        </span>
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => copyToClipboard(viewingCode.code)}
                            style={{ fontSize: 10 }}
                          >
                            Copy Source
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => downloadFile(`${viewingCode.id}.py`, viewingCode.code)}
                            style={{ fontSize: 10 }}
                          >
                            Download .py
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => setViewingCode(null)}
                            style={{ fontSize: 10 }}
                          >
                            Close
                          </button>
                        </div>
                      </div>
                      <pre style={styles.codeBlock}>
                        {viewingCode.code}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              {/* TAB 2: UPLOAD CUSTOM PROTOCOL */}
              {modalTab === 'upload' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Upload any Python file that subclasses <code className="mono">BaseFlashingProtocol</code>.
                    The system verifies its AST syntax, loads it dynamically in memory, and immediately registers it for flashing!
                  </div>

                  <div
                    style={styles.dropZone}
                    onClick={() => protoFileInputRef.current?.click()}
                    onDrop={(e) => {
                      e.preventDefault();
                      const file = e.dataTransfer.files[0];
                      if (file && file.name.endsWith('.py')) handleProtoUpload(file);
                    }}
                    onDragOver={(e) => e.preventDefault()}
                  >
                    <div style={{ fontSize: 24, color: 'var(--text-muted)' }}>🐍</div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)' }}>
                      {uploading ? 'Validating & Registering Protocol...' : 'Drop .py Protocol File or Click to Browse'}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      Must inherit from BaseFlashingProtocol
                    </div>
                  </div>

                  <input
                    ref={protoFileInputRef}
                    type="file"
                    accept=".py"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleProtoUpload(file);
                      e.target.value = '';
                    }}
                  />

                  {uploadSuccess && (
                    <div style={{ padding: '8px 10px', background: 'rgba(34,197,94,0.1)', border: '1px solid #22c55e50', borderRadius: 4, color: '#22c55e', fontSize: 11 }}>
                      ✓ {uploadSuccess}
                    </div>
                  )}

                  {uploadError && (
                    <div style={styles.errorBox}>
                      ⚠ {uploadError}
                    </div>
                  )}
                </div>
              )}

              {/* TAB 3: STARTER TEMPLATE */}
              {modalTab === 'template' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Use this starter boilerplate as reference to create your own custom flashing protocol.
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => copyToClipboard(templateCode)}
                        style={{ fontSize: 10 }}
                      >
                        Copy Template
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => downloadFile('custom_protocol_template.py', templateCode)}
                        style={{ fontSize: 10 }}
                      >
                        Download template.py
                      </button>
                    </div>
                  </div>

                  <pre style={styles.codeBlock}>
                    {templateCode || 'Loading template...'}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    height: '100%',
    overflow: 'hidden',
  },
  topBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '4px 8px',
    background: 'var(--bg-elevated)',
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border-subtle)',
  },
  sectionLabel: {
    fontSize: 10,
    fontFamily: 'var(--font-mono)',
    fontWeight: 'bold',
    color: 'var(--text-muted)',
    letterSpacing: '0.05em',
  },
  threeColumnGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr 1.3fr',
    gap: 8,
    flex: 1,
    minHeight: 0,
  },
  columnCard: {
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--bg-elevated)',
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border-subtle)',
    overflowY: 'auto',
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 10px',
    borderBottom: '1px solid var(--border-subtle)',
    background: 'rgba(255,255,255,0.015)',
  },
  cardTitle: {
    fontSize: 11,
    fontFamily: 'var(--font-mono)',
    fontWeight: 600,
    color: 'var(--text-primary)',
    letterSpacing: '0.03em',
  },
  fieldLabel: {
    fontSize: 10,
    fontFamily: 'var(--font-mono)',
    color: 'var(--text-muted)',
    marginBottom: 2,
    display: 'block',
  },
  input: {
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-default)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)',
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    padding: '4px 7px',
    outline: 'none',
    width: '100%',
  },
  select: {
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-default)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)',
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    padding: '3px 6px',
    outline: 'none',
    cursor: 'pointer',
    width: '100%',
  },
  dropZone: {
    border: '1px dashed var(--border-default)',
    borderRadius: 'var(--radius-sm)',
    padding: '16px 10px',
    textAlign: 'center',
    cursor: 'pointer',
    background: 'rgba(255,255,255,0.01)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
    transition: 'border-color 0.2s',
  },
  firmwareCard: {
    padding: 8,
    borderRadius: 'var(--radius-sm)',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border-default)',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  firmwareDetailsGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 4,
    fontSize: 10,
    color: 'var(--text-secondary)',
    borderTop: '1px solid var(--border-subtle)',
    paddingTop: 6,
  },
  metaLabel: {
    color: 'var(--text-muted)',
    fontFamily: 'var(--font-mono)',
    fontSize: 9,
  },
  errorBox: {
    padding: '6px 8px',
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid #ef444450',
    borderRadius: 'var(--radius-sm)',
    color: '#ef4444',
    fontSize: 10,
    fontFamily: 'var(--font-mono)',
    wordBreak: 'break-all',
  },
  progressTrack: {
    width: '100%',
    height: 8,
    borderRadius: 4,
    background: 'var(--bg-panel)',
    overflow: 'hidden',
    border: '1px solid var(--border-subtle)',
  },
  progressFill: {
    height: '100%',
    transition: 'width 0.25s ease',
  },
  terminalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 6,
    paddingBottom: 4,
    borderBottom: '1px solid var(--border-subtle)',
  },
  terminalContainer: {
    flex: 1,
    minHeight: 90,
    background: 'var(--bg-base)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 'var(--radius-sm)',
    padding: '6px 8px',
    overflowY: 'auto',
    fontFamily: 'var(--font-mono)',
    fontSize: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  logRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 6,
    lineHeight: 1.35,
  },
  logTime: {
    color: 'var(--text-muted)',
    fontSize: 9,
    flexShrink: 0,
  },
  logLevel: {
    fontWeight: 600,
    fontSize: 9,
    flexShrink: 0,
  },
  modalBackdrop: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0,0,0,0.65)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
    backdropFilter: 'blur(3px)',
  },
  modalBox: {
    width: 680,
    maxWidth: '90vw',
    maxHeight: '85vh',
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-default)',
    borderRadius: 8,
    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  modalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 14px',
    borderBottom: '1px solid var(--border-subtle)',
  },
  modalTabBar: {
    display: 'flex',
    borderBottom: '1px solid var(--border-subtle)',
    background: 'var(--bg-elevated)',
    padding: '0 10px',
  },
  modalContent: {
    padding: 14,
    overflowY: 'auto',
    flex: 1,
  },
  protocolCard: {
    padding: 10,
    borderRadius: 'var(--radius-sm)',
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border-subtle)',
  },
  codeBlock: {
    background: 'var(--bg-base)',
    padding: 10,
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border-subtle)',
    fontSize: 10.5,
    fontFamily: 'var(--font-mono)',
    color: 'var(--text-primary)',
    maxHeight: 320,
    overflowY: 'auto',
    whiteSpace: 'pre-wrap',
    margin: 0,
  },
};
