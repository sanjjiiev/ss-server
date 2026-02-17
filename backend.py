from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
import time
import os
import random
from blockchain import Blockchain

# Initialize Blockchain
blockchain = Blockchain()

active_nodes = {}  # {ip:port: timestamp}

app = FastAPI()

# ─────────────────────────────────────────────
# NODE REGISTRY LOGIC
# ─────────────────────────────────────────────
def register_node(ip: str, port: int):
    """Register storage node"""
    # Relay mode: port 0 means no direct connection, use ID only
    node = ip if port == 0 else f"{ip}:{port}"
    active_nodes[node] = time.time()
    print(f"[+] Node Registered: {node}")
    return list(active_nodes.keys())

def get_live_nodes():
    """Get active nodes (prune > 5 mins old)"""
    current = time.time()
    dead = [n for n, ts in active_nodes.items() if current - ts > 30]
    for n in dead:
        del active_nodes[n]
    return list(active_nodes.keys())

# ---------------------------------------------------------
# RELAY & POLLING SYSTEM (Firewall Bypass)
# ---------------------------------------------------------

RELAY_DIR = "relay_storage"
os.makedirs(RELAY_DIR, exist_ok=True)

REPLICATION_FACTOR = 2  # Store each chunk on 2 nodes

# {node_id: [task_1, task_2]}
node_tasks = {}
# Track pending confirmations: {chunk_name: set(node_ids)}
relay_pending = {}

@app.post("/api/relay_upload")
async def relay_upload(file: UploadFile = File(...), chunk_name: str = Form(...)):
    """Frontend uploads chunk here. We queue it for multiple Nodes (replication)."""
    try:
        # 1. Save to temp relay storage
        file_path = os.path.join(RELAY_DIR, chunk_name)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # 2. Assign to multiple nodes (replication)
        nodes = get_live_nodes()
        if not nodes:
            return {"status": "error", "message": "No active nodes"}
        
        num_replicas = min(REPLICATION_FACTOR, len(nodes))
        target_nodes = random.sample(nodes, num_replicas)
        
        # 3. Queue store task for each target node
        relay_pending[chunk_name] = set(target_nodes)
        for target in target_nodes:
            if target not in node_tasks:
                node_tasks[target] = []
            node_tasks[target].append({
                "type": "store",
                "chunk_name": chunk_name
            })
        
        return {"status": "queued", "target_nodes": target_nodes}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/poll_tasks")
def poll_tasks(node_id: str):
    """Node polls this to see if it has work."""
    if node_id in node_tasks and node_tasks[node_id]:
        # Return tasks and clear them (pop)
        tasks = node_tasks[node_id]
        node_tasks[node_id] = [] # Clear queue once fetched
        return {"tasks": tasks}
    return {"tasks": []}

@app.get("/api/download_relay/{chunk_name}")
def download_relay(chunk_name: str):
    """Node downloads the chunk from our relay storage."""
    file_path = os.path.join(RELAY_DIR, chunk_name)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "File not found"}

@app.post("/api/confirm_task")
def confirm_task(node_id: str, chunk_name: str, status: str):
    """Node confirms it saved the file. Delete relay copy only when ALL replicas confirm."""
    if status == "success":
        if chunk_name in relay_pending:
            relay_pending[chunk_name].discard(node_id)
            if len(relay_pending[chunk_name]) == 0:
                # All replicas confirmed — safe to delete
                file_path = os.path.join(RELAY_DIR, chunk_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                del relay_pending[chunk_name]
                print(f"[+] All replicas confirmed for {chunk_name}. Relay cleaned.")
        return {"status": "acknowledged"}
    return {"status": "ok"}

@app.post("/api/request_retrieval")
async def request_retrieval_endpoint(request: Request):
    try:
        data = await request.json()
        chunk_name = data.get("chunk_name")
        node_id = data.get("node_id")
        
        if not chunk_name or not node_id:
             return JSONResponse({"status": "error", "message": "Missing params"}, status_code=400)

        if node_id not in node_tasks:
            node_tasks[node_id] = []
        
        # Add task (avoid duplicates if possible, but simpler to just append)
        node_tasks[node_id].append({"type": "retrieve", "chunk_name": chunk_name})
        return JSONResponse({"status": "queued"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/relay_push")
async def relay_push(file: UploadFile = File(...), chunk_name: str = Form(...)):
    """Node pushes chunk here for Frontend to download."""
    try:
        file_path = os.path.join(RELAY_DIR, chunk_name)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        return {"status": "received"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────
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

@app.post("/api/add_transaction")
async def api_add_transaction(request: Request):
    data = await request.json()
    # Validate fields
    required = ['owner', 'file_hash', 'file_name', 'locations']
    if not all(k in data for k in required):
        return JSONResponse({"error": "Missing fields"}, status_code=400)
    
    # Add to blockchain
    index = blockchain.new_transaction(
        data['owner'], data['file_hash'],
        data['file_name'], data['locations']
    )
    # Mine block with Proof of Work & Persist
    block = blockchain.mine_block()
    blockchain.save_to_repo()
    
    return JSONResponse({
        "message": f"Transaction added to Block {block['index']}",
        "proof": block['proof']
    }, status_code=201)

@app.get("/api/get_file/{file_hash}")
async def api_get_file(file_hash: str):
    result = blockchain.get_file_location(file_hash)
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "File not found"}, status_code=404)

@app.get("/api/chain")
async def api_chain():
    return JSONResponse({
        "chain": blockchain.chain,
        "length": len(blockchain.chain),
        "files": len(blockchain.get_all_files())
    })

@app.get("/api/validate")
async def api_validate():
    """Validate the entire blockchain for tampering"""
    valid, bad_index = blockchain.validate_chain()
    return JSONResponse({
        "valid": valid,
        "blocks": len(blockchain.chain),
        "tampered_at": bad_index if not valid else None
    })

if __name__ == "__main__":
    # Internal port for API (not exposed publically directly)
    uvicorn.run(app, host="0.0.0.0", port=8000)
