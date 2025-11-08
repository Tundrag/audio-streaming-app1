import subprocess
import socket
import requests
import asyncio
import sys
from datetime import datetime

async def check_mega_connectivity():
    results = {
        "timestamp": datetime.now().isoformat(),
        "basic_connectivity": {},
        "mega_specific": {},
        "detailed_info": {}
    }
    
    # 1. Basic Internet Connectivity
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        results["basic_connectivity"]["internet"] = "Connected"
    except OSError:
        results["basic_connectivity"]["internet"] = "No internet connection"

    # 2. DNS Resolution
    try:
        mega_ip = socket.gethostbyname("mega.nz")
        results["basic_connectivity"]["dns"] = f"Resolves to {mega_ip}"
    except socket.gaierror:
        results["basic_connectivity"]["dns"] = "DNS resolution failed"

    # 3. MEGA CLI Status
    try:
        whoami = subprocess.run(['mega-whoami'], capture_output=True, text=True, timeout=5)
        results["mega_specific"]["login_status"] = whoami.stdout.strip() or whoami.stderr.strip()
        
        # Storage info
        df = subprocess.run(['mega-df'], capture_output=True, text=True, timeout=5)
        results["mega_specific"]["storage_info"] = df.stdout.strip() or df.stderr.strip()
        
    except subprocess.TimeoutExpired:
        results["mega_specific"]["cli_status"] = "Command timed out"
    except FileNotFoundError:
        results["mega_specific"]["cli_status"] = "MEGA CLI not found"
    except Exception as e:
        results["mega_specific"]["cli_status"] = f"Error: {str(e)}"

    # 4. API Endpoint Checks
    endpoints = [
        "https://g.api.mega.co.nz/cs",
        "https://mega.nz"
    ]
    
    results["detailed_info"]["endpoints"] = {}
    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            results["detailed_info"]["endpoints"][endpoint] = {
                "status_code": response.status_code,
                "reachable": response.status_code == 200
            }
        except requests.exceptions.RequestException as e:
            results["detailed_info"]["endpoints"][endpoint] = {
                "error": str(e)
            }

    return results

def print_results(results):
    print("\n=== MEGA Connectivity Check Results ===")
    print(f"Time: {results['timestamp']}\n")
    
    print("Basic Connectivity:")
    for k, v in results["basic_connectivity"].items():
        print(f"  {k}: {v}")
    
    print("\nMEGA Specific:")
    for k, v in results["mega_specific"].items():
        print(f"  {k}: {v}")
    
    print("\nEndpoint Checks:")
    for endpoint, info in results["detailed_info"]["endpoints"].items():
        print(f"  {endpoint}:")
        for k, v in info.items():
            print(f"    {k}: {v}")

if __name__ == "__main__":
    results = asyncio.run(check_mega_connectivity())
    print_results(results)