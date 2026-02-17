import hashlib
import json
import time
import os

# Path for persistent blockchain data
BLOCKCHAIN_FILE = "blockchain_data.json"


class Blockchain:
    def __init__(self):
        self.chain = []
        self.current_transactions = []

        # Try to load existing chain from file
        if os.path.exists(BLOCKCHAIN_FILE):
            self._load_from_file()
            print(f"[+] Blockchain loaded from {BLOCKCHAIN_FILE} ({len(self.chain)} blocks)")
        else:
            # Create the "Genesis Block" (The first block)
            self.new_block(previous_hash='1', proof=100)
            print("[+] Genesis block created")

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
            self.new_block(previous_hash='1', proof=100)

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