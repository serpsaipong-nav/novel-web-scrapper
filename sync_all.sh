#!/bin/bash
# Daily scraper pipeline — replaces Docker/n8n
# Runs all 4 pipelines in parallel, logs output, optional Discord webhook

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="$SCRIPT_DIR/sync.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Source local env for Discord webhook (optional)
[ -f "$SCRIPT_DIR/.env.local" ] && source "$SCRIPT_DIR/.env.local"

# Colors
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
CYAN='\033[36m'
RESET='\033[0m'

# Detect if running interactively (show terminal output) or from launchd (log only)
if [ -t 1 ]; then
  INTERACTIVE=true
else
  INTERACTIVE=false
fi

print() {
  if [ "$INTERACTIVE" = true ]; then
    echo -e "$1"
  fi
}

log() { echo "[$TIMESTAMP] $1" >> "$LOG_FILE"; }
log "=== Pipeline started ==="

print "${BOLD}Scraper Pipeline${RESET}  ${DIM}$TIMESTAMP${RESET}"
print ""

# Temporary files for capturing per-pipeline results
NOVELS_OUT=$(mktemp) BLOGS_OUT=$(mktemp) MEDIUM_OUT=$(mktemp) RAINDROP_OUT=$(mktemp)

# Track start times
SECONDS_NOVELS=0 SECONDS_BLOGS=0 SECONDS_MEDIUM=0 SECONDS_RAINDROP=0

# --- Pipeline functions ---

# Helper: run a command, check for config errors, write to output file
# Usage: run_step <output_file> <command...>
# Returns 0 on success, 1 on config error
run_step() {
  local outfile=$1; shift
  local output
  output=$("$@" 2>&1)
  local exit_code=$?
  echo "$output" >> "$outfile"
  if echo "$output" | grep -qi "not configured\|vault path not configured\|token not configured"; then
    echo "CONFIG_ERROR:$output" >> "$outfile"
    return 1
  fi
  return $exit_code
}

run_novels() {
  local start=$SECONDS
  local check_output
  check_output=$(uv run python scrape_novels.py check --json 2>&1)
  echo "$check_output" > "$NOVELS_OUT"
  if echo "$check_output" | grep -qi "not configured"; then
    echo "STATUS:config_error" >> "$NOVELS_OUT"
  elif echo "$check_output" | grep -q '"has_new": true'; then
    if run_step "$NOVELS_OUT" uv run python scrape_novels.py sync --all && \
       run_step "$NOVELS_OUT" uv run python scrape_novels.py move --all; then
      echo "STATUS:synced" >> "$NOVELS_OUT"
    else
      echo "STATUS:failed" >> "$NOVELS_OUT"
    fi
  else
    echo "STATUS:no_new" >> "$NOVELS_OUT"
  fi
  echo "ELAPSED:$(( SECONDS - start ))" >> "$NOVELS_OUT"
}

run_blogs() {
  local start=$SECONDS
  if run_step "$BLOGS_OUT" uv run python scrape_blogs.py discover && \
     run_step "$BLOGS_OUT" uv run python scrape_blogs.py scrape --parallel && \
     run_step "$BLOGS_OUT" uv run python scrape_blogs.py move --all; then
    echo "STATUS:done" >> "$BLOGS_OUT"
  elif grep -q "CONFIG_ERROR:" "$BLOGS_OUT"; then
    echo "STATUS:config_error" >> "$BLOGS_OUT"
  else
    echo "STATUS:failed" >> "$BLOGS_OUT"
  fi
  echo "ELAPSED:$(( SECONDS - start ))" >> "$BLOGS_OUT"
}

run_medium() {
  local start=$SECONDS
  if run_step "$MEDIUM_OUT" uv run python scrape_medium.py discover && \
     run_step "$MEDIUM_OUT" uv run python scrape_medium.py scrape --parallel && \
     run_step "$MEDIUM_OUT" uv run python scrape_medium.py move --all; then
    echo "STATUS:done" >> "$MEDIUM_OUT"
  elif grep -q "CONFIG_ERROR:" "$MEDIUM_OUT"; then
    echo "STATUS:config_error" >> "$MEDIUM_OUT"
  else
    echo "STATUS:failed" >> "$MEDIUM_OUT"
  fi
  echo "ELAPSED:$(( SECONDS - start ))" >> "$MEDIUM_OUT"
}

run_raindrop() {
  local start=$SECONDS
  if run_step "$RAINDROP_OUT" uv run python scrape_raindrop.py discover && \
     run_step "$RAINDROP_OUT" uv run python scrape_raindrop.py scrape --parallel && \
     run_step "$RAINDROP_OUT" uv run python scrape_raindrop.py move --all; then
    echo "STATUS:done" >> "$RAINDROP_OUT"
  elif grep -q "CONFIG_ERROR:" "$RAINDROP_OUT"; then
    echo "STATUS:config_error" >> "$RAINDROP_OUT"
  else
    echo "STATUS:failed" >> "$RAINDROP_OUT"
  fi
  echo "ELAPSED:$(( SECONDS - start ))" >> "$RAINDROP_OUT"
}

# --- Spinner for interactive mode ---

spinner() {
  local pid=$1
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  while kill -0 "$pid" 2>/dev/null; do
    # Build status line from current temp file contents
    local n_st b_st m_st r_st
    n_st=$(grep "^STATUS:" "$NOVELS_OUT" 2>/dev/null | cut -d: -f2)
    b_st=$(grep "^STATUS:" "$BLOGS_OUT" 2>/dev/null | cut -d: -f2)
    m_st=$(grep "^STATUS:" "$MEDIUM_OUT" 2>/dev/null | cut -d: -f2)
    r_st=$(grep "^STATUS:" "$RAINDROP_OUT" 2>/dev/null | cut -d: -f2)

    local n_icon b_icon m_icon r_icon
    n_icon=$(status_icon "$n_st")
    b_icon=$(status_icon "$b_st")
    m_icon=$(status_icon "$m_st")
    r_icon=$(status_icon "$r_st")

    printf "\r  ${frames[$i]}  ${n_icon} Novels  ${b_icon} Blogs  ${m_icon} Medium  ${r_icon} Raindrop  " >&2
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.15
  done
  printf "\r%80s\r" "" >&2  # Clear spinner line
}

status_icon() {
  case "$1" in
    synced|done)  echo -e "${GREEN}✓${RESET}" ;;
    no_new)       echo -e "${YELLOW}–${RESET}" ;;
    config_error) echo -e "${RED}!${RESET}" ;;
    "")           echo -e "${DIM}…${RESET}" ;;
    *)            echo -e "${RED}✗${RESET}" ;;
  esac
}

format_status() {
  local label=$1 status=$2 elapsed=$3
  local icon color status_text
  case "$status" in
    synced)       icon="✓"; color="$GREEN"; status_text="synced" ;;
    done)         icon="✓"; color="$GREEN"; status_text="done" ;;
    no_new)       icon="–"; color="$YELLOW"; status_text="no new chapters" ;;
    config_error) icon="!"; color="$RED"; status_text="missing config" ;;
    *)            icon="✗"; color="$RED"; status_text="failed" ;;
  esac
  printf "  ${color}${icon}${RESET}  %-12s ${color}%-18s${RESET} ${DIM}%s${RESET}\n" "$label" "$status_text" "${elapsed}s"
}

# --- Run all 4 in parallel ---

TOTAL_START=$SECONDS

run_novels &
PID_NOVELS=$!
run_blogs &
PID_BLOGS=$!
run_medium &
PID_MEDIUM=$!
run_raindrop &
PID_RAINDROP=$!

if [ "$INTERACTIVE" = true ]; then
  # Start spinner in background, kill when all pipelines finish
  (spinner $$) &
  SPINNER_PID=$!
  wait "$PID_NOVELS" "$PID_BLOGS" "$PID_MEDIUM" "$PID_RAINDROP" 2>/dev/null
  kill "$SPINNER_PID" 2>/dev/null
  wait "$SPINNER_PID" 2>/dev/null
  printf "\r\033[2K" >&2  # Clear spinner line completely
else
  wait
fi

TOTAL_ELAPSED=$(( SECONDS - TOTAL_START ))

# --- Summarize ---
novels_status=$(grep "^STATUS:" "$NOVELS_OUT" | cut -d: -f2)
blogs_status=$(grep "^STATUS:" "$BLOGS_OUT" | cut -d: -f2)
medium_status=$(grep "^STATUS:" "$MEDIUM_OUT" | cut -d: -f2)
raindrop_status=$(grep "^STATUS:" "$RAINDROP_OUT" | cut -d: -f2)

novels_elapsed=$(grep "^ELAPSED:" "$NOVELS_OUT" | cut -d: -f2)
blogs_elapsed=$(grep "^ELAPSED:" "$BLOGS_OUT" | cut -d: -f2)
medium_elapsed=$(grep "^ELAPSED:" "$MEDIUM_OUT" | cut -d: -f2)
raindrop_elapsed=$(grep "^ELAPSED:" "$RAINDROP_OUT" | cut -d: -f2)

summary="Novels: ${novels_status:-failed} | Blogs: ${blogs_status:-failed} | Medium: ${medium_status:-failed} | Raindrop: ${raindrop_status:-failed}"
log "$summary"

# Print results table
if [ "$INTERACTIVE" = true ]; then
  format_status "Novels" "${novels_status:-failed}" "${novels_elapsed:-?}"
  format_status "Blogs" "${blogs_status:-failed}" "${blogs_elapsed:-?}"
  format_status "Medium" "${medium_status:-failed}" "${medium_elapsed:-?}"
  format_status "Raindrop" "${raindrop_status:-failed}" "${raindrop_elapsed:-?}"
  print ""
  print "${DIM}Done in ${TOTAL_ELAPSED}s — logged to sync.log${RESET}"
fi

# Append full output for debugging
cat "$NOVELS_OUT" "$BLOGS_OUT" "$MEDIUM_OUT" "$RAINDROP_OUT" >> "$LOG_FILE"

# --- Discord webhook (optional) ---
if [ -n "$DISCORD_WEBHOOK_URL" ]; then
  curl -s -H "Content-Type: application/json" \
    -d "{\"content\":\"**Scraper Pipeline** ($TIMESTAMP)\\n$summary\"}" \
    "$DISCORD_WEBHOOK_URL" > /dev/null 2>&1
fi

# Cleanup temp files
rm -f "$NOVELS_OUT" "$BLOGS_OUT" "$MEDIUM_OUT" "$RAINDROP_OUT"

log "=== Pipeline finished ==="
