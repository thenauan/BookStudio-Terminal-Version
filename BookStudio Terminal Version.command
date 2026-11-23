#!/usr/bin/env bash
# macOS: dois cliques no Finder abrem o BookStudio Terminal Version no Terminal
cd "$(dirname "$0")" || exit 1
./bookstudio.sh "$@"
status=$?
if [ $status -ne 0 ]; then
    read -r -p "Press Enter to close..."
fi
exit $status
