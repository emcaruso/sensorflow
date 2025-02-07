# cd to rootpath
SCRIPT_DIR=$(dirname "$(realpath "$0")")
cd "$SCRIPT_DIR/.."

# run main
# uv run ./src/main.py --config-path ../configs --config-name collector_default.yaml
python ./src/main.py --config-path ../configs --config-name collector_default.yaml
