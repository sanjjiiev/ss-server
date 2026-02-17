#!/bin/bash

# Start Nginx
echo "Starting Nginx..."
nginx -c /app/nginx.conf &

# Start Backend API (FastAPI)
echo "Starting Backend API..."
uvicorn backend:app --host 0.0.0.0 --port 8000 &

# Start Frontend UI (Streamlit)
echo "Starting Frontend UI..."
streamlit run frontend.py --server.port 8501 --server.address 0.0.0.0 &

# Wait for any process to exit
wait -n
  
# Exit with status of process that exited first
exit $?
