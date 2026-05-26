FROM python:3.12

WORKDIR /app

COPY pyproject.toml /app/
COPY manufacturing/ /app/manufacturing/
COPY saved_plots/ /app/saved_plots/
COPY server.py /app/

# Install the manufacturing package and dependencies
RUN pip install /app/

# Expose the port of the server
EXPOSE 5000

# Run the server
CMD ["python", "server.py"]
