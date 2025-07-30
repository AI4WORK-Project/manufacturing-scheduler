FROM python:3.10

WORKDIR /app

# Install Google Chrome
RUN apt-get update && apt-get install -y wget
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN apt-get install -y ./google-chrome-stable_current_amd64.deb

COPY pyproject.toml /app/
COPY manufacturing/ /app/manufacturing/
COPY saved_plots/ /app/saved_plots/
COPY server.py /app/
COPY configuration.json /app/

# Install the manufacturing package and dependencies
RUN pip install /app/

# Expose the port of the server
EXPOSE 5000

# Run the server
CMD ["python", "server.py"]
