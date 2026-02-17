import gradio as gr
import socket
import hashlib
import json
import time
import os
import tempfile
from cryptography.fernet import Fernet
from blockchain import Blockchain

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# ─────────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────────
blockchain = Blockchain()
active_nodes = {}  # {"ip:port": last_seen_timestamp}


# ─────────────────────────────────────────────
# NODE REGISTRY (replaces discovery_server.py)
# ─────────────────────────────────────────────
def register_node(ip: str, port: int):
    """Register a storage node"""
    node_address = f"{ip}:{port}"
    active_nodes[node_address] = time.time()
    print(f"[+] Node Registered: {node_address}")
    return list(active_nodes.keys())


def get_live_nodes():
    """Get active nodes, pruning dead ones (5 min timeout)"""
    current = time.time()
    dead = [n for n, ts in active_nodes.items() if current - ts > 300]
    for n in dead:
        del active_nodes[n]
        print(f"[-] Node Pruned: {n}")
    return list(active_nodes.keys())


# ─────────────────────────────────────────────
# TCP HELPERS (talk to storage nodes)
# ─────────────────────────────────────────────
def tcp_upload_chunk(ip, port, chunk_data, chunk_name):
    """Send a single encrypted chunk to a storage node via TCP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((ip, int(port)))
        s.send(chunk_name.encode())
        ack = s.recv(1024)
        if ack != b"ACK":
            s.close()
            return False
        s.sendall(chunk_data)
        s.close()
        return True
    except Exception as e:
        print(f"[-] Upload to {ip}:{port} failed: {e}")
        return False


def tcp_download_chunk(ip, port, chunk_name):
    """Fetch a single chunk from a storage node via TCP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((ip, int(port)))
        s.send(f"GET:{chunk_name}".encode())
        data = b""
        while True:
            packet = s.recv(4096)
            if not packet:
                break
            data += packet
        s.close()
        return data if data else None
    except Exception as e:
        print(f"[-] Download from {ip}:{port} failed: {e}")
        return None


# ─────────────────────────────────────────────
# CRYPTO & MERKLE
# ─────────────────────────────────────────────
def build_merkle_tree(chunks):
    """Build Merkle Root from list of chunk byte arrays"""
    hashes = [hashlib.sha256(c).hexdigest() for c in chunks]
    while len(hashes) > 1:
        temp = []
        for i in range(0, len(hashes), 2):
            n1 = hashes[i]
            n2 = hashes[i + 1] if i + 1 < len(hashes) else n1
            combined = hashlib.sha256((n1 + n2).encode()).hexdigest()
            temp.append(combined)
        hashes = temp
    return hashes[0]


# ─────────────────────────────────────────────
# UPLOAD HANDLER
# ─────────────────────────────────────────────
def handle_upload(file, owner_name, progress=gr.Progress()):
    """Full upload pipeline: encrypt → shard → distribute → blockchain"""
    if file is None:
        return "❌ No file selected.", ""

    owner = owner_name.strip() or "Anonymous"

    # Read file bytes
    with open(file.name, "rb") as f:
        file_bytes = f.read()

    original_name = os.path.basename(file.name)

    if len(file_bytes) > MAX_FILE_SIZE:
        return f"❌ File too large. Max size: {MAX_FILE_SIZE // (1024*1024)} MB.", ""

    progress(0.1, desc="🔐 Encrypting...")

    # 1. Encrypt
    key = Fernet.generate_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(file_bytes)

    progress(0.3, desc="✂️ Sharding...")

    # 2. Shard
    chunks = [encrypted[i:i + CHUNK_SIZE] for i in range(0, len(encrypted), CHUNK_SIZE)]

    # 3. Get nodes
    nodes = get_live_nodes()
    if not nodes:
        return "❌ No storage nodes online. Start smart_node.py with Playit.gg first.", ""

    progress(0.4, desc=f"📡 Distributing {len(chunks)} chunks to {len(nodes)} nodes...")

    # 4. Distribute
    location_map = {}
    failed = []

    for i, chunk_data in enumerate(chunks):
        node_str = nodes[i % len(nodes)]
        ip, port = node_str.split(":")
        chunk_name = f"{original_name}.part_{i}"

        if tcp_upload_chunk(ip, port, chunk_data, chunk_name):
            location_map[chunk_name] = node_str  # Store ip:port (bug fix!)
        else:
            failed.append(i)

        progress(0.4 + (0.4 * (i + 1) / len(chunks)), desc=f"📡 Chunk {i+1}/{len(chunks)}...")

    if not location_map:
        return "❌ All chunk uploads failed. Check if storage nodes are reachable.", ""

    progress(0.85, desc="⛓️ Recording on blockchain...")

    # 5. Merkle root + Blockchain
    merkle_root = build_merkle_tree(chunks)
    blockchain.new_transaction(owner, merkle_root, original_name, location_map)
    blockchain.new_block(proof=100)
    blockchain.save_to_repo()

    progress(1.0, desc="✅ Complete!")

    warn = f"\n⚠️ {len(failed)} chunk(s) failed to upload." if failed else ""

    result = f"""✅ File uploaded successfully!{warn}

📄 **File:** {original_name}
👤 **Owner:** {owner}
🧩 **Chunks:** {len(chunks)} ({len(location_map)} stored)
🌐 **Nodes used:** {len(set(location_map.values()))}"""

    credentials = f"""🔑 **SAVE THESE — you need them to download:**

**File ID (Merkle Root):**
`{merkle_root}`

**Decryption Key:**
`{key.decode()}`"""

    return result, credentials


# ─────────────────────────────────────────────
# DOWNLOAD HANDLER
# ─────────────────────────────────────────────
def handle_download(file_id, decryption_key):
    """Full download pipeline: lookup → fetch → reassemble → decrypt"""
    file_id = file_id.strip()
    decryption_key = decryption_key.strip()

    if not file_id or not decryption_key:
        return None, "❌ Both File ID and Key are required."

    # 1. Lookup on blockchain
    meta = blockchain.get_file_location(file_id)
    if not meta:
        return None, "❌ File ID not found on blockchain."

    locations = meta['locations']
    file_name = meta['file_name']

    # 2. Sort chunks numerically (fix sorting bug!)
    sorted_chunks = sorted(locations.keys(), key=lambda x: int(x.split('_')[-1]))

    # 3. Fetch each chunk
    full_encrypted = b""
    for chunk_name in sorted_chunks:
        node_str = locations[chunk_name]
        # Handle both old format (ip only) and new format (ip:port)
        if ":" in node_str:
            ip, port = node_str.split(":")
        else:
            ip, port = node_str, "25565"

        data = tcp_download_chunk(ip, port, chunk_name)
        if data is None:
            return None, f"❌ Failed to fetch chunk `{chunk_name}` from `{node_str}`. Node may be offline."
        full_encrypted += data

    # 4. Decrypt
    try:
        fernet = Fernet(decryption_key.encode())
        decrypted = fernet.decrypt(full_encrypted)
    except Exception:
        return None, "❌ Decryption failed. Wrong key or corrupted data."

    # 5. Save to temp file for download
    tmp_path = os.path.join(tempfile.gettempdir(), file_name)
    with open(tmp_path, "wb") as f:
        f.write(decrypted)

    return tmp_path, f"✅ File `{file_name}` decrypted and ready for download!"


# ─────────────────────────────────────────────
# BLOCKCHAIN EXPLORER
# ─────────────────────────────────────────────
def get_chain_display():
    """Return formatted blockchain for display"""
    return json.dumps({
        'chain': blockchain.chain,
        'length': len(blockchain.chain),
        'total_files': len(blockchain.get_all_files())
    }, indent=2)


def get_files_display():
    """Return list of all files on the network"""
    files = blockchain.get_all_files()
    if not files:
        return "No files uploaded yet."

    rows = []
    for f in files:
        ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(f['timestamp']))
        rows.append(f"| {f['file_name']} | {f['owner']} | {f['chunks']} | `{f['file_hash'][:16]}...` | {ts} |")

    header = "| File | Owner | Chunks | ID (truncated) | Uploaded |\n|---|---|---|---|---|"
    return header + "\n" + "\n".join(rows)


# ─────────────────────────────────────────────
# NETWORK STATUS
# ─────────────────────────────────────────────
def get_network_status():
    """Return node status"""
    nodes = get_live_nodes()
    if not nodes:
        return "⚠️ **No nodes online.** Start `smart_node.py` with Playit.gg tunnel to begin.\n"

    lines = [f"### 🟢 {len(nodes)} Node(s) Online\n"]
    for n in nodes:
        last = active_nodes.get(n, 0)
        ago = int(time.time() - last)
        lines.append(f"- `{n}` — last seen {ago}s ago")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CUSTOM CSS
# ─────────────────────────────────────────────
CUSTOM_CSS = """
/* ── Global ── */
.gradio-container {
    max-width: 1100px !important;
    font-family: 'Inter', 'Segoe UI', sans-serif !important;
}

/* ── Hero Banner ── */
.hero-section {
    background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    border-radius: 16px;
    padding: 40px 32px;
    text-align: center;
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
    border: 1px solid rgba(255,255,255,0.08);
}
.hero-section::before {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 60%);
    animation: pulse 6s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { transform: scale(1); opacity: 0.5; }
    50% { transform: scale(1.1); opacity: 1; }
}
.hero-section h1 {
    font-size: 2.4em;
    font-weight: 800;
    background: linear-gradient(135deg, #818cf8, #c084fc, #f472b6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0 0 8px 0;
    position: relative;
}
.hero-section p {
    color: rgba(255,255,255,0.7);
    font-size: 1.05em;
    margin: 4px 0;
    position: relative;
}

/* ── Tab Styling ── */
.tab-nav button {
    font-weight: 600 !important;
    font-size: 0.95em !important;
    padding: 10px 20px !important;
    border-radius: 10px 10px 0 0 !important;
}
.tab-nav button.selected {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    color: white !important;
}

/* ── Cards ── */
.card-panel {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 20px;
    backdrop-filter: blur(10px);
}

/* ── Result boxes ── */
.result-success {
    background: linear-gradient(135deg, rgba(34,197,94,0.1), rgba(16,185,129,0.05));
    border: 1px solid rgba(34,197,94,0.3);
    border-radius: 12px;
    padding: 16px;
}
.result-key {
    background: linear-gradient(135deg, rgba(234,179,8,0.1), rgba(245,158,11,0.05));
    border: 1px solid rgba(234,179,8,0.3);
    border-radius: 12px;
    padding: 16px;
}

/* ── Buttons ── */
.primary-btn {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 1em !important;
    padding: 12px 28px !important;
    transition: all 0.3s ease !important;
}
.primary-btn:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 25px rgba(99,102,241,0.4) !important;
}

/* ── Stats badges ── */
.stats-row {
    display: flex;
    gap: 12px;
    margin: 16px 0;
}
.stat-badge {
    background: rgba(99,102,241,0.1);
    border: 1px solid rgba(99,102,241,0.2);
    border-radius: 10px;
    padding: 12px 20px;
    text-align: center;
    flex: 1;
}
.stat-badge .num {
    font-size: 1.8em;
    font-weight: 800;
    color: #818cf8;
}
.stat-badge .label {
    font-size: 0.8em;
    color: rgba(255,255,255,0.5);
    text-transform: uppercase;
    letter-spacing: 1px;
}
"""

# ─────────────────────────────────────────────
# BUILD GRADIO UI
# ─────────────────────────────────────────────
with gr.Blocks(
    css=CUSTOM_CSS,
    theme=gr.themes.Soft(
        primary_hue="indigo",
        secondary_hue="purple",
        neutral_hue="slate",
        font=gr.themes.GoogleFont("Inter"),
    ).set(
        body_background_fill="linear-gradient(180deg, #0f172a 0%, #1e1b4b 100%)",
        body_background_fill_dark="linear-gradient(180deg, #0f172a 0%, #1e1b4b 100%)",
        block_background_fill="rgba(30,27,75,0.5)",
        block_background_fill_dark="rgba(30,27,75,0.5)",
        block_border_width="1px",
        block_border_color="rgba(255,255,255,0.06)",
        input_background_fill="rgba(15,23,42,0.8)",
        input_background_fill_dark="rgba(15,23,42,0.8)",
        button_primary_background_fill="linear-gradient(135deg, #6366f1, #8b5cf6)",
        button_primary_background_fill_hover="linear-gradient(135deg, #818cf8, #a78bfa)",
    ),
    title="BlockDrive — Decentralized P2P Storage"
) as demo:

    # ── Hero ──
    gr.HTML("""
    <div class="hero-section">
        <h1>⛓️ BlockDrive</h1>
        <p><strong>Decentralized P2P Secure File Storage</strong></p>
        <p>End-to-end encrypted • Blockchain verified • Distributed across peers</p>
    </div>
    """)

    # ── Stats Row ──
    with gr.Row():
        stats_display = gr.HTML(value="")

    def refresh_stats():
        nodes = get_live_nodes()
        files = blockchain.get_all_files()
        return f"""
        <div class="stats-row">
            <div class="stat-badge"><div class="num">{len(nodes)}</div><div class="label">Active Nodes</div></div>
            <div class="stat-badge"><div class="num">{len(files)}</div><div class="label">Files Stored</div></div>
            <div class="stat-badge"><div class="num">{len(blockchain.chain)}</div><div class="label">Blocks</div></div>
            <div class="stat-badge"><div class="num">AES-256</div><div class="label">Encryption</div></div>
        </div>
        """

    demo.load(fn=refresh_stats, outputs=stats_display)

    # ── Tabs ──
    with gr.Tabs():
        # ═══════════ UPLOAD TAB ═══════════
        with gr.Tab("📤 Upload", id="upload"):
            gr.Markdown("### Upload a file to the decentralized network\nYour file is encrypted, split into chunks, and distributed across storage nodes.")

            with gr.Row():
                with gr.Column(scale=2):
                    upload_file = gr.File(label="Drop your file here", file_types=None)
                    owner_input = gr.Textbox(
                        label="Owner Name",
                        placeholder="e.g., Alice",
                        value="Anonymous",
                        max_lines=1
                    )
                    upload_btn = gr.Button("🚀 Encrypt & Upload", variant="primary", elem_classes=["primary-btn"])

                with gr.Column(scale=2):
                    upload_result = gr.Markdown(label="Result", value="*Upload a file to begin...*")
                    upload_creds = gr.Markdown(label="Credentials", value="")

            upload_btn.click(
                fn=handle_upload,
                inputs=[upload_file, owner_input],
                outputs=[upload_result, upload_creds]
            )

        # ═══════════ DOWNLOAD TAB ═══════════
        with gr.Tab("📥 Download", id="download"):
            gr.Markdown("### Retrieve your file from the network\nEnter the File ID and Decryption Key you received during upload.")

            with gr.Row():
                with gr.Column(scale=2):
                    dl_file_id = gr.Textbox(
                        label="File ID (Merkle Root)",
                        placeholder="Paste your File ID here...",
                        max_lines=1
                    )
                    dl_key = gr.Textbox(
                        label="Decryption Key",
                        placeholder="Paste your decryption key here...",
                        max_lines=1
                    )
                    dl_btn = gr.Button("📥 Fetch & Decrypt", variant="primary", elem_classes=["primary-btn"])

                with gr.Column(scale=2):
                    dl_status = gr.Markdown(value="*Enter your credentials to download...*")
                    dl_file_output = gr.File(label="Your Decrypted File", interactive=False)

            dl_btn.click(
                fn=handle_download,
                inputs=[dl_file_id, dl_key],
                outputs=[dl_file_output, dl_status]
            )

        # ═══════════ EXPLORER TAB ═══════════
        with gr.Tab("⛓️ Blockchain", id="explorer"):
            gr.Markdown("### Blockchain Explorer\nView all blocks and file transactions on the ledger.")

            with gr.Row():
                explore_btn = gr.Button("🔄 Refresh Ledger", variant="primary", elem_classes=["primary-btn"])

            files_md = gr.Markdown(value="*Click refresh to load files...*")
            chain_json = gr.Code(label="Raw Blockchain", language="json", value="")

            def refresh_explorer():
                return get_files_display(), get_chain_display()

            explore_btn.click(fn=refresh_explorer, outputs=[files_md, chain_json])

        # ═══════════ NETWORK TAB ═══════════
        with gr.Tab("🌐 Network", id="network"):
            gr.Markdown("### Network Status\nView connected storage nodes and their health.")

            net_btn = gr.Button("🔄 Refresh Network", variant="primary", elem_classes=["primary-btn"])
            net_status = gr.Markdown(value="*Click refresh to check nodes...*")

            net_btn.click(fn=get_network_status, outputs=net_status)

    # ── Footer ──
    gr.HTML("""
    <div style="text-align:center; padding:20px; margin-top:20px; border-top: 1px solid rgba(255,255,255,0.06);">
        <p style="color:rgba(255,255,255,0.3); font-size:0.85em;">
            BlockDrive — Built with AES-256 Encryption, Merkle Trees, and P2P Networking
        </p>
    </div>
    """)


# ─────────────────────────────────────────────
# LAUNCH
# ─────────────────────────────────────────────
# Mount custom API routes for smart_node.py
# We do this AFTER Gradio Blocks is created to avoid
# schema generation issues

from fastapi import Request
from fastapi.responses import JSONResponse

@demo.load()
def mount_custom_api():
    """Mount REST API routes on startup"""
    app = demo.fastapi_app
    
    @app.post("/api/register")
    async def api_register(request: Request):
        data = await request.json()
        ip = data.get("ip", "")
        port = data.get("port", 25565)
        nodes = register_node(ip, int(port))
        return JSONResponse({"status": "registered", "nodes": nodes})
    
    @app.get("/api/get_nodes")
    async def api_get_nodes():
        return JSONResponse(get_live_nodes())
    
    print("[✓] API routes mounted")


if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)
