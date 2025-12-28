from flask import Flask, request, render_template, jsonify
import subprocess, os, signal
app = Flask(__name__)

PYMD3 = os.path.expanduser("/home/ghost/ghost/bin/python3")
tunneld_proc = None
loc_proc = None
LOG_DIR = "/tmp/pymd3_logs"
os.makedirs(LOG_DIR, exist_ok=True)

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
    out_file = open(os.path.join(LOG_DIR, f"{name}_out.log"), "a")
    err_file = open(os.path.join(LOG_DIR, f"{name}_err.log"), "a")
    return subprocess.Popen(argv, stdout=out_file, stderr=err_file, text=True)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/init", methods=["POST"])
def init():
    global tunneld_proc
    kill_existing_processes()
    tunneld_proc = run_cmd_bg_log(
        ["sudo", "-n", PYMD3, "-m", "pymobiledevice3", "remote", "tunneld"],
        "tunneld"
    )
    return "TUNNEL_STARTING\n"

@app.route("/stop_tunnel", methods=["POST"])
def stop_tunnel():
    global tunneld_proc
    if tunneld_proc:
        tunneld_proc.terminate()
        tunneld_proc = None
        return "Tunnel arrêté\n"
    return "Aucun tunnel actif\n"

@app.route("/apply", methods=["POST"])
def apply():
    global loc_proc
    data = request.get_json(force=True)
    lat = str(data.get("lat", "")).strip()
    lon = str(data.get("lon", "")).strip()
    if not lat or not lon:
        return "Latitude et longitude requises\n", 400
    if loc_proc and loc_proc.poll() is None:
        loc_proc.terminate()
    loc_proc = run_cmd_bg_log([
        PYMD3, "-m", "pymobiledevice3",
        "developer", "dvt", "simulate-location", "set",
        "--tunnel", "", "--", lat, lon
    ], "loc")
    return f"LOCATION_APPLYING\n"

@app.route("/stop_location", methods=["POST"])
def stop_location():
    global loc_proc
    if loc_proc:
        loc_proc.terminate()
        loc_proc = None
        return "Localisation arrêtée\n"
    return "Pas de localisation active\n"

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
