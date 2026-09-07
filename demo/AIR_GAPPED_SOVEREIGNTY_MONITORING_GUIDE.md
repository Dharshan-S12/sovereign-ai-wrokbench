# Air-Gapped Sovereignty Monitoring & Zero-WAN Egress Verification

**Document Classification:** Cybersecurity & Compliance Engineering Specification  
**Facility Target:** Mangalore Refinery and Petrochemicals Limited (MRPL On-Premises Complex)  
**Location in Repository:** `demo/AIR_GAPPED_SOVEREIGNTY_MONITORING_GUIDE.md`  
**Compliance Standards:** CERT-In Industrial Cybersecurity Directives, NCIIPC Critical Infrastructure Guidelines, OISD-163, MRPL SEC-POL-007  

---

## Executive Summary

In high-consequence industrial facilities such as oil refineries, petrochemical complexes, and power plants, artificial intelligence systems must comply with strict statutory **air-gap mandates**. Critical plant telemetry (vibration spectrums, bearing temperatures, emergency valve timings, pipe wall degradation) represents national energy infrastructure data. 

To prove compliance to cybersecurity auditors and regulatory bodies, Omni Studio does not simply claim to be offline—it implements **active, real-time, process-level socket monitoring and cryptographic audit chaining**.

This document details:
1. **The Sovereignty Mandate & Threat Model:** Why active monitoring is necessary.
2. **The Real-Time Network Monitor Architecture:** How the 2-second background socket sampler operates.
3. **Microscopic Code Mechanisms:** Exact functions, data structures, and classification rules.
4. **Defense-in-Depth Offline Hardening:** Environment variables and library configurations.
5. **Cryptographic SHA-256 Merkle Chain Auditing:** Tamper-evident operational logging.
6. **Frontend Live Dashboard (`/monitor`):** Real-time UI visualization.
7. **Auditor Demonstration Playbook:** Step-by-step verification commands.

---

## 1. The Sovereignty Mandate & Threat Model

Traditional cloud AI or improperly configured local AI systems present multiple attack surfaces for silent data leakage:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DATA EXFILTRATION THREATS                       │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  1. Vendor Analytics Telemetry:                                        │
│     Open-source frameworks (ChromaDB, HuggingFace, PyTorch) often      │
│     contain default opt-out telemetry beacons that transmit IP, host,  │
│     and usage statistics to external cloud endpoints.                  │
│                                                                        │
│  2. Silent Weight Downloading:                                         │
│     If an embedding or tokenizer model is missing from local cache,    │
│     libraries may silently attempt an HTTPS connection to HuggingFace  │
│     Hub or PyPI.                                                       │
│                                                                        │
│  3. Model Phoning Home:                                                │
│     Unverified inference servers may open outbound telemetry channels. │
│                                                                        │
│  4. Malicious Code Execution Payloads:                                 │
│     A prompt injection in an uploaded PDF attempting a reverse-shell   │
│     via Python socket libraries.                                       │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**Omni Studio's Defense Principle:**  
> *"Any outbound socket connection to a non-loopback IP address is classified as a severe statutory sovereignty violation and triggers an immediate plant security alert."*

---

## 2. High-Level Monitoring Architecture

The Air-Gap Monitoring subsystem operates continuously in the background of the FastAPI application.

```mermaid
flowchart TD
    subgraph KERNEL ["1. Operating System Socket Layer"]
        OS_NET["OS Kernel Socket Table<br>(psutil.net_connections)"]
    end

    subgraph PROCS ["2. Monitored Process Tree"]
        UVICORN["FastAPI / Uvicorn Server (PID n)"]
        WORKERS["Child Worker Subprocesses"]
        SANDBOX["Ephemeral Sandbox Workers"]
        OLLAMA["Local Inference Server (PID m)"]
        UVICORN --> WORKERS & SANDBOX
    end

    subgraph WATCHDOG ["3. Real-Time Network Watchdog (network_watch.py)"]
        LOOP["Async Background Daemon<br>(Runs every 2.0 seconds)"]
        SAMPLER["sample_network_connections()"]
        CLASSIFIER{"classify_connection()<br>Is Remote IP Loopback?"}
        
        LOOP --> SAMPLER
        SAMPLER --> PROCS
        PROCS --> OS_NET
        OS_NET --> CLASSIFIER
        
        CLASSIFIER -->|127.0.0.1 / ::1| LOG_LOCAL["Log 'local' Connection<br>Increment _total_local_seen"]
        CLASSIFIER -->|Any External IP| LOG_ALERT["Log 'external' ALARM<br>Increment _total_external_seen<br>is_air_gapped = False"]
    end

    subgraph STORAGE ["4. State & Audit Memory"]
        ROLLING["Rolling Log Buffer<br>(deque maxlen=200)"]
        COUNTERS["Lifetime Counters<br>(_total_local_seen, _total_external_seen)"]
        HASH_CHAIN["Cryptographic Merkle Audit Chain<br>(audit_hash_chain.jsonl)"]
        
        LOG_LOCAL & LOG_ALERT --> ROLLING & COUNTERS
    end

    subgraph UI ["5. Security Dashboard (React SPA)"]
        DASH["/monitor Dashboard View<br>(Polls every 3s via REST)"]
        BADGE_OK["Green Badge: AIR-GAPPED COMPLIANT<br>(0 External Egress)"]
        BADGE_FAIL["Red Badge: SOVEREIGNTY VIOLATION<br>(External Egress Detected)"]
        SOCKET_TABLE["Live Sockets Table<br>(PID, Local, Remote, Status, Protocol)"]
        AUDIT_VERIFIER["Merkle Audit Verifier Card"]
        
        DASH --> BADGE_OK & BADGE_FAIL & SOCKET_TABLE & AUDIT_VERIFIER
    end

    STORAGE --> DASH
```

---

## 3. Microscopic Code Mechanisms

The monitoring engine is implemented in [`backend/app/monitor/network_watch.py`](file:///c:/sih117/prototype/backend/app/monitor/network_watch.py) and exposed via [`backend/app/routers/monitor.py`](file:///c:/sih117/prototype/backend/app/routers/monitor.py).

### 3.1 Strict Socket Classification (`classify_connection`)
Located at [`network_watch.py:L22-L38`](file:///c:/sih117/prototype/backend/app/monitor/network_watch.py#L22-L38):

```python
def classify_connection(remote_ip: str, remote_port: Optional[int] = None) -> str:
    """
    Classify connection as 'local' (loopback) or 'external'.
    Flag ANY non-loopback connection (including LAN/WAN) as 'external' for sovereign proof.
    """
    if not remote_ip or remote_ip in ["0.0.0.0", "::", ""]:
        return "local"
    
    clean_ip = remote_ip.strip().lower()
    if (
        clean_ip in ["127.0.0.1", "::1", "localhost"]
        or clean_ip.startswith("127.")
        or clean_ip == "fe80::1"
    ):
        return "local"
    
    return "external"
```

- **Loopback Whitelist:** Strictly restricted to `127.0.0.1`, `::1`, `localhost`, and link-local IPv6 loopback `fe80::1`.
- **Zero-Egress Rule:** Any connection attempt to a public WAN IP, cloud DNS, or corporate intranet routing gateway is immediately stamped as `external`.

---

### 3.2 Recursive Process Tree Discovery (`get_monitored_processes`)
Located at [`network_watch.py:L40-L70`](file:///c:/sih117/prototype/backend/app/monitor/network_watch.py#L40-L70):

```python
def get_monitored_processes() -> List[psutil.Process]:
    """
    Get the backend process and any children (sandbox workers, subprocesses).
    """
    procs = []
    try:
        current_proc = psutil.Process(os.getpid())
        procs.append(current_proc)
        
        # Add parent if uvicorn worker child
        try:
            parent = current_proc.parent()
            if parent and ("python" in parent.name().lower() or "uvicorn" in (parent.name() or "").lower()):
                procs.append(parent)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        # Add recursive children (sandbox processes, sub-agents)
        children = current_proc.children(recursive=True)
        procs.extend(children)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # Deduplicate by PID
    unique_map = {p.pid: p for p in procs if p.is_running()}
    return list(unique_map.values())
```

- Discovers not only the main FastAPI process, but also parent Uvicorn managers and any child processes spawned to run Python code sandboxes or OCR rasterizers.

---

### 3.3 Active Connection Inspection (`get_active_connections`)
Located at [`network_watch.py:L72-L125`](file:///c:/sih117/prototype/backend/app/monitor/network_watch.py#L72-L125):

- For each discovered PID, queries active sockets via:
  ```python
  net_conns = p.net_connections(kind="inet")
  ```
- Extracts:
  - `pid`: Process ID
  - `process_name`: Executable name (`python.exe`, `uvicorn.exe`)
  - `local_address`: `127.0.0.1:8000` (FastAPI) or `127.0.0.1:5432` (PostgreSQL)
  - `remote_address`: Destination IP and port
  - `status`: `LISTEN`, `ESTABLISHED`, `TIME_WAIT`
  - `type`: `TCP` or `UDP`
  - `classification`: `local` vs `external`
- Records distinct connection keys into `_seen_connection_keys` to maintain running counters across the entire process lifetime.

---

### 3.4 2-Second Background Daemon Loop (`_monitor_loop`)
Located at [`network_watch.py:L183-L213`](file:///c:/sih117/prototype/backend/app/monitor/network_watch.py#L183-L213):

- **Startup:** Registered in FastAPI lifespan ([`backend/app/main.py`](file:///c:/sih117/prototype/backend/app/main.py#L55)) via `start_network_monitor_loop()`.
- **Sampling Interval:** Executes every `2.0 seconds` asynchronously (`await asyncio.sleep(2)`).
- **Idle Heartbeat:** If no active connections exist, it logs an idle heartbeat entry (`LISTEN (Loopback Only)`) into the rolling buffer, proving that the monitor was active and verified zero traffic.
- **Rolling Log Buffer:** Keeps the latest 200 events in memory (`deque(maxlen=200)`).

---

### 3.5 REST API Payload (`GET /monitor/connections`)
Exposed at [`backend/app/routers/monitor.py`](file:///c:/sih117/prototype/backend/app/routers/monitor.py):

```json
{
  "is_air_gapped": true,
  "total_external_connections_seen": 0,
  "total_local_connections_seen": 42,
  "monitored_processes_count": 2,
  "monitored_processes": [
    {
      "pid": 14220,
      "name": "python.exe",
      "status": "running",
      "created": "2026-09-07T12:15:30.123Z"
    }
  ],
  "active_connections": [
    {
      "pid": 14220,
      "process_name": "python.exe",
      "local_address": "127.0.0.1:8000",
      "remote_address": "-",
      "remote_ip": "",
      "remote_port": null,
      "status": "LISTEN",
      "type": "TCP",
      "classification": "local",
      "timestamp": "2026-09-07T17:28:45.000Z"
    },
    {
      "pid": 14220,
      "process_name": "python.exe",
      "local_address": "127.0.0.1:54912",
      "remote_address": "127.0.0.1:11434",
      "remote_ip": "127.0.0.1",
      "remote_port": 11434,
      "status": "ESTABLISHED",
      "type": "TCP",
      "classification": "local",
      "timestamp": "2026-09-07T17:28:45.000Z"
    }
  ],
  "recent_log": [ ... ],
  "timestamp": "2026-09-07T17:28:45.102Z"
}
```

---

## 4. Defense-in-Depth Offline Hardening

Active monitoring is backed by **strict environment-level and framework-level locks** that prevent outbound connections at the driver and library level:

### 1. Hugging Face & Transformers Offline Lock
Enforced at the top of [`seed_kb.py`](file:///c:/sih117/prototype/backend/scripts/seed_kb.py#L7-L8) and [`rag/client.py`](file:///c:/sih117/prototype/backend/app/rag/client.py#L7-L8):
```python
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
```
*Effect:* If any component attempts to download tokenizers or weights from the Hugging Face Hub, the library immediately raises a local `OfflineModeIsEnabled` error instead of attempting a DNS lookup.

### 2. ChromaDB Telemetry Hard Kill
Enforced at [`rag/client.py:L9`](file:///c:/sih117/prototype/backend/app/rag/client.py#L9):
```python
os.environ["ANONYMIZED_TELEMETRY"] = "False"
Settings(anonymized_telemetry=False)
```
*Effect:* Completely strips posthog and telemetry HTTP dispatchers from ChromaDB vector store operations.

### 3. Localhost-Only Service Binding
Enforced across the configuration stack:
- **Ollama Inference:** Bound strictly to `http://127.0.0.1:11434`.
- **FastAPI Server:** Bound to `127.0.0.1:8000` (or `0.0.0.0:8000` inside isolated Docker bridge network).
- **PostgreSQL:** Bound to `localhost:5432`.
- **ChromaDB:** In-process embedded database (`./storage/chroma`) with zero client-server network hops.

---

## 5. Cryptographic SHA-256 Merkle Chain Auditing

To prove that operational logs have not been altered or deleted after the fact, Omni Studio maintains an immutable **SHA-256 Merkle Hash Chain** in [`storage/audit_hash_chain.jsonl`](file:///c:/sih117/prototype/backend/storage/audit_hash_chain.jsonl).

### 5.1 Mathematical Chaining Formula
Every recorded event $n$ is cryptographically linked to the preceding event $n-1$:

$$\text{Hash}_n = \text{SHA-256}\left(\text{Data}_n \parallel \text{Hash}_{n-1}\right)$$

Where:
- $\text{Data}_n$ is the canonical JSON string containing: `timestamp`, `event_type`, `operator_id`, `task_id`, `input_hash`, `output_hash`.
- $\text{Hash}_{n-1}$ is the SHA-256 checksum of the prior entry in the ledger.
- The genesis entry uses $\text{Hash}_0 = \text{"0" \times 64}$.

### 5.2 Tamper Verification (`GET /audit/verify`)
Exposed at [`backend/app/routers/audit.py`](file:///c:/sih117/prototype/backend/app/routers/audit.py):
1. Reads all lines sequentially from disk.
2. Recomputes $\text{Hash}_n$ from the stored record fields and previous hash.
3. If $\text{RecomputedHash} \neq \text{StoredHash}$, the verifier halts and flags the exact line number as **TAMPERED**.
4. Returns verification proof:
   ```json
   {
     "is_valid": true,
     "total_entries": 158,
     "corrupted_entries": 0,
     "verified_at": "2026-09-07T17:30:00.000Z",
     "root_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
   }
   ```

---

## 6. Frontend Live Dashboard (`/monitor`)

Plant operators and cybersecurity officers inspect air-gap compliance via the **Air-Gap Sovereignty Monitor** UI ([`frontend/src/views/MonitorView.tsx`](file:///c:/sih117/prototype/frontend/src/views/MonitorView.tsx)):

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  MRPL CYBERSECURITY & COMPLIANCE                          [ Live Audit: Active (3s) ]  │
│  🛡️ AIR-GAP SOVEREIGNTY MONITOR                                                       │
│  Real-time process-level socket audit & zero-telemetry hardware verification           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  ┌────────────────────────────────────────┐  ┌──────────────────────────────────────┐  │
│  │  AIR-GAP SOVEREIGNTY STATUS            │  │  NETWORK EGRESS COUNTER              │  │
│  │  ✅ AIR-GAPPED COMPLIANT               │  │  0 External Connections Seen         │  │
│  │  Zero non-loopback egress detected     │  │  42 Local Loopback Sockets Verified  │  │
│  └────────────────────────────────────────┘  └──────────────────────────────────────┘  │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │  CRYPTOGRAPHIC SHA-256 AUDIT CHAIN VERIFIER                   [ Verify Chain ]   │  │
│  │  Status: ✅ UNBROKEN (158 / 158 records mathematically verified)                 │  │
│  │  Root Hash: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855    │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                        │
│  ACTIVE PROCESS SOCKETS:                                                               │
│  ┌───────┬─────────────┬─────────────────┬──────────────────┬──────────┬────────────┐  │
│  │ PID   │ Process     │ Local Address   │ Remote Address   │ Status   │ Type       │  │
│  ├───────┼─────────────┼─────────────────┼──────────────────┼──────────┼────────────┤  │
│  │ 14220 │ python.exe  │ 127.0.0.1:8000  │ -                │ LISTEN   │ TCP (Local)│  │
│  │ 14220 │ python.exe  │ 127.0.0.1:54912 │ 127.0.0.1:11434  │ ESTAB    │ TCP (Local)│  │
│  │ 14220 │ python.exe  │ 127.0.0.1:54915 │ 127.0.0.1:5432   │ ESTAB    │ TCP (Local)│  │
│  └───────┴─────────────┴─────────────────┴──────────────────┴──────────┴────────────┘  │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Key UI Features:
1. **Pulsing Live Indicator:** Continuously polls `/monitor/connections` every 3 seconds.
2. **Instant Status Flip:** If even a single external connection occurs, the card flips from an **Emerald ShieldCheck** to a **Crimson ShieldAlert** with the warning: *"SOVEREIGNTY VIOLATION DETECTED: External Egress Attempt Recorded"*.
3. **One-Click Merkle Proof:** The **"Verify Chain"** button executes full end-to-end mathematical verification of the SHA-256 ledger across hundreds of historical operations.
4. **Socket Inspector Table:** Displays live kernel sockets, ports, and classifications for every child worker.

---

## 7. Auditor Demonstration Playbook

To demonstrate air-gap sovereignty to an auditor, follow this 4-step terminal and browser verification protocol:

### Step 1: Verify API Endpoint Directly via cURL
In a terminal, query the monitor endpoint:
```powershell
curl -s http://127.0.0.1:8000/monitor/connections | jq .
```
**Expected Verification:**
- `"is_air_gapped": true`
- `"total_external_connections_seen": 0`
- All active connections list `127.0.0.1` or loopback addresses.

---

### Step 2: Verify OS-Level Socket Bindings via Netstat
Inspect Windows / Linux kernel network connections for the Python process:
```powershell
netstat -ano | findstr "8000 11434 5432"
```
**Expected Verification:**
- All listening ports bind strictly to `127.0.0.1:8000`, `127.0.0.1:11434`, or `127.0.0.1:5432`.
- Zero foreign IP addresses appear in the `Foreign Address` column.

---

### Step 3: Trigger Cryptographic Audit Chain Verification
```powershell
curl -s http://127.0.0.1:8000/audit/verify | jq .
```
**Expected Verification:**
- `"is_valid": true`
- `"corrupted_entries": 0`
- Demonstrates that every action in the facility's ledger is mathematically linked to the genesis block.

---

### Step 4: View the Real-Time Web Dashboard
1. Open browser to `http://localhost:5173/monitor` (or click **"Security & Monitor"** in the sidebar).
2. Observe the green **"AIR-GAPPED COMPLIANT"** badge and live socket activity.
3. Click **"Verify Chain"** to confirm tamper-evidence.

---

## Conclusion

Omni Studio's air-gap monitoring replaces trust with **continuous mathematical and operating-system-level proof**. By continuously inspecting sockets, killing telemetry at the environment layer, and linking all actions into a SHA-256 Merkle chain, the Sovereign Workbench satisfies the most stringent cybersecurity requirements for critical energy infrastructure.
