from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import utils
import os
import sqlite_store

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

base_dir = "/Users/r/projects/routstr_main/nostr_mcp"
db_path = os.path.join(base_dir, "goose.db")

# Ensure the application database is initialized at startup (not the API keys DB)
try:
    _conn = sqlite_store.get_connection(db_path)
    sqlite_store.initialize_database(_conn)
    _conn.close()
except Exception as e:
    print(f"Failed to initialize database at {db_path}: {e}")

@app.route('/run', methods=['POST'])
def run_command():
    data = request.get_json()
    if not data or "npub" not in data or "since" not in data or "curr_timestamp" not in data:
        return jsonify({"error": "Missing required parameters"}), 400
    
    npub = data["npub"]
    since = data["since"]
    curr_timestamp = data["curr_timestamp"]
    instruction = data.get("instruction", "Posts that contain useful information that educate me in someway or the other. Shitposting should be avoided. Low effort notes should be avoided. ")
    db_path = os.path.join(base_dir, "goose.db")

    # If job already completed for these parameters, return formatted output from DB
    try:
        print(npub, since, curr_timestamp)
        job_status = utils.get_job_status(npub, since, curr_timestamp, db_path=db_path)
        if job_status == "success":
            print("JOB COMPLETED - returning formatted output from DB")
            formatted_result = utils.load_formatted_npub_output(npub, since, curr_timestamp)
            return jsonify(formatted_result)
    except Exception as e:
        return jsonify({"error": f"Failed to check job status: {str(e)}"}), 500
    # Check if job is already queued/running for these parameters
    try:
        job_status = utils.get_job_status(npub, since, curr_timestamp, db_path=db_path)
        if job_status in ("queued", "running"):
            print("COMMAND ALREADY RUNNING - returning empty JSON")
            return jsonify({})
    except Exception as e:
        return jsonify({"error": f"Failed to check running commands: {str(e)}"}), 500
    
    # Files don't exist and no command running, run the command to create them
    cmd = f"{base_dir}/run_goose.sh {npub} {since} {curr_timestamp} \"{instruction}\" {base_dir}"
    print("FILES DO NOT EXIST. RUNNING new")
    
    # Mark command as running before starting
    try:
        utils.mark_command_running(npub, since, curr_timestamp, db_path=db_path)
        print(f"Marked command as running for {npub}_{since}_{curr_timestamp}")
    except Exception as e:
        return jsonify({"error": f"Failed to mark command as running: {str(e)}"}), 500
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        print(result)
        
        # If command succeeded, return formatted JSON output
        if result.returncode == 0:
            utils.mark_command_completed(npub, since, curr_timestamp, db_path=db_path)
            print(f"Marked command as completed for {npub}_{since}_{curr_timestamp}")
            try:
                formatted_result = utils.load_formatted_npub_output(npub, since, curr_timestamp)
                return jsonify(formatted_result)
            except Exception as e:
                return jsonify({"error": f"Failed to format output: {str(e)}"}), 500
        else:
            try:
                utils.mark_command_failed(
                    npub, since, curr_timestamp,
                    f"returncode={result.returncode}, stderr={result.stderr[:1000]}",
                    db_path=db_path
                )
            except Exception:
                pass
            # If command failed, return the original error output
            return jsonify({
                "cmd": cmd,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }), 500
    except Exception as e:
        # Record failure for the job
        try:
            utils.mark_command_failed(
                npub, since, curr_timestamp, f"exception: {str(e)[:1000]}", db_path=db_path
            )
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)