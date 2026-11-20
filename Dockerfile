FROM python:3.12

WORKDIR /app

COPY pyproject.toml /app/
COPY manufacturing/ /app/manufacturing/
COPY server.py /app/

# Install the manufacturing package and dependencies
RUN pip install /app/

# Expose the port of the server
EXPOSE 5000

# Report the container as unhealthy if the server stops answering
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=4)"

# Run the server
CMD ["python", "server.py"]
