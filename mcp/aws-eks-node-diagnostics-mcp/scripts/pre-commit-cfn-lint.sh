#!/bin/bash
# Pre-commit hook: synthesize CDK and validate with cfn-lint
# Install: cp scripts/pre-commit-cfn-lint.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# Or run manually: bash scripts/pre-commit-cfn-lint.sh

set -e

echo "Running cfn-lint on synthesized CloudFormation template..."

# Synth to a temp directory to avoid polluting cdk.out
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

npx cdk synth --quiet -o "$TMPDIR" 2>/dev/null

TEMPLATE="$TMPDIR/EksNodeLogMcpStack.template.json"
if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: Template not found after cdk synth"
  exit 1
fi

# Find cfn-lint — check PATH first, then common pip install locations
CFN_LINT=$(command -v cfn-lint 2>/dev/null || \
  python3 -c "import shutil; print(shutil.which('cfn-lint') or '')" 2>/dev/null || true)

if [ -z "$CFN_LINT" ]; then
  # Try user pip bin directories
  for candidate in \
    "$HOME/Library/Python/3.9/bin/cfn-lint" \
    "$HOME/Library/Python/3.11/bin/cfn-lint" \
    "$HOME/Library/Python/3.12/bin/cfn-lint" \
    "$HOME/.local/bin/cfn-lint"; do
    if [ -x "$candidate" ]; then
      CFN_LINT="$candidate"
      break
    fi
  done
fi

if [ -z "$CFN_LINT" ]; then
  echo "ERROR: cfn-lint not found. Install with: pip3 install cfn-lint"
  exit 1
fi

# Run cfn-lint — capture exit code without triggering set -e
RESULT=0
"$CFN_LINT" "$TEMPLATE" || RESULT=$?

if [ $RESULT -eq 0 ]; then
  echo "cfn-lint: PASSED (no issues)"
elif [ $RESULT -eq 4 ]; then
  echo "cfn-lint: PASSED (warnings only — see above)"
  exit 0
else
  echo "cfn-lint: FAILED — fix errors before committing"
  exit 1
fi
