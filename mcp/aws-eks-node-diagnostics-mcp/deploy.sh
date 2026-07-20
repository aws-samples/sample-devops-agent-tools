#!/bin/bash
set -e

# EKS Node Log MCP - Deploy and Configure Script
# This script deploys the CDK stack and outputs all values needed for DevOps Agent configuration

STACK_NAME="${1:-EksNodeLogMcpStack}"
REGION="${AWS_REGION:-us-east-1}"

# Optional: pass EKS node role ARNs directly (comma-separated)
# Usage: ./deploy.sh EksNodeLogMcpStack arn:aws:iam::123456789012:role/MyNodeRole
# Or:    EKS_NODE_ROLE_ARNS=arn:aws:iam::123456789012:role/MyNodeRole ./deploy.sh
if [ -n "$2" ]; then
  export EKS_NODE_ROLE_ARNS="$2"
fi

echo "=============================================="
echo "EKS Node Log MCP - Deployment Script"
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

  # Create the trust policy
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
# AUTO-DETECT EKS NODE ROLE ARNS (interactive region & cluster selection)
# ========================================================================
echo ""
if [ -n "$EKS_NODE_ROLE_ARNS" ]; then
  echo "Using provided EKS node role ARNs: $EKS_NODE_ROLE_ARNS"
else
  # --- Step 1: Region selection ---
  echo "Which AWS regions should be scanned for EKS clusters?"
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

  # --- Step 2: Discover clusters across selected regions ---
  echo ""
  echo "Scanning for EKS clusters..."

  # Arrays to hold discovered cluster info
  CLUSTER_LIST=()       # "region/cluster-name"
  CLUSTER_DISPLAY=()    # for numbered display

  for SCAN_REGION in $SCAN_REGIONS; do
    CLUSTERS=$(aws eks list-clusters --region "$SCAN_REGION" --query 'clusters[*]' --output text 2>/dev/null || true)
    if [ -n "$CLUSTERS" ]; then
      for CLUSTER in $CLUSTERS; do
        CLUSTER_LIST+=("${SCAN_REGION}/${CLUSTER}")
      done
    fi
  done

  if [ ${#CLUSTER_LIST[@]} -eq 0 ]; then
    echo "WARNING: No EKS clusters found in the selected region(s)."
    echo ""
    read -rp "Would you like to manually enter node role ARN(s)? [y/N]: " MANUAL_ENTRY
    if [ "$MANUAL_ENTRY" = "y" ] || [ "$MANUAL_ENTRY" = "Y" ]; then
      echo "Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/MyNodeRole):"
      read -rp "> " MANUAL_ARNS
      MANUAL_ARNS=$(echo "$MANUAL_ARNS" | tr -d ' ')
      if [ -n "$MANUAL_ARNS" ]; then
        EKS_NODE_ROLE_ARNS="$MANUAL_ARNS"
        echo "Using manually provided roles: $EKS_NODE_ROLE_ARNS"
        export EKS_NODE_ROLE_ARNS
      else
        echo "No ARNs entered. The S3 bucket policy will use an account-scoped fallback."
      fi
    else
      echo "Skipping. The S3 bucket policy will use an account-scoped fallback (less restrictive)."
    fi
  else
    # --- Step 3: Display clusters and let user choose ---
    echo ""
    echo "Found ${#CLUSTER_LIST[@]} EKS cluster(s):"
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
      echo "No valid clusters selected. Skipping node role detection."
    else
      echo ""
      echo "Detecting node roles for ${#SELECTED_CLUSTERS[@]} cluster(s)..."

      # --- Step 4: Collect all unique node role ARNs from selected clusters ---
      ALL_ROLE_ARNS=()
      ROLE_SOURCES=()   # "role-arn -> cluster (region)"

      for ENTRY in "${SELECTED_CLUSTERS[@]}"; do
        C_REGION="${ENTRY%%/*}"
        C_NAME="${ENTRY#*/}"

        NODEGROUPS=$(aws eks list-nodegroups --cluster-name "$C_NAME" --region "$C_REGION" \
          --query 'nodegroups[*]' --output text 2>/dev/null || true)
        for NG in $NODEGROUPS; do
          ROLE_ARN=$(aws eks describe-nodegroup --cluster-name "$C_NAME" --nodegroup-name "$NG" \
            --region "$C_REGION" --query 'nodegroup.nodeRole' --output text 2>/dev/null || true)
          if [ -n "$ROLE_ARN" ] && [ "$ROLE_ARN" != "None" ]; then
            # Deduplicate
            ALREADY_ADDED=false
            for EXISTING in "${ALL_ROLE_ARNS[@]}"; do
              if [ "$EXISTING" = "$ROLE_ARN" ]; then
                ALREADY_ADDED=true
                break
              fi
            done
            if [ "$ALREADY_ADDED" = false ]; then
              ALL_ROLE_ARNS+=("$ROLE_ARN")
              ROLE_NAME="${ROLE_ARN##*/}"
              ROLE_SOURCES+=("${ROLE_NAME}  (${C_NAME} / ${C_REGION})")
            fi
          fi
        done
      done

      if [ ${#ALL_ROLE_ARNS[@]} -eq 0 ]; then
        echo "WARNING: Selected clusters have no managed node groups with detectable roles."
        echo ""
        read -rp "Would you like to manually enter node role ARN(s)? [y/N]: " MANUAL_ENTRY
        if [ "$MANUAL_ENTRY" = "y" ] || [ "$MANUAL_ENTRY" = "Y" ]; then
          echo "Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/MyNodeRole):"
          read -rp "> " MANUAL_ARNS
          MANUAL_ARNS=$(echo "$MANUAL_ARNS" | tr -d ' ')
          if [ -n "$MANUAL_ARNS" ]; then
            EKS_NODE_ROLE_ARNS="$MANUAL_ARNS"
            echo "Using manually provided roles: $EKS_NODE_ROLE_ARNS"
            export EKS_NODE_ROLE_ARNS
          else
            echo "No ARNs entered. The S3 bucket policy will use an account-scoped fallback."
          fi
        else
          echo "Skipping. The S3 bucket policy will use an account-scoped fallback (less restrictive)."
        fi
      else
        # --- Step 5: Let user choose which node roles to include ---
        echo ""
        echo "Found ${#ALL_ROLE_ARNS[@]} unique node role(s):"
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
        read -rp "Select node roles (comma-separated numbers, or 'a' for all) [default: a]: " ROLE_CHOICE
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

        # Build the comma-separated string
        EKS_NODE_ROLE_ARNS=""
        for ROLE in "${SELECTED_ROLES[@]}"; do
          if [ -z "$EKS_NODE_ROLE_ARNS" ]; then
            EKS_NODE_ROLE_ARNS="$ROLE"
          else
            EKS_NODE_ROLE_ARNS="$EKS_NODE_ROLE_ARNS,$ROLE"
          fi
        done

        if [ -n "$EKS_NODE_ROLE_ARNS" ]; then
          echo ""
          echo "Using EKS node roles: $EKS_NODE_ROLE_ARNS"
          export EKS_NODE_ROLE_ARNS
        else
          echo "No roles selected."
          read -rp "Would you like to manually enter node role ARN(s) instead? [y/N]: " MANUAL_ENTRY
          if [ "$MANUAL_ENTRY" = "y" ] || [ "$MANUAL_ENTRY" = "Y" ]; then
            echo "Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/MyNodeRole):"
            read -rp "> " MANUAL_ARNS
            MANUAL_ARNS=$(echo "$MANUAL_ARNS" | tr -d ' ')
            if [ -n "$MANUAL_ARNS" ]; then
              EKS_NODE_ROLE_ARNS="$MANUAL_ARNS"
              echo "Using manually provided roles: $EKS_NODE_ROLE_ARNS"
              export EKS_NODE_ROLE_ARNS
            else
              echo "No ARNs entered. The S3 bucket policy will use an account-scoped fallback."
            fi
          else
            echo "The S3 bucket policy will use an account-scoped fallback."
          fi
        fi
      fi
    fi
  fi
fi

# Deploy the stack
echo ""
echo "=============================================="
echo "Security Scoping Configuration"
echo "=============================================="
echo ""

# --- ALLOWED_REGIONS: derive from selected clusters ---
if [ -z "$ALLOWED_REGIONS" ]; then
  if [ "${#SELECTED_CLUSTERS[@]:-0}" -gt 0 ] 2>/dev/null; then
    # Extract unique regions from selected clusters (format: "region/cluster-name")
    DETECTED_REGIONS=()
    for ENTRY in "${SELECTED_CLUSTERS[@]}"; do
      C_REGION="${ENTRY%%/*}"
      ALREADY=false
      for R in "${DETECTED_REGIONS[@]}"; do
        if [ "$R" = "$C_REGION" ]; then ALREADY=true; break; fi
      done
      if [ "$ALREADY" = false ]; then
        DETECTED_REGIONS+=("$C_REGION")
      fi
    done
    ALLOWED_REGIONS=$(IFS=','; echo "${DETECTED_REGIONS[*]}")
    echo "Allowed regions (from selected clusters): $ALLOWED_REGIONS"
  else
    ALLOWED_REGIONS="$REGION"
    echo "Allowed regions (deploy region only): $ALLOWED_REGIONS"
  fi
  export ALLOWED_REGIONS
fi

# --- ALLOWED_CLUSTER_NAMES: derive from selected clusters ---
if [ -z "$ALLOWED_CLUSTER_NAMES" ]; then
  if [ "${#SELECTED_CLUSTERS[@]:-0}" -gt 0 ] 2>/dev/null; then
    DETECTED_CLUSTERS=()
    for ENTRY in "${SELECTED_CLUSTERS[@]}"; do
      C_NAME="${ENTRY#*/}"
      DETECTED_CLUSTERS+=("$C_NAME")
    done
    ALLOWED_CLUSTER_NAMES=$(IFS=','; echo "${DETECTED_CLUSTERS[*]}")
    echo "Allowed cluster names: $ALLOWED_CLUSTER_NAMES"
  else
    echo "Allowed cluster names: (any EKS cluster — no clusters were selected)"
  fi
  export ALLOWED_CLUSTER_NAMES
fi

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

# Get Cognito Client Secret
echo "Retrieving Cognito Client Secret..."
if [ "$USER_POOL_ID" != "NOT_FOUND" ] && [ "$CLIENT_ID" != "NOT_FOUND" ]; then
  CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-id "$CLIENT_ID" \
    --region "$REGION" \
    --query "UserPoolClient.ClientSecret" \
    --output text 2>/dev/null || echo "NOT_FOUND")
else
  CLIENT_SECRET="NOT_FOUND"
fi

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
echo "│ $CLIENT_SECRET"
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
# EKS Node Log MCP - DevOps Agent Configuration
# Generated: $(date)
# Stack: $STACK_NAME
# Region: $REGION

MCP_SERVER_URL=$GATEWAY_URL
OAUTH_CLIENT_ID=$CLIENT_ID
OAUTH_CLIENT_SECRET=$CLIENT_SECRET
TOKEN_URL=$TOKEN_URL
OAUTH_SCOPE=$OAUTH_SCOPE
LOGS_BUCKET=$LOGS_BUCKET

# Security Scoping
ALLOWED_REGIONS=${ALLOWED_REGIONS:-$REGION}
ALLOWED_CLUSTER_NAMES=${ALLOWED_CLUSTER_NAMES:-(any EKS cluster)}
ALLOWED_SSM_DOCUMENTS=${ALLOWED_SSM_DOCUMENTS:-AWS-RunShellScript}
PRESIGNED_URL_EXPIRATION=${PRESIGNED_URL_EXPIRATION:-300}
ENABLED_RESTRICTED_TOOLS=${ENABLED_RESTRICTED_TOOLS:-(none — tcpdump disabled)}
EOF

echo "Configuration saved to: $CONFIG_FILE"
echo ""
echo "=============================================="
echo "SECURITY SCOPING"
echo "=============================================="
echo ""
echo "  Allowed Regions:        ${ALLOWED_REGIONS:-$REGION}"
echo "  Allowed Clusters:       ${ALLOWED_CLUSTER_NAMES:-(any EKS cluster)}"
echo "  Allowed SSM Documents:  ${ALLOWED_SSM_DOCUMENTS:-AWS-RunShellScript}"
echo "  Presigned URL Expiry:   ${PRESIGNED_URL_EXPIRATION:-300}s (max 900s)"
echo "  Restricted Tools:       ${ENABLED_RESTRICTED_TOOLS:-(none — tcpdump not available)}"
echo ""
echo "  IAM enforces: ssm:SendCommand only on instances tagged with the"
echo "  selected cluster names, in the allowed regions, using the allowed"
echo "  SSM documents. tcpdump tools are removed from the routing table"
echo "  unless explicitly enabled via ENABLED_RESTRICTED_TOOLS."
echo ""
echo "=============================================="
echo "AVAILABLE MCP TOOLS (20)"
echo "=============================================="
echo ""
echo "TIER 1 — CORE OPERATIONS"
echo "  collect              Start log collection with idempotency"
echo "  status               Get collection status with progress tracking"
echo "  validate             Verify all expected files were extracted"
echo "  errors               Get pre-indexed error findings (fast path)"
echo "  read                 Byte-range streaming for multi-GB files"
echo ""
echo "TIER 2 — ADVANCED ANALYSIS"
echo "  search               Full-text regex search across all logs"
echo "  correlate            Cross-file timeline correlation"
echo "  artifact             Secure presigned URLs for large artifacts"
echo "  summarize            AI-ready structured incident summary"
echo "  quick_triage         Rapid node health assessment"
echo "  history              Audit trail of past collections"
echo ""
echo "TIER 3 — CLUSTER INTELLIGENCE"
echo "  cluster_health       Cluster-wide node health overview"
echo "  compare_nodes        Compare healthy vs unhealthy nodes"
echo "  batch_collect        Batch log collection with sampling"
echo "  batch_status         Batch collection status"
echo "  network_diagnostics  Network stack analysis (CNI, iptables, DNS)"
echo "  storage_diagnostics  Storage/volume/CSI analysis"
echo ""
echo "TIER 4 — LIVE CAPTURE (disabled by default)"
echo "  tcpdump_capture      Live packet capture via SSM"
echo "  tcpdump_analyze      Analyze captured pcap files"
echo "  NOTE: These tools are removed from the routing table unless"
echo "        ENABLED_RESTRICTED_TOOLS includes them."
echo ""
echo "TIER 5 — SOPs"
echo "  list_sops            List available runbooks"
echo "  get_sop              Retrieve a specific runbook"
echo ""
echo "=============================================="
echo "EXAMPLE PROMPT FOR DEVOPS AGENT"
echo "=============================================="
echo ""
echo "\"I'm investigating a node issue on i-0123456789abcdef0."
echo " Collect logs, find any critical errors, and give me a summary.\""
echo ""
