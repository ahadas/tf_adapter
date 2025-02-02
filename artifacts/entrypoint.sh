#!/bin/bash

rsync --daemon --no-detach --config /etc/rsync.config --port 8090
