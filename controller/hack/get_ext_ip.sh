#!/usr/bin/env bash
if which ip 2>/dev/null 1>/dev/null; then
    ip route get 1.1.1.1 | grep -oP 'src \K\S+'
else
    # MacOS does not have ip, so we use route and ifconfig instead
    INTERFACE=$(route get 1.1.1.1 | grep interface | awk '{print $2}')
    ifconfig | grep $INTERFACE -A 10 | grep "inet " | grep -Fv 127.0.0.1 | awk '{print $2}' | head -n 1
fi
