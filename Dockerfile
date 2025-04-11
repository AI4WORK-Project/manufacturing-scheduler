FROM python:3.10

WORKDIR /app

COPY pyproject.toml /app/
COPY manufacturing/ /app/manufacturing/
COPY server.py /app/
COPY configuration.json /app/

# Install the manufacturing package and dependencies
RUN pip install /app/

# Expose the port of the server
EXPOSE 5000

# Run the server
CMD ["python", "server.py"]
