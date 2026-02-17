FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    nginx \
    git \
    git-lfs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy Nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Make start script executable
RUN chmod +x start.sh

# Expose the port HF Spaces uses (7860)
EXPOSE 7860

# Run the start script
CMD ["./start.sh"]
