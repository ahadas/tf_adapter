#!/bin/bash

rsync --daemon --config /etc/rsync.config --port 8090 && python3 /usr/local/server.py
