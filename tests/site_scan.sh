#!/bin/bash
# Re-run the plugins.qgis.org checks locally on a plugin zip.
# usage: tests/site_scan.sh <plugin.zip> <QGIS-Plugins-Website checkout> [qgis4 python]
set -u
ZIP=$1; SITE=$2; QPY=${3:-}
RULES=$SITE/qgis-app/plugins/management/commands/data
W=$(mktemp -d); unzip -q "$ZIP" -d "$W"
fail=0
BT=$(python3 -c "import json;print(','.join(sorted(r['check_code'] for r in json.load(open('$RULES/bandit_rules.json'))['rules'] if r['enabled'])))")
echo "== bandit (site-enabled rules; any finding blocks)"
n=$(bandit -r "$W" -f json --quiet -t "$BT" 2>/dev/null | python3 -c "import json,sys;r=json.load(sys.stdin)['results'];[print('  ',x['test_id'],x['filename'].split('/',3)[-1],x['line_number'],x['issue_text']) for x in r];print(len(r))" | tee /dev/stderr | tail -1)
[ "$n" != "0" ] && fail=1
echo "== detect-secrets"
s=$(cd "$W" && detect-secrets scan --all-files . 2>/dev/null | python3 -c "import json,sys;r=json.load(sys.stdin)['results'];print(sum(len(v) for v in r.values()))")
echo "   findings: $s"; [ "$s" != "0" ] && fail=1
echo "== flake8 (site-enabled rules)"
FL=$(python3 -c "import json;d=json.load(open('$RULES/flake8_rules.json'));d=d.get('rules',d);print(','.join(sorted(r['check_code'] for r in d if r['enabled'])))")
flake8 --select="$FL" --max-line-length=120 "$W" | sed "s#$W/##" ; echo "   flake8 lines: $(flake8 --select="$FL" --max-line-length=120 "$W" | wc -l)"
CR=$(python3 -c "import json;d=json.load(open('$RULES/flake8_rules.json'));d=d.get('rules',d);print(','.join(sorted(r['check_code'] for r in d if r['enabled'] and r['severity']=='critical')))")
[ -n "$(flake8 --select="$CR" "$W")" ] && { echo "   CRITICAL flake8 findings"; fail=1; }
if [ -n "$QPY" ]; then
  echo "== Qt6 (pyqt5_to_pyqt6.py --dry_run)"
  q=$("$QPY" /tmp/qt6chk/pyqt5_to_pyqt6.py "$W" --dry_run 2>&1 | grep -E "\.py:[0-9]+:[0-9]+ - ")
  echo "$q" | sed "s#$W/##" | grep . ; echo "   Qt6 issues: $(echo -n "$q" | grep -c .)"
  [ -n "$q" ] && fail=1
fi
rm -rf "$W"
[ $fail = 0 ] && echo "SITE SCAN CLEAN" || { echo "SITE SCAN FOUND BLOCKING/QT6 ISSUES"; exit 1; }
