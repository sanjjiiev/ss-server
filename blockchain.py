import hashlib
import json
import time
import os

# Path for persistent blockchain data
BLOCKCHAIN_FILE = "blockchain_data.json"

# Mining difficulty: number of leading zeros required in hash
# 2 = fast (demo), 4 = slow (production)
DIFFICULTY = 2


class Blockchain:
    def __init__(self):
        self.chain = []
        self.current_transactions = []

        # Try to load existing chain from file
        if os.path.exists(BLOCKCHAIN_FILE):
            self._load_from_file()
            # Validate loaded chain
            valid, bad_index = self.validate_chain()
            if valid:
                print(f"[+] Blockchain loaded & verified ({len(self.chain)} blocks) ✅")
            else:
                print(f"[!] TAMPERED CHAIN DETECTED at block {bad_index}! Rejecting.")
                print(f"[!] Resetting to genesis block.")
                self.chain = []
                self.current_transactions = []
                self._create_genesis()
        else:
            self._create_genesis()

    def _create_genesis(self):
        """Create the genesis (first) block with PoW"""
        self.new_block(previous_hash='1', proof=self.proof_of_work(0))
        print("[+] Genesis block created (mined)")

    def _load_from_file(self):
        """Load blockchain from JSON file"""
        try:
            with open(BLOCKCHAIN_FILE, 'r') as f:
                data = json.load(f)
                self.chain = data.get('chain', [])
                self.current_transactions = data.get('pending', [])
        except (json.JSONDecodeError, IOError) as e:
            print(f"[-] Failed to load blockchain: {e}. Creating fresh chain.")
            self.chain = []
            self.current_transactions = []
            self._create_genesis()

    def _save_to_file(self):
        """Save blockchain to local JSON file"""
        try:
            data = {
                'chain': self.chain,
                'pending': self.current_transactions,
                'last_saved': time.time()
            }
            with open(BLOCKCHAIN_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"[-] Failed to save blockchain: {e}")

    def save_to_repo(self):
        """
        Persist blockchain by committing to the HF Space repo.
        Called after adding new blocks.
        """
        self._save_to_file()
        try:
            from huggingface_hub import HfApi
            space_id = os.environ.get("SPACE_ID")
            if space_id:
                api = HfApi()
                api.upload_file(
                    path_or_fileobj=BLOCKCHAIN_FILE,
                    path_in_repo=BLOCKCHAIN_FILE,
                    repo_id=space_id,
                    repo_type="space",
                    commit_message=f"Auto-save blockchain ({len(self.chain)} blocks)"
                )
                print(f"[+] Blockchain committed to HF repo")
        except Exception as e:
            print(f"[-] HF commit failed (local save still OK): {e}")

    # ─────────────────────────────────────────────
    # PROOF OF WORK (Mining)
    # ─────────────────────────────────────────────
    def proof_of_work(self, last_proof):
        """
        Find a number 'proof' such that hash(last_proof, proof)
        has DIFFICULTY leading zeros. This is the mining process.
        """
        proof = 0
        while not self.valid_proof(last_proof, proof):
            proof += 1
        return proof

    @staticmethod
    def valid_proof(last_proof, proof):
        """Check if hash(last_proof, proof) has required leading zeros"""
        guess = f'{last_proof}{proof}'.encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:DIFFICULTY] == "0" * DIFFICULTY

    # ─────────────────────────────────────────────
    # CHAIN VALIDATION (Tamper Detection)
    # ─────────────────────────────────────────────
    def validate_chain(self):
        """
        Verify the entire chain is valid:
        1. Each block's previous_hash matches the hash of the prior block
        2. Each block's proof of work is valid
        Returns (True, -1) if valid, (False, bad_block_index) if tampered
        """
        for i in range(1, len(self.chain)):
            block = self.chain[i]
            prev_block = self.chain[i - 1]

            # Check 1: Hash chain integrity
            if block['previous_hash'] != self.hash(prev_block):
                return False, i

            # Check 2: Proof of Work validity
            if not self.valid_proof(prev_block['proof'], block['proof']):
                return False, i

        return True, -1

    # ─────────────────────────────────────────────
    # BLOCK & TRANSACTION OPERATIONS
    # ─────────────────────────────────────────────
    def new_block(self, proof, previous_hash=None):
        """Creates a new Block and adds it to the chain"""
        block = {
            'index': len(self.chain) + 1,
            'timestamp': time.time(),
            'transactions': self.current_transactions,
            'proof': proof,
            'previous_hash': previous_hash or self.hash(self.chain[-1]),
        }

        # Reset the current list of transactions
        self.current_transactions = []
        self.chain.append(block)
        return block

    def mine_block(self):
        """Mine a new block using Proof of Work"""
        last_block = self.last_block
        last_proof = last_block['proof']
        proof = self.proof_of_work(last_proof)
        previous_hash = self.hash(last_block)
        block = self.new_block(proof, previous_hash)
        print(f"[+] Block #{block['index']} mined (proof={proof})")
        return block

    def new_transaction(self, owner, file_hash, file_name, chunks_metadata):
        """
        Creates a new transaction to go into the next Mined Block
        """
        self.current_transactions.append({
            'owner': owner,
            'file_hash': file_hash,
            'file_name': file_name,
            'locations': chunks_metadata,
            'timestamp': time.time()
        })
        return self.last_block['index'] + 1

    @staticmethod
    def hash(block):
        """Hashes a Block (SHA-256)"""
        block_string = json.dumps(block, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    @property
    def last_block(self):
        return self.chain[-1]

    def get_file_location(self, file_hash):
        """Search the blockchain for a specific file by its hash"""
        for block in self.chain:
            for tx in block['transactions']:
                if tx['file_hash'] == file_hash:
                    return tx
        return None

    def get_all_files(self):
        """Return all file transactions from the blockchain"""
        files = []
        for block in self.chain:
            for tx in block['transactions']:
                files.append({
                    'file_name': tx['file_name'],
                    'file_hash': tx['file_hash'],
                    'owner': tx['owner'],
                    'chunks': len(tx['locations']),
                    'timestamp': tx.get('timestamp', block['timestamp'])
                })
        return files