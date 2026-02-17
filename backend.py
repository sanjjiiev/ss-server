from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
import time
import os
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
    node = f"{ip}:{port}"
    active_nodes[node] = time.time()
    print(f"[+] Node Registered: {node}")
    return list(active_nodes.keys())

def get_live_nodes():
    """Get active nodes (prune > 5 mins old)"""
    current = time.time()
    dead = [n for n, ts in active_nodes.items() if current - ts > 300]
    for n in dead:
        del active_nodes[n]
    return list(active_nodes.keys())

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
    # Mine block & Persist
    blockchain.new_block(proof=100)
    blockchain.save_to_repo()
    
    return JSONResponse({"message": f"Transaction added to Block {index}"}, status_code=201)

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

if __name__ == "__main__":
    # Internal port for API (not exposed publically directly)
    uvicorn.run(app, host="0.0.0.0", port=8000)
