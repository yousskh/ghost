from flask import Flask, request, render_template, jsonify
import subprocess, os, signal, json, time, threading
app = Flask(__name__)

PYMD3 = os.path.expanduser("/home/ghost/ghost/bin/python3")
tunneld_proc = None
loc_proc = None
LOG_DIR = "/tmp/pymd3_logs"
os.makedirs(LOG_DIR, exist_ok=True)

# État de la connexion
connection_state = {
    "connected": False,
    "device_info": None,
    "tunnel_ready": False
}

def kill_existing_processes():
    """Tue tous les process pymobiledevice3 ou tunneld encore actifs"""
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,cmd"], text=True)
        for line in out.splitlines():
            if "pymobiledevice3" in line or "tunneld" in line:
                pid = int(line.split()[0])
                if pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
    except Exception as e:
        print("Erreur kill_existing_processes:", e)

def run_cmd_bg_log(argv, name):
    """Exécute en arrière-plan avec stdout/stderr séparés"""
    # Vider les anciens logs
    out_path = os.path.join(LOG_DIR, f"{name}_out.log")
    err_path = os.path.join(LOG_DIR, f"{name}_err.log")
    open(out_path, "w").close()
    open(err_path, "w").close()
    out_file = open(out_path, "a")
    err_file = open(err_path, "a")
    return subprocess.Popen(argv, stdout=out_file, stderr=err_file, text=True)

def get_device_info():
    """Récupère les informations de l'appareil connecté via lockdown"""
    try:
        result = subprocess.run(
            [PYMD3, "-m", "pymobiledevice3", "lockdown", "info"],
            capture_output=True, text=True, timeout=10
        )
        print(f"[DEBUG] lockdown info stdout: {result.stdout[:500] if result.stdout else 'EMPTY'}")
        print(f"[DEBUG] lockdown info stderr: {result.stderr[:500] if result.stderr else 'EMPTY'}")
        print(f"[DEBUG] return code: {result.returncode}")
        
        if result.returncode == 0 and result.stdout:
            # Essayer de parser comme JSON d'abord
            try:
                import json
                data = json.loads(result.stdout)
                print(f"[DEBUG] Parsed as JSON: {list(data.keys())[:10]}")
                return data
            except json.JSONDecodeError:
                pass
            
            # Sinon parser comme key: value
            info = {}
            for line in result.stdout.splitlines():
                if ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip()
                        info[key] = value
            print(f"[DEBUG] Parsed as key:value - keys: {list(info.keys())[:10]}")
            return info if info else None
    except Exception as e:
        print(f"Erreur get_device_info: {e}")
    return None

def check_tunnel_status():
    """Vérifie si le tunnel est prêt en regardant les logs"""
    try:
        err_path = os.path.join(LOG_DIR, "tunneld_err.log")
        if os.path.exists(err_path):
            with open(err_path, "r") as f:
                content = f.read()
                # Le tunnel est prêt quand il affiche l'adresse du tunnel
                if "Created tunnel" in content or "Tunnel established" in content or "tunnel --" in content.lower():
                    return True
                # Vérifier aussi dans stdout
        out_path = os.path.join(LOG_DIR, "tunneld_out.log")
        if os.path.exists(out_path):
            with open(out_path, "r") as f:
                content = f.read()
                if "Created tunnel" in content or "Tunnel established" in content or "fd" in content:
                    return True
    except Exception as e:
        print(f"Erreur check_tunnel_status: {e}")
    return False

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/debug_device", methods=["GET"])
def debug_device():
    """Debug: retourne la sortie brute de lockdown info"""
    try:
        result = subprocess.run(
            [PYMD3, "-m", "pymobiledevice3", "lockdown", "info"],
            capture_output=True, text=True, timeout=15
        )
        return jsonify({
            "stdout": result.stdout[:2000] if result.stdout else None,
            "stderr": result.stderr[:2000] if result.stderr else None,
            "returncode": result.returncode
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/device_info", methods=["GET"])
def device_info():
    """Retourne les informations de l'appareil connecté"""
    info = get_device_info()
    if info:
        # Debug: retourner toutes les clés disponibles
        return jsonify({
            "connected": True,
            "raw_keys": list(info.keys())[:20],
            "info": {
                "name": info.get("DeviceName") or info.get("device_name") or info.get("Name") or "Inconnu",
                "ios_version": info.get("ProductVersion") or info.get("product_version") or info.get("Version") or "Inconnue",
                "model": info.get("ProductType") or info.get("product_type") or info.get("Model") or "Inconnu",
                "udid": (info.get("UniqueDeviceID") or info.get("udid") or info.get("UDID") or "Inconnu")[:8] + "..." if (info.get("UniqueDeviceID") or info.get("udid") or info.get("UDID")) else "Inconnu"
            }
        })
    return jsonify({"connected": False, "info": None})

@app.route("/connect", methods=["POST"])
def connect():
    """Démarre le tunnel et retourne les infos de l'appareil"""
    global tunneld_proc, connection_state
    
    # Vérifier d'abord si un appareil est connecté
    device = get_device_info()
    if not device:
        return jsonify({
            "success": False,
            "error": "Aucun iPhone détecté. Vérifiez la connexion USB et que l'appareil est déverrouillé."
        }), 400
    
    kill_existing_processes()
    tunneld_proc = run_cmd_bg_log(
        ["sudo", "-n", PYMD3, "-m", "pymobiledevice3", "remote", "tunneld"],
        "tunneld"
    )
    
    # Attendre que le tunnel soit prêt (max 15 secondes)
    for _ in range(30):
        time.sleep(0.5)
        if check_tunnel_status():
            connection_state["connected"] = True
            connection_state["tunnel_ready"] = True
            connection_state["device_info"] = device
            return jsonify({
                "success": True,
                "device": {
                    "name": device.get("DeviceName", "Inconnu"),
                    "ios_version": device.get("ProductVersion", "Inconnue"),
                    "model": device.get("ProductType", "Inconnu"),
                    "udid": device.get("UniqueDeviceID", "Inconnu")[:8] + "..." if device.get("UniqueDeviceID") else "Inconnu"
                }
            })
    
    # Le tunnel n'a pas pu être établi
    if tunneld_proc:
        tunneld_proc.terminate()
        tunneld_proc = None
    return jsonify({
        "success": False,
        "error": "Impossible d'établir le tunnel. Vérifiez que le mode développeur est activé."
    }), 500

@app.route("/disconnect", methods=["POST"])
def disconnect():
    """Arrête le tunnel"""
    global tunneld_proc, loc_proc, connection_state
    
    if loc_proc:
        loc_proc.terminate()
        loc_proc = None
    
    if tunneld_proc:
        tunneld_proc.terminate()
        tunneld_proc = None
    
    kill_existing_processes()
    
    connection_state["connected"] = False
    connection_state["tunnel_ready"] = False
    connection_state["device_info"] = None
    
    return jsonify({"success": True, "message": "Déconnecté"})

@app.route("/status", methods=["GET"])
def status():
    """Retourne l'état actuel de la connexion"""
    global connection_state, tunneld_proc
    
    # Vérifier si le processus est toujours actif
    if tunneld_proc and tunneld_proc.poll() is not None:
        connection_state["connected"] = False
        connection_state["tunnel_ready"] = False
        tunneld_proc = None
    
    return jsonify(connection_state)

@app.route("/apply", methods=["POST"])
def apply():
    global loc_proc, connection_state
    
    if not connection_state["connected"]:
        return jsonify({"success": False, "error": "iPhone non connecté"}), 400
    
    data = request.get_json(force=True)
    lat = str(data.get("lat", "")).strip()
    lon = str(data.get("lon", "")).strip()
    
    if not lat or not lon:
        return jsonify({"success": False, "error": "Latitude et longitude requises"}), 400
    
    if loc_proc and loc_proc.poll() is None:
        loc_proc.terminate()
    
    loc_proc = run_cmd_bg_log([
        PYMD3, "-m", "pymobiledevice3",
        "developer", "dvt", "simulate-location", "set",
        "--tunnel", "", "--", lat, lon
    ], "loc")
    
    # Attendre un peu pour vérifier si la commande a réussi
    time.sleep(1)
    
    return jsonify({"success": True, "message": f"Position appliquée: {lat}, {lon}"})

@app.route("/stop_location", methods=["POST"])
def stop_location():
    global loc_proc
    
    if loc_proc:
        loc_proc.terminate()
        loc_proc = None
    
    # Exécuter la commande pour rétablir la vraie position
    try:
        subprocess.run([
            PYMD3, "-m", "pymobiledevice3",
            "developer", "dvt", "simulate-location", "clear",
            "--tunnel", ""
        ], capture_output=True, timeout=5)
    except:
        pass
    
    return jsonify({"success": True, "message": "Position réelle rétablie"})

@app.route("/logs/<proc>", methods=["GET"])
def logs(proc):
    base = os.path.join(LOG_DIR, proc)
    out = base + "_out.log"
    err = base + "_err.log"
    result = {"stdout": "", "stderr": ""}
    if os.path.exists(out):
        with open(out, "r") as f: result["stdout"] = f.read()
    if os.path.exists(err):
        with open(err, "r") as f: result["stderr"] = f.read()
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
