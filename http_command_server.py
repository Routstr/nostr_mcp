from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import utils

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

@app.route('/run', methods=['POST'])
def run_command():
    data = request.get_json()
    if not data or "npub" not in data or "since" not in data or "curr_timestamp" not in data:
        return jsonify({"error": "Missing required parameters"}), 400
    
    npub = data["npub"]
    since = data["since"]
    curr_timestamp = data["curr_timestamp"]

    # Check if files already exist for the current timestamp
    try:
        print(npub, since, curr_timestamp)
        if utils.files_exist_for_timestamp(npub, since, curr_timestamp):
            print("FILES ECIST")
            # Files exist, return formatted output directly without running command
            formatted_result = utils.load_formatted_npub_output(npub, since, curr_timestamp)
            return jsonify(formatted_result)
    except Exception as e:
        return jsonify({"error": f"Failed to check existing files: {str(e)}"}), 500
    
    # Files don't exist, run the command to create them
    cmd = f"/Users/r/projects/routstr_main/nostr_mcp/run_goose.sh {npub} {since} {curr_timestamp}"
    print("FILES iNOT EXCIST. RUNNING new")
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
            try:
                formatted_result = utils.load_formatted_npub_output(npub, since, curr_timestamp)
                return jsonify(formatted_result)
            except Exception as e:
                return jsonify({"error": f"Failed to format output: {str(e)}"}), 500
        else:
            # If command failed, return the original error output
            return jsonify({
                "cmd": cmd,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)