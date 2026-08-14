#!/bin/bash
set -euo pipefail

# ECS Instance Log MCP - Deploy and Configure Script
# This script deploys the CDK stack and outputs all values needed for DevOps Agent configuration

STACK_NAME="${1:-${CDK_STACK_NAME:-EcsInstanceLogMcpStack}}"
REGION="${AWS_REGION:-us-east-1}"
export CDK_STACK_NAME="$STACK_NAME"

# Optional: pass ECS instance role ARNs directly (comma-separated)
# Usage: ./deploy.sh EcsInstanceLogMcpStack arn:aws:iam::123456789012:role/ecsInstanceRole
# Or:    ECS_INSTANCE_ROLE_ARNS=arn:aws:iam::123456789012:role/ecsInstanceRole ./deploy.sh
if [ -n "${2:-}" ]; then
  export ECS_INSTANCE_ROLE_ARNS="$2"
fi

echo "=============================================="
echo "ECS Instance Log MCP - Deployment Script"
echo "=============================================="
echo "Stack Name: $STACK_NAME"
echo "Region: $REGION"
echo ""

# Check prerequisites
command -v npm >/dev/null 2>&1 || { echo "Error: npm is required but not installed."; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "Error: AWS CLI is required but not installed."; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 is required but not installed."; exit 1; }

# Install dependencies
echo "Installing dependencies..."
npm install --silent

# Build TypeScript
echo "Building TypeScript..."
npm run build

# Bootstrap CDK (if needed)
echo "Bootstrapping CDK (if needed)..."
npx cdk bootstrap --quiet 2>/dev/null || true

# ========================================================================
# DETECT / CREATE SSM DEFAULT HOST MANAGEMENT ROLE
# ========================================================================
echo ""
echo "Setting up SSM Default Host Management role..."

SSM_ROLE_NAME="AWSSystemsManagerDefaultEC2InstanceManagementRole"
EPOXY_ROLE_NAME="EpoxyAWSSystemsManagerDefaultEC2InstanceManagementRole"

# Check for existing role (standard or Epoxy-prefixed)
SSM_DEFAULT_HOST_ROLE_ARN=$(aws iam get-role \
  --role-name "$SSM_ROLE_NAME" \
  --query 'Role.Arn' --output text 2>/dev/null || true)

if [ -z "$SSM_DEFAULT_HOST_ROLE_ARN" ] || [ "$SSM_DEFAULT_HOST_ROLE_ARN" = "None" ]; then
  SSM_DEFAULT_HOST_ROLE_ARN=$(aws iam get-role \
    --role-name "$EPOXY_ROLE_NAME" \
    --query 'Role.Arn' --output text 2>/dev/null || true)
fi

if [ -z "$SSM_DEFAULT_HOST_ROLE_ARN" ] || [ "$SSM_DEFAULT_HOST_ROLE_ARN" = "None" ]; then
  echo "SSM Default Host Management role not found. Creating $SSM_ROLE_NAME..."

  TRUST_POLICY=$(cat <<'TRUST'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ssm.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
TRUST
)

  aws iam create-role \
    --role-name "$SSM_ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "Default EC2 instance management role for SSM" \
    --region "$REGION" >/dev/null

  aws iam attach-role-policy \
    --role-name "$SSM_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

  SSM_DEFAULT_HOST_ROLE_ARN=$(aws iam get-role \
    --role-name "$SSM_ROLE_NAME" \
    --query 'Role.Arn' --output text)

  echo "Created role: $SSM_DEFAULT_HOST_ROLE_ARN"
  echo "Waiting 10s for IAM propagation..."
  sleep 10
else
  echo "Found existing role: $SSM_DEFAULT_HOST_ROLE_ARN"
fi

export SSM_DEFAULT_HOST_ROLE_ARN

# ========================================================================
# AUTO-DETECT ECS CONTAINER INSTANCE ROLE ARNS (interactive)
# ========================================================================
echo ""
if [ -n "${ECS_INSTANCE_ROLE_ARNS:-}" ]; then
  if [ -z "${ALLOWED_CLUSTER_NAMES:-}" ]; then
    echo "Error: ALLOWED_CLUSTER_NAMES is required in non-interactive role mode."
    exit 1
  fi
  export ALLOWED_REGIONS="${ALLOWED_REGIONS:-$REGION}"
  echo "Using provided ECS instance role ARNs: $ECS_INSTANCE_ROLE_ARNS"
  echo "Allowed clusters: $ALLOWED_CLUSTER_NAMES"
  echo "Allowed regions: $ALLOWED_REGIONS"
else
  # --- Step 1: Region selection ---
  echo "Which AWS regions should be scanned for ECS clusters?"
  echo ""
  echo "  1) All enabled regions"
  echo "  2) Current deploy region only ($REGION)"
  echo "  3) Enter a specific region"
  echo ""
  read -rp "Select [1/2/3] (default: 1): " REGION_CHOICE
  REGION_CHOICE="${REGION_CHOICE:-1}"

  case "$REGION_CHOICE" in
    1)
      echo ""
      echo "Fetching all enabled regions..."
      SCAN_REGIONS=$(aws ec2 describe-regions --query 'Regions[].RegionName' --output text 2>/dev/null || echo "$REGION")
      ;;
    2)
      SCAN_REGIONS="$REGION"
      ;;
    3)
      read -rp "Enter region (e.g. us-west-2): " CUSTOM_REGION
      if [ -z "$CUSTOM_REGION" ]; then
        echo "No region entered, falling back to $REGION"
        CUSTOM_REGION="$REGION"
      fi
      SCAN_REGIONS="$CUSTOM_REGION"
      ;;
    *)
      echo "Invalid choice, falling back to all regions."
      SCAN_REGIONS=$(aws ec2 describe-regions --query 'Regions[].RegionName' --output text 2>/dev/null || echo "$REGION")
      ;;
  esac

  # --- Step 2: Discover ECS clusters across selected regions ---
  echo ""
  echo "Scanning for ECS clusters..."

  CLUSTER_LIST=()
  CLUSTER_DISPLAY=()

  for SCAN_REGION in $SCAN_REGIONS; do
    CLUSTERS=$(aws ecs list-clusters --region "$SCAN_REGION" --query 'clusterArns[*]' --output text 2>/dev/null || true)
    if [ -n "$CLUSTERS" ]; then
      for CLUSTER_ARN in $CLUSTERS; do
        CLUSTER_NAME="${CLUSTER_ARN##*/}"
        CLUSTER_LIST+=("${SCAN_REGION}/${CLUSTER_NAME}")
      done
    fi
  done

  if [ ${#CLUSTER_LIST[@]} -eq 0 ]; then
    echo "Error: No ECS clusters found in the selected region(s)."
    echo "A nonempty cluster allowlist and explicit ECS instance role ARNs are mandatory."
    exit 1
  else
    # --- Step 3: Display clusters and let user choose ---
    echo ""
    echo "Found ${#CLUSTER_LIST[@]} ECS cluster(s):"
    echo ""
    IDX=1
    for ENTRY in "${CLUSTER_LIST[@]}"; do
      C_REGION="${ENTRY%%/*}"
      C_NAME="${ENTRY#*/}"
      echo "  ${IDX}) ${C_NAME}  (${C_REGION})"
      IDX=$((IDX + 1))
    done
    echo ""
    echo "  a) All clusters"
    echo ""
    read -rp "Select clusters (comma-separated numbers, or 'a' for all) [default: a]: " CLUSTER_CHOICE
    CLUSTER_CHOICE="${CLUSTER_CHOICE:-a}"

    SELECTED_CLUSTERS=()
    if [ "$CLUSTER_CHOICE" = "a" ] || [ "$CLUSTER_CHOICE" = "A" ]; then
      SELECTED_CLUSTERS=("${CLUSTER_LIST[@]}")
    else
      IFS=',' read -ra PICKS <<< "$CLUSTER_CHOICE"
      for PICK in "${PICKS[@]}"; do
        PICK=$(echo "$PICK" | tr -d ' ')
        if [[ "$PICK" =~ ^[0-9]+$ ]] && [ "$PICK" -ge 1 ] && [ "$PICK" -le ${#CLUSTER_LIST[@]} ]; then
          SELECTED_CLUSTERS+=("${CLUSTER_LIST[$((PICK - 1))]}")
        else
          echo "  Skipping invalid selection: $PICK"
        fi
      done
    fi

    if [ ${#SELECTED_CLUSTERS[@]} -eq 0 ]; then
      echo "Error: No valid clusters selected."
      exit 1
    else
      ALLOWED_CLUSTER_NAMES=""
      ALLOWED_REGIONS=""
      for ENTRY in "${SELECTED_CLUSTERS[@]}"; do
        C_REGION="${ENTRY%%/*}"
        C_NAME="${ENTRY#*/}"
        case ",$ALLOWED_CLUSTER_NAMES," in
          *",$C_NAME,"*) ;;
          *) ALLOWED_CLUSTER_NAMES="${ALLOWED_CLUSTER_NAMES:+$ALLOWED_CLUSTER_NAMES,}$C_NAME" ;;
        esac
        case ",$ALLOWED_REGIONS," in
          *",$C_REGION,"*) ;;
          *) ALLOWED_REGIONS="${ALLOWED_REGIONS:+$ALLOWED_REGIONS,}$C_REGION" ;;
        esac
      done
      export ALLOWED_CLUSTER_NAMES ALLOWED_REGIONS
      echo "Allowed clusters: $ALLOWED_CLUSTER_NAMES"
      echo "Allowed regions: $ALLOWED_REGIONS"
      echo ""
      echo "Detecting container instance roles for ${#SELECTED_CLUSTERS[@]} cluster(s)..."

      # --- Step 4: Collect unique instance role ARNs from selected clusters ---
      ALL_ROLE_ARNS=()
      ROLE_SOURCES=()

      for ENTRY in "${SELECTED_CLUSTERS[@]}"; do
        C_REGION="${ENTRY%%/*}"
        C_NAME="${ENTRY#*/}"

        # List container instances in the cluster
        CI_ARNS=$(aws ecs list-container-instances --cluster "$C_NAME" --region "$C_REGION" \
          --query 'containerInstanceArns[*]' --output text 2>/dev/null || true)

        if [ -n "$CI_ARNS" ]; then
          # Describe container instances to get EC2 instance IDs
          CI_DETAILS=$(aws ecs describe-container-instances --cluster "$C_NAME" --region "$C_REGION" \
            --container-instances $CI_ARNS \
            --query 'containerInstances[].ec2InstanceId' --output text 2>/dev/null || true)

          for EC2_ID in $CI_DETAILS; do
            # Get the IAM instance profile role from the EC2 instance
            ROLE_ARN=$(aws ec2 describe-instances --instance-ids "$EC2_ID" --region "$C_REGION" \
              --query 'Reservations[0].Instances[0].IamInstanceProfile.Arn' --output text 2>/dev/null || true)

            if [ -n "$ROLE_ARN" ] && [ "$ROLE_ARN" != "None" ]; then
              # Convert instance profile ARN to role ARN
              PROFILE_NAME="${ROLE_ARN##*/}"
              ACTUAL_ROLE_ARN=$(aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" \
                --query 'InstanceProfile.Roles[0].Arn' --output text 2>/dev/null || true)

              if [ -n "$ACTUAL_ROLE_ARN" ] && [ "$ACTUAL_ROLE_ARN" != "None" ]; then
                # Deduplicate
                ALREADY_ADDED=false
                for EXISTING in "${ALL_ROLE_ARNS[@]}"; do
                  if [ "$EXISTING" = "$ACTUAL_ROLE_ARN" ]; then
                    ALREADY_ADDED=true
                    break
                  fi
                done
                if [ "$ALREADY_ADDED" = false ]; then
                  ALL_ROLE_ARNS+=("$ACTUAL_ROLE_ARN")
                  ROLE_NAME="${ACTUAL_ROLE_ARN##*/}"
                  ROLE_SOURCES+=("${ROLE_NAME}  (${C_NAME} / ${C_REGION})")
                fi
              fi
            fi
          done
        fi
      done

      if [ ${#ALL_ROLE_ARNS[@]} -eq 0 ]; then
        echo "WARNING: No container instance roles found in selected clusters."
        echo "  (Clusters may be empty or using Fargate launch type)"
        echo ""
        read -rp "Would you like to manually enter instance role ARN(s)? [y/N]: " MANUAL_ENTRY
        if [ "$MANUAL_ENTRY" = "y" ] || [ "$MANUAL_ENTRY" = "Y" ]; then
          echo "Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/ecsInstanceRole):"
          read -rp "> " MANUAL_ARNS
          MANUAL_ARNS=$(echo "$MANUAL_ARNS" | tr -d ' ')
          if [ -n "$MANUAL_ARNS" ]; then
            ECS_INSTANCE_ROLE_ARNS="$MANUAL_ARNS"
            echo "Using manually provided roles: $ECS_INSTANCE_ROLE_ARNS"
            export ECS_INSTANCE_ROLE_ARNS
          else
            echo "Error: At least one explicit ECS instance role ARN is required."
            exit 1
          fi
        else
          echo "Error: At least one explicit ECS instance role ARN is required."
          exit 1
        fi
      else
        # --- Step 5: Let user choose which instance roles to include ---
        echo ""
        echo "Found ${#ALL_ROLE_ARNS[@]} unique container instance role(s):"
        echo ""
        IDX=1
        for i in "${!ALL_ROLE_ARNS[@]}"; do
          echo "  ${IDX}) ${ALL_ROLE_ARNS[$i]}"
          echo "     └─ ${ROLE_SOURCES[$i]}"
          IDX=$((IDX + 1))
        done
        echo ""
        echo "  a) All roles"
        echo ""
        read -rp "Select instance roles (comma-separated numbers, or 'a' for all) [default: a]: " ROLE_CHOICE
        ROLE_CHOICE="${ROLE_CHOICE:-a}"

        SELECTED_ROLES=()
        if [ "$ROLE_CHOICE" = "a" ] || [ "$ROLE_CHOICE" = "A" ]; then
          SELECTED_ROLES=("${ALL_ROLE_ARNS[@]}")
        else
          IFS=',' read -ra PICKS <<< "$ROLE_CHOICE"
          for PICK in "${PICKS[@]}"; do
            PICK=$(echo "$PICK" | tr -d ' ')
            if [[ "$PICK" =~ ^[0-9]+$ ]] && [ "$PICK" -ge 1 ] && [ "$PICK" -le ${#ALL_ROLE_ARNS[@]} ]; then
              SELECTED_ROLES+=("${ALL_ROLE_ARNS[$((PICK - 1))]}")
            else
              echo "  Skipping invalid selection: $PICK"
            fi
          done
        fi

        # Build comma-separated string
        ECS_INSTANCE_ROLE_ARNS=""
        for ROLE in "${SELECTED_ROLES[@]}"; do
          if [ -z "$ECS_INSTANCE_ROLE_ARNS" ]; then
            ECS_INSTANCE_ROLE_ARNS="$ROLE"
          else
            ECS_INSTANCE_ROLE_ARNS="$ECS_INSTANCE_ROLE_ARNS,$ROLE"
          fi
        done

        if [ -n "$ECS_INSTANCE_ROLE_ARNS" ]; then
          echo ""
          echo "Using ECS instance roles: $ECS_INSTANCE_ROLE_ARNS"
          export ECS_INSTANCE_ROLE_ARNS
        else
          echo "No roles selected."
          read -rp "Would you like to manually enter instance role ARN(s) instead? [y/N]: " MANUAL_ENTRY
          if [ "$MANUAL_ENTRY" = "y" ] || [ "$MANUAL_ENTRY" = "Y" ]; then
            echo "Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/ecsInstanceRole):"
            read -rp "> " MANUAL_ARNS
            MANUAL_ARNS=$(echo "$MANUAL_ARNS" | tr -d ' ')
            if [ -n "$MANUAL_ARNS" ]; then
              ECS_INSTANCE_ROLE_ARNS="$MANUAL_ARNS"
              echo "Using manually provided roles: $ECS_INSTANCE_ROLE_ARNS"
              export ECS_INSTANCE_ROLE_ARNS
            else
              echo "Error: At least one explicit ECS instance role ARN is required."
              exit 1
            fi
          else
            echo "Error: At least one explicit ECS instance role ARN is required."
            exit 1
          fi
        fi
      fi
    fi
  fi
fi

if [ -z "${ALLOWED_CLUSTER_NAMES:-}" ]; then
  echo "Error: ALLOWED_CLUSTER_NAMES must contain at least one ECS cluster."
  exit 1
fi
if [ -z "${ALLOWED_REGIONS:-}" ]; then
  echo "Error: ALLOWED_REGIONS must contain at least one AWS region."
  exit 1
fi
if [ -z "${ECS_INSTANCE_ROLE_ARNS:-}" ]; then
  echo "Error: ECS_INSTANCE_ROLE_ARNS must contain at least one explicit role ARN."
  exit 1
fi
export ALLOWED_CLUSTER_NAMES ALLOWED_REGIONS ECS_INSTANCE_ROLE_ARNS

# Deploy the stack
echo ""
echo "Deploying CDK stack..."
npx cdk deploy "$STACK_NAME" --require-approval never --outputs-file cdk-outputs.json

echo ""
echo "=============================================="
echo "Deployment Complete! Retrieving configuration..."
echo "=============================================="

# Read from cdk-outputs.json using python3 for reliable JSON parsing
if [ ! -f cdk-outputs.json ]; then
  echo "Error: cdk-outputs.json not found"
  exit 1
fi

# Parse values from cdk-outputs.json
GATEWAY_URL=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'GatewayUrl' in k][0])" 2>/dev/null || echo "NOT_FOUND")
CLIENT_ID=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'CognitoClientId' in k][0])" 2>/dev/null || echo "NOT_FOUND")
USER_POOL_ID=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'CognitoUserPoolId' in k][0])" 2>/dev/null || echo "NOT_FOUND")
TOKEN_URL=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'OAuthExchangeUrl' in k][0])" 2>/dev/null || echo "NOT_FOUND")
OAUTH_SCOPE=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'OAuthScope' in k][0])" 2>/dev/null || echo "NOT_FOUND")
LOGS_BUCKET=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'LogsBucketName' in k][0])" 2>/dev/null || echo "NOT_FOUND")

# Verify the deployed resource policy supports the preflight and upload calls made by
# AWSSupport-CollectECSInstanceLogs. This catches policy regressions before users run a collection.
if [ "$LOGS_BUCKET" = "NOT_FOUND" ]; then
  echo "Error: Logs bucket output was not found; cannot validate instance upload permissions."
  exit 1
fi

echo "Validating ECS instance upload permissions..."
if ! BUCKET_POLICY_JSON=$(aws s3api get-bucket-policy \
  --bucket "$LOGS_BUCKET" \
  --region "$REGION" \
  --query Policy \
  --output text 2>/dev/null); then
  echo "Error: Unable to read the logs bucket policy for post-deployment validation."
  exit 1
fi
export BUCKET_POLICY_JSON

python3 - "$LOGS_BUCKET" "$ECS_INSTANCE_ROLE_ARNS" <<'PY'
import json
import os
import sys

bucket_name = sys.argv[1]
role_arns = [value.strip() for value in sys.argv[2].split(',') if value.strip()]
policy = json.loads(os.environ['BUCKET_POLICY_JSON'])
bucket_arn = f'arn:aws:s3:::{bucket_name}'
object_arn = f'{bucket_arn}/*'
required_bucket_actions = {'s3:GetBucketAcl', 's3:GetBucketPolicyStatus', 's3:ListBucket'}
required_object_actions = {'s3:PutObject'}


def values(value):
    return value if isinstance(value, list) else [value]


failures = []
for role_arn in role_arns:
    bucket_actions = set()
    object_actions = set()
    for statement in policy.get('Statement', []):
        if statement.get('Effect') != 'Allow':
            continue
        principals = values(statement.get('Principal', {}).get('AWS', []))
        if role_arn not in principals:
            continue
        actions = set(values(statement.get('Action', [])))
        resources = set(values(statement.get('Resource', [])))
        if bucket_arn in resources:
            bucket_actions.update(actions)
        if object_arn in resources:
            object_actions.update(actions)
    missing_bucket = sorted(required_bucket_actions - bucket_actions)
    missing_object = sorted(required_object_actions - object_actions)
    if missing_bucket or missing_object:
        failures.append(
            f'{role_arn}: missing bucket actions {missing_bucket or "none"}; '
            f'missing object actions {missing_object or "none"}'
        )

if failures:
    raise SystemExit('Error: ECS instance upload policy validation failed:\n  ' + '\n  '.join(failures))

print(f'Validated HeadBucket and PutObject permissions for {len(role_arns)} ECS instance role(s).')
PY
unset BUCKET_POLICY_JSON

echo "Validating archive extraction notifications..."
if ! NOTIFICATIONS_JSON=$(aws s3api get-bucket-notification-configuration \
  --bucket "$LOGS_BUCKET" \
  --region "$REGION" \
  --output json 2>/dev/null); then
  echo "Error: Unable to read the logs bucket notification configuration."
  exit 1
fi
export NOTIFICATIONS_JSON

python3 - "$STACK_NAME" <<'PY'
import json
import os
import sys

function_name = f'{sys.argv[1]}-unzip-function'
configuration = json.loads(os.environ['NOTIFICATIONS_JSON'])
configured_suffixes = set()
for item in configuration.get('LambdaFunctionConfigurations', []):
    if not item.get('LambdaFunctionArn', '').endswith(f':function:{function_name}'):
        continue
    for rule in item.get('Filter', {}).get('Key', {}).get('FilterRules', []):
        if rule.get('Name', '').lower() == 'suffix':
            configured_suffixes.add(rule.get('Value'))

required_suffixes = {'.zip', '.tar.gz', '.tgz'}
missing_suffixes = sorted(required_suffixes - configured_suffixes)
if missing_suffixes:
    raise SystemExit(
        f'Error: {function_name} is missing S3 ObjectCreated notifications for: '
        f'{", ".join(missing_suffixes)}'
    )
print(f'Validated archive notifications for {function_name}: {sorted(configured_suffixes)}')
PY
unset NOTIFICATIONS_JSON

# The OAuth client secret is intentionally not retrieved, printed, or written to disk.
# Retrieve it only at the point of secure MCP registration using least-privilege credentials.

echo ""
echo "=============================================="
echo "DEVOPS AGENT MCP SERVER CONFIGURATION"
echo "=============================================="
echo ""
echo "Copy these values to configure the MCP Server in DevOps Agent Console:"
echo ""
echo "┌─────────────────────────────────────────────────────────────────────┐"
echo "│ MCP Server URL:                                                     │"
echo "│ $GATEWAY_URL"
echo "├─────────────────────────────────────────────────────────────────────┤"
echo "│ OAuth Client ID:                                                    │"
echo "│ $CLIENT_ID"
echo "├─────────────────────────────────────────────────────────────────────┤"
echo "│ OAuth Client Secret:                                                │"
echo "│ Not displayed or stored by this script. Retrieve it only during     │"
echo "│ secure MCP client registration.                                     │"
echo "├─────────────────────────────────────────────────────────────────────┤"
echo "│ Token URL:                                                          │"
echo "│ $TOKEN_URL"
echo "├─────────────────────────────────────────────────────────────────────┤"
echo "│ Scope (use only ONE):                                               │"
echo "│ $OAUTH_SCOPE"
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""
echo "Additional Info:"
echo "  Logs Bucket: $LOGS_BUCKET"
echo "  Region: $REGION"
echo ""

# Save configuration to file
CONFIG_FILE="mcp-config.txt"
cat > "$CONFIG_FILE" << EOF
# ECS Instance Log MCP - DevOps Agent Configuration
# Generated: $(date)
# Stack: $STACK_NAME
# Region: $REGION

MCP_SERVER_URL=$GATEWAY_URL
OAUTH_CLIENT_ID=$CLIENT_ID
# OAUTH_CLIENT_SECRET is intentionally omitted. Retrieve it only during secure client registration.
TOKEN_URL=$TOKEN_URL
OAUTH_SCOPE=$OAUTH_SCOPE
LOGS_BUCKET=$LOGS_BUCKET
EOF

echo "Configuration saved to: $CONFIG_FILE"
echo ""
echo "=============================================="
echo "AVAILABLE MCP TOOLS"
echo "=============================================="
echo ""
echo "TIER 1: CORE OPERATIONS"
echo "------------------------"
echo "1. collect         - Start log collection from an ECS container instance"
echo "2. status          - Get detailed status with progress tracking"
echo "3. validate        - Verify all expected files were extracted"
echo "4. errors          - Get pre-indexed error findings by severity"
echo "5. read            - Byte-range streaming for multi-GB files"
echo ""
echo "TIER 2: ADVANCED ANALYSIS"
echo "-------------------------"
echo "6. search          - Full-text regex search across all logs"
echo "7. correlate       - Cross-file timeline correlation"
echo "8. artifact        - Secure presigned URLs for large artifacts"
echo "9. summarize       - Finding-grounded incident summary"
echo "10. history        - Audit trail of past collections"
echo ""
echo "TIER 3: CLUSTER-LEVEL INTELLIGENCE"
echo "-----------------------------------"
echo "11. cluster_health     - Health overview across all instances in a cluster"
echo "12. compare_instances  - Diff findings between 2+ instances"
echo "13. batch_collect      - Smart batch collection with sampling"
echo "14. batch_status       - Poll status of multiple collections"
echo "15. network_diagnostics - Structured networking analysis"
echo ""
echo "TIER 4: LIVE PACKET CAPTURE"
echo "---------------------------"
echo "16. tcpdump_capture  - Run tcpdump via SSM (supports task-scoped captures)"
echo "17. tcpdump_analyze  - Decoded packets, stats, anomaly detection"
echo ""
echo "TIER 5: SOPs"
echo "------------"
echo "18. list_sops      - List all 36 structured runbooks"
echo "19. get_sop        - Get a specific runbook by name"
echo ""
echo "=============================================="
echo "EXAMPLE PROMPT FOR DEVOPS AGENT"
echo "=============================================="
echo ""
echo "\"I'm investigating an ECS container instance issue on i-0123456789abcdef0."
echo " Collect logs, find any critical errors, and give me a summary.\""
echo ""
