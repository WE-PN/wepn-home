#!/bin/bash

# Script to limit outgoing bandwidth rate for a specific user.
#
# WARNINGS:
# 1. Must be run as root (sudo).
# 2. This script WILL DELETE any existing root qdisc on the specified interface
#    before applying its own. Use with caution if you have existing tc setups.
# 3. This limits RATE, not total data QUOTA.
# 4. Rules are not persistent across reboots by default.
#
# Usage: sudo ./limit_bandwidth.sh <set|remove> <username> <interface> [rate]
#
# Examples:
#   Set limit: sudo ./limit_bandwidth.sh set myuser eth0 512kbit
#   Set limit: sudo ./limit_bandwidth.sh set myuser eth0 10mbit
#   Set limit: sudo ./limit_bandwidth.sh set myuser eth0 1MB
#              (1MB will be interpreted as 1 MegaByte/sec = 8 Megabits/sec)
#
#   Remove limit: sudo ./limit_bandwidth.sh remove myuser eth0
#
# 'rate' can be specified in:
#   - kbit, mbit, gbit (kilobits, megabits, gigabits per second)
#   - KB, MB, GB (KiloBytes, MegaBytes, GigaBytes per second - will be converted to bits/sec)

# --- Configuration ---
IPTABLES_MARK="1" # iptables mark value to identify user's traffic.
                  # Ensure this doesn't conflict if you have other rules using marks.

# TC class structure:
# 1: is the root qdisc handle
# 1:1 is a parent class for all shaped traffic by this script (given a large bandwidth)
# 1:10 will be the user-specific class (example, actual suffix derived from fixed value now)
# 1:30 will be the default class for other traffic on the root qdisc
TC_MAIN_PARENT_CLASSID="1:1"
TC_USER_CLASS_SUFFIX="10" # User's class will be ${TC_MAIN_PARENT_CLASSID%:*}:${TC_USER_CLASS_SUFFIX} -> e.g. 1:10
TC_DEFAULT_CLASSID="1:30" # For traffic not matching user's filter
COMMENT_CMD=" -m comment --comment wepn-bw"

# --- Helper Functions ---
log_error() {
    echo "ERROR: $1" >&2
}

log_info() {
    echo "INFO: $1"
}

# --- Argument Parsing and Validation ---
ACTION="$1"
USERNAME="$2"
INTERFACE="$3"
RATE_INPUT="$4" # Only required for 'set' action

if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root."
   exit 1
fi

if [ "$ACTION" != "set" ] && [ "$ACTION" != "remove" ]; then
    echo "Usage: sudo $0 <set|remove> <username> <interface> [rate]"
    echo "Example (set): sudo $0 set myuser eth0 1mbit"
    echo "Example (remove): sudo $0 remove myuser eth0"
    exit 1
fi

if [ -z "$USERNAME" ] || [ -z "$INTERFACE" ]; then
    echo "Usage: sudo $0 <set|remove> <username> <interface> [rate]"
    exit 1
fi

if [ "$ACTION" == "set" ] && [ -z "$RATE_INPUT" ]; then
    log_error "Rate must be specified for 'set' action."
    echo "Usage: sudo $0 set <username> <interface> <rate>"
    exit 1
fi

# --- Validate Username ---
USER_UID=$(id -u "$USERNAME" 2>/dev/null)
if [ -z "$USER_UID" ]; then
    log_error "User '$USERNAME' not found."
    exit 1
fi

# --- Validate Interface ---
if ! ip link show "$INTERFACE" > /dev/null 2>&1; then
    log_error "Interface '$INTERFACE' not found."
    exit 1
fi

# --- Define user-specific TC class ID ---
# This simple script uses a fixed suffix. For multiple users, you'd need a scheme
# to generate unique class IDs, perhaps based on UID.
USER_TC_CLASSID="${TC_MAIN_PARENT_CLASSID%:*}:${TC_USER_CLASS_SUFFIX}" # e.g., 1:10

# --- Action: set ---
if [ "$ACTION" == "set" ]; then
    # --- Parse and Convert Rate ---
    RATE_VALUE=$(echo "$RATE_INPUT" | grep -o -E '[0-9]+')
    RATE_UNIT_STR=$(echo "$RATE_INPUT" | grep -o -E '[a-zA-Z]+')
    RATE_KBIT=""

    if [ -z "$RATE_VALUE" ] || [ -z "$RATE_UNIT_STR" ]; then
        log_error "Invalid rate format: '$RATE_INPUT'. Use NNNkbit, NNNMB, etc."
        exit 1
    fi

    RATE_UNIT_LOWER=$(echo "$RATE_UNIT_STR" | tr '[:upper:]' '[:lower:]')

    case "$RATE_UNIT_LOWER" in
        kbit|kbits) # Kilobits
            RATE_KBIT=$((RATE_VALUE))
            ;;
        mbit|mbits) # Megabits
            RATE_KBIT=$((RATE_VALUE * 1000))
            ;;
        gbit|gbits) # Gigabits
            RATE_KBIT=$((RATE_VALUE * 1000 * 1000))
            ;;
        kb|kbyte|kbytes) # KiloBytes per second
            RATE_KBIT=$((RATE_VALUE * 8))
            ;;
        mb|mbyte|mbytes) # MegaBytes per second
            RATE_KBIT=$((RATE_VALUE * 8 * 1000))
            ;;
        gb|gbyte|gbytes) # GigaBytes per second
            RATE_KBIT=$((RATE_VALUE * 8 * 1000 * 1000))
            ;;
        *)
            log_error "Unsupported rate unit '$RATE_UNIT_STR'."
            echo "Use: kbit, mbit, gbit (for bits/sec) OR KB, MB, GB (for Bytes/sec)."
            exit 1
            ;;
    esac

    if [ -z "$RATE_KBIT" ] || [ "$RATE_KBIT" -le 0 ]; then
        log_error "Invalid or zero rate calculated from '$RATE_INPUT'."
        exit 1
    fi
    FINAL_RATE_TC="${RATE_KBIT}kbit"

    log_info "Attempting to set bandwidth limit for user '$USERNAME' (UID: $USER_UID) to $FINAL_RATE_TC ($RATE_INPUT) on interface '$INTERFACE'."

    # --- Clean up previous rules for this specific user/mark first ---
    log_info "Cleaning up any potentially conflicting existing rules for this user/mark..."
    sudo iptables -t mangle -D OUTPUT -m owner --uid-owner "$USER_UID" -o "$INTERFACE" -j MARK --set-mark "$IPTABLES_MARK" $COMMENT_CMD 2>/dev/null

    # TC cleanup is implicitly handled by qdisc del below, but filter/class for this specific mark could be removed if qdisc wasn't reset.
    # For this script's model (full qdisc reset), explicit tc rule removal here before reset is redundant.

    # --- Setup TC (Traffic Control) ---
    log_info "WARNING: Deleting existing root qdisc on '$INTERFACE' if any."
    sudo tc qdisc del dev "$INTERFACE" root 2>/dev/null || true # Suppress error if no qdisc

    log_info "Adding root HTB qdisc on '$INTERFACE' (handle 1:, default traffic to class $TC_DEFAULT_CLASSID)."
    sudo tc qdisc add dev "$INTERFACE" root handle 1: htb default "${TC_DEFAULT_CLASSID##*:}" # Pass only minor number for default

    log_info "Adding main parent class $TC_MAIN_PARENT_CLASSID (rate 10gbit - adjust if needed for overall limit)."
    sudo tc class add dev "$INTERFACE" parent 1: classid "$TC_MAIN_PARENT_CLASSID" htb rate 10gbit ceil 10gbit

    log_info "Adding user-specific class $USER_TC_CLASSID under $TC_MAIN_PARENT_CLASSID with rate $FINAL_RATE_TC."
    sudo tc class add dev "$INTERFACE" parent "$TC_MAIN_PARENT_CLASSID" classid "$USER_TC_CLASSID" htb rate "$FINAL_RATE_TC" ceil "$FINAL_RATE_TC" prio 1

    log_info "Adding default traffic class $TC_DEFAULT_CLASSID under root (rate 500mbit - adjust as needed)."
    sudo tc class add dev "$INTERFACE" parent 1: classid "$TC_DEFAULT_CLASSID" htb rate 500mbit ceil 1gbit prio 2

    log_info "Adding TC filter to direct user's (mark $IPTABLES_MARK) traffic to class $USER_TC_CLASSID."
    sudo tc filter add dev "$INTERFACE" parent 1:0 protocol ip prio 1 handle "$IPTABLES_MARK" fw classid "$USER_TC_CLASSID"

    # --- Setup iptables to mark user's packets ---
    log_info "Adding iptables rule to mark packets from UID $USER_UID with mark $IPTABLES_MARK on output interface $INTERFACE."
    sudo iptables -t mangle -A OUTPUT -m owner --uid-owner "$USER_UID" -o "$INTERFACE" -j MARK --set-mark "$IPTABLES_MARK" $COMMENT_CMD

    log_info "--- Bandwidth Limit Applied ---"
    log_info "User: $USERNAME (UID: $USER_UID)"
    log_info "Interface: $INTERFACE"
    log_info "Rate Limit: $FINAL_RATE_TC (from $RATE_INPUT)"
    echo ""
    log_info "To view TC configuration:"
    echo "  sudo tc qdisc show dev $INTERFACE"
    echo "  sudo tc class show dev $INTERFACE"
    echo "  sudo tc filter show dev $INTERFACE"
    echo ""
    log_info "To view relevant iptables rule:"
    echo "  sudo iptables -t mangle -L OUTPUT -v -n | grep 'MARK set 0x${IPTABLES_MARK}'" # iptables shows mark in hex

# --- Action: remove ---
elif [ "$ACTION" == "remove" ]; then
    log_info "Attempting to remove bandwidth limit for user '$USERNAME' (UID: $USER_UID) on interface '$INTERFACE'."

    # 1. Remove iptables rule
    log_info "Removing iptables rule for UID $USER_UID and mark $IPTABLES_MARK."
    sudo iptables -t mangle -D OUTPUT -m owner --uid-owner "$USER_UID" -o "$INTERFACE" -j MARK --set-mark "$IPTABLES_MARK" $COMMENT_CMD 2>/dev/null
    if [ $? -ne 0 ]; then
        log_info "iptables rule might not have existed or was already removed."
    fi

    # 2. Remove TC filter
    # Need to know the filter's parent (1:0) and handle ($IPTABLES_MARK)
    log_info "Removing TC filter for mark $IPTABLES_MARK (directing to class $USER_TC_CLASSID)."
    sudo tc filter del dev "$INTERFACE" parent 1:0 protocol ip prio 1 handle "$IPTABLES_MARK" fw 2>/dev/null
     if [ $? -ne 0 ]; then
        log_info "TC filter might not have existed."
    fi

    # 3. Remove TC class for the user
    log_info "Removing user-specific TC class $USER_TC_CLASSID."
    sudo tc class del dev "$INTERFACE" parent "$TC_MAIN_PARENT_CLASSID" classid "$USER_TC_CLASSID" 2>/dev/null
    if [ $? -ne 0 ]; then
        log_info "User TC class $USER_TC_CLASSID might not have existed."
    fi

    # OPTIONAL: More comprehensive cleanup
    # If $USER_TC_CLASSID was the only child of $TC_MAIN_PARENT_CLASSID, $TC_MAIN_PARENT_CLASSID could be removed.
    # If $TC_MAIN_PARENT_CLASSID and $TC_DEFAULT_CLASSID were the only classes under root 1:,
    # the root qdisc itself could be removed with 'sudo tc qdisc del dev $INTERFACE root'.
    # However, this script keeps it simple: it only removes the user-specific parts.
    # Another user might still be limited by other classes, or default traffic might still use $TC_DEFAULT_CLASSID.
    # To fully clear ALL shaping by this script's structure, you might run:
    # sudo tc qdisc del dev $INTERFACE root 2>/dev/null
    # but this is aggressive if other non-script related tc rules exist.

    log_info "--- Bandwidth Limit Removal Attempted ---"
    log_info "Verify by checking:"
    echo "  sudo tc qdisc show dev $INTERFACE"
    echo "  sudo tc class show dev $INTERFACE"
    echo "  sudo tc filter show dev $INTERFACE"
    echo "  sudo iptables -t mangle -L OUTPUT -v -n"
fi

exit 0

