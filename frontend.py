import streamlit as st
import requests
import socket
import hashlib
import os
import tempfile
import time
from cryptography.fernet import Fernet
import json

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
API_URL = "http://localhost:7860/api"  # Nginx routes /api to backend

st.set_page_config(
    page_title="BlockDrive",
    page_icon="⛓️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .reportview-container {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
        color: white;
    }
    .stButton>button {
        background: linear-gradient(135deg, #6366f1, #8b5cf6);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-weight: 600;
    }
    .success-box {
        padding: 1rem;
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.2);
        border-radius: 8px;
        color: #d1fae5;
    }
    .warning-box {
        padding: 1rem;
        background: rgba(245, 158, 11, 0.1);
        border: 1px solid rgba(245, 158, 11, 0.2);
        border-radius: 8px;
        color: #fcd34d;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def get_nodes():
    try:
        resp = requests.get(f"{API_URL}/get_nodes", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except:
        return []
    return []

def http_relay_upload(chunk_data, chunk_name):
    try:
        files = {"file": (chunk_name, chunk_data)}
        data = {"chunk_name": chunk_name}
        resp = requests.post(f"{API_URL}/relay_upload", files=files, data=data, timeout=30)
        
        if resp.status_code == 200:
            res = resp.json()
            if res.get("status") == "queued":
                return True, res.get("target_node"), ""
            else:
                return False, None, res.get("message")
        return False, None, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, None, str(e)

def http_relay_download(node_id, chunk_name):
    try:
        # 1. Request Retrieval
        # Extracts actual ID if format is "ID:PORT" (which is common legacy format or if register sends port)
        if ":" in node_id:
            node_id = node_id.split(":")[0]
            
        payload = {"chunk_name": chunk_name, "node_id": node_id}
        requests.post(f"{API_URL}/request_retrieval", json=payload, timeout=5)
        
        # 2. Poll for file (up to 30s)
        for _ in range(30):
            r = requests.get(f"{API_URL}/download_relay/{chunk_name}", timeout=5)
            if r.status_code == 200:
                return r.content
            time.sleep(1)
            
        return None
    except Exception as e:
        return None

def build_merkle_tree(chunks):
    """Build Merkle Root from list of chunk byte arrays"""
    hashes = [hashlib.sha256(c).hexdigest() for c in chunks]
    if not hashes: return ""
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
# UI LAYOUT
# ─────────────────────────────────────────────

st.title("⛓️ BlockDrive")
st.markdown("**Decentralized P2P Secure File Storage**")

tab1, tab2, tab3, tab4 = st.tabs(["📤 Upload", "📥 Download", "⛓️ Blockchain", "🌐 Network"])

# ── UPLOAD TAB ──
with tab1:
    st.header("Upload File")
    uploaded_file = st.file_uploader("Choose a file", type=None)
    owner_name = st.text_input("Owner Name", "Anonymous")
    
    if st.button("🚀 Encrypt & Upload"):
        if uploaded_file is None:
            st.error("Please select a file first.")
        else:
            file_bytes = uploaded_file.read()
            original_name = uploaded_file.name
            
            if len(file_bytes) > MAX_FILE_SIZE:
                 st.error(f"File too large. Max: {MAX_FILE_SIZE // (1024*1024)} MB")
            else:
                status = st.empty()
                status.info("🔐 Encrypting...")
                
                # 1. Encrypt
                key = Fernet.generate_key()
                fernet = Fernet(key)
                encrypted = fernet.encrypt(file_bytes)
                
                # 2. Shard
                status.info("✂️ Sharding...")
                chunks = [encrypted[i:i + CHUNK_SIZE] for i in range(0, len(encrypted), CHUNK_SIZE)]
                
                # 3. Relay Upload
                status.info(f"📡 Uploading {len(chunks)} chunks to Relay...")
                location_map = {}
                failed = []
                progress_bar = st.progress(0)
                
                for i, chunk_data in enumerate(chunks):
                    chunk_name = f"{original_name}.part_{i}"
                    success, target_node, error_msg = http_relay_upload(chunk_data, chunk_name)
                    
                    if success:
                        location_map[chunk_name] = target_node
                    else:
                        failed.append(f"Chunk {i}: {error_msg}")
                    
                    progress_bar.progress((i + 1) / len(chunks))

                if not location_map:
                     st.error(f"❌ Upload to Relay failed. Last error: {error_msg if 'error_msg' in locals() else 'Unknown'}")
                     if failed:
                         with st.expander("See error details"):
                             for f in failed:
                                 st.write(f)
                else:
                    status.info("⛓️ Recording on blockchain...")
                    merkle_root = build_merkle_tree(chunks)
                    
                    payload = {
                        "owner": owner_name,
                        "file_hash": merkle_root,
                        "file_name": original_name,
                        "locations": location_map
                    }
                    
                    try:
                        r = requests.post(f"{API_URL}/add_transaction", json=payload)
                        if r.status_code == 201:
                            status.success("✅ File Uploaded & Transaction Recorded!")
                            st.markdown(f"""
                            <div class="success-box">
                                <h4>Download Credentials (SAVE THIS!)</h4>
                                <p><b>File ID (Merkle Root):</b> <code>{merkle_root}</code></p>
                                <p><b>Decryption Key:</b> <code>{key.decode()}</code></p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            if failed:
                                st.warning(f"⚠️ {len(failed)} chunks failed to upload.")
                        else:
                            st.error(f"❌ Blockchain error: {r.text}")
                    except Exception as e:
                        st.error(f"❌ API connection failed: {e}")

# ── DOWNLOAD TAB ──
with tab2:
    st.header("Download File")
    
    col1, col2 = st.columns(2)
    with col1:
        dl_id = st.text_input("File ID (Merkle Root)")
    with col2:
        dl_key = st.text_input("Decryption Key")
        
    if st.button("📥 Fetch & Decrypt"):
        if not dl_id or not dl_key:
            st.error("Please enter both File ID and Key.")
        else:
            status = st.empty()
            status.info("🔍 Looking up file on blockchain...")
            
            try:
                # 1. Lookup
                r = requests.get(f"{API_URL}/get_file/{dl_id}")
                if r.status_code != 200:
                    st.error("❌ File not found on blockchain.")
                else:
                    meta = r.json()
                    locations = meta['locations']
                    fname = meta['file_name']
                    
                    status.info(f"📡 Found {len(locations)} chunks. Fetching...")
                    
                    # 2. Sort & Fetch
                    sorted_chunks = sorted(locations.keys(), key=lambda x: int(x.split('_')[-1]))
                    full_enc = b""
                    
                    progress = st.progress(0)
                    
                    for i, chunk_name in enumerate(sorted_chunks):
                        node_str = locations[chunk_name]
                        # Use Relay Download
                        data = http_relay_download(node_str, chunk_name)
                        
                        if not data:
                             st.error(f"❌ Failed to fetch chunk {chunk_name} from {node_str}")
                             full_enc = None
                             break
                        full_enc += data
                        progress.progress((i + 1) / len(sorted_chunks))
                        
                    if full_enc:
                        # 3. Decrypt
                        status.info("🔓 Decrypting...")
                        try:
                            fernet = Fernet(dl_key.encode())
                            decrypted = fernet.decrypt(full_enc)
                            
                            st.download_button(
                                label=f"💾 Download {fname}",
                                data=decrypted,
                                file_name=fname,
                                mime="application/octet-stream"
                            )
                            status.success("✅ File ready for download!")
                        except:
                            st.error("❌ Decryption failed! Invalid key?")
            except Exception as e:
                st.error(f"❌ Error: {e}")


# ── BLOCKCHAIN TAB ──
with tab3:
    st.header("Blockchain Explorer")
    if st.button("🔄 Refresh Ledger"):
        try:
            r = requests.get(f"{API_URL}/chain")
            if r.status_code == 200:
                data = r.json()
                st.metric("Total Blocks", data['length'])
                st.metric("Total Files", data['files'])
                st.json(data['chain'])
        except Exception as e:
            st.error(f"API Error: {e}")

# ── NETWORK TAB ──
with tab4:
    st.header("Network Status")
    if st.button("🔄 Refresh Nodes"):
         nodes = get_nodes()
         if nodes:
             st.success(f"{len(nodes)} Active Storage Node(s)")
             for n in nodes:
                 st.code(n)
         else:
             st.warning("No active nodes found. Start smart_node.py locally!")
