#!/bin/bash
echo "======================================"
echo "    FARMCAM DIAGNOSTIC SCRIPT         "
echo "======================================"
echo ""

echo ">>> 1. Checking if FarmCam is securely on the Hotspot..."
nmcli connection show --active | grep wifi

echo ""
echo ">>> 2. Finding your TRUE Local IP Address..."
IP_ADDR=$(hostname -I | awk '{print $1}')
echo "YOUR RASPBERRY PI IP IS: $IP_ADDR"
echo "(THIS IS THE EXACT NUMBER YOU MUST PUT IN THE APP!)"

echo ""
echo ">>> 3. Checking if the background App Server is Running..."
SERVICE_STATUS=$(systemctl is-active farmcam)
echo "Service Status: $SERVICE_STATUS"

echo ""
echo ">>> 4. Testing if the API responds to local pings..."
curl -m 3 -X GET http://localhost:8000/api/status -s
if [ $? -eq 0 ]; then
    echo "SUCCESS! The Server is alive and responding perfectly on Port 8000!"
else
    echo "FAILED! The server is NOT responding."
    echo ""
    echo ">>> REASON: Showing the last 15 lines of Crash Logs..."
    sudo journalctl -u farmcam -n 15 --no-pager
fi

echo ""
echo "======================================"
