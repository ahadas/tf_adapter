FROM fedora:latest

RUN dnf -y install python3 python3-pip && \
    dnf clean all
RUN pip3 install kubernetes
COPY server.py /usr/local/server.py
ENTRYPOINT ["python3", "/usr/local/server.py"]
