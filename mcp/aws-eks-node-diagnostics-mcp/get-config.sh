#!/bin/bash
# EKS Node Log MCP - Get Configuration Values
# Run this after deployment to retrieve all configuration values

STACK_NAME="${1:-SsmAutomationGatewayStack}"
REGION="${AWS_REGION:-us-east-1}"

if [ ! -f cdk-outputs.json ]; then
  echo "Error: cdk-outputs.json not found. Run ./deploy.sh first."
  exit 1
fi

# Parse values from cdk-outputs.json
GATEWAY_URL=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'GatewayUrl' in k][0])" 2>/dev/null)
CLIENT_ID=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'CognitoClientId' in k][0])" 2>/dev/null)
USER_POOL_ID=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'CognitoUserPoolId' in k][0])" 2>/dev/null)
TOKEN_URL=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'OAuthExchangeUrl' in k][0])" 2>/dev/null)
OAUTH_SCOPE=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'OAuthScope' in k][0])" 2>/dev/null)
LOGS_BUCKET=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print([v for k,v in d.get('$STACK_NAME',{}).items() if 'LogsBucketName' in k][0])" 2>/dev/null)

# Get Cognito Client Secret
CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$CLIENT_ID" \
  --region "$REGION" \
  --query "UserPoolClient.ClientSecret" \
  --output text 2>/dev/null)

echo ""
echo "=============================================="
echo "DEVOPS AGENT MCP SERVER CONFIGURATION"
echo "=============================================="
echo ""
echo "MCP Server URL:"
echo "  $GATEWAY_URL"
echo ""
echo "OAuth Client ID:"
echo "  $CLIENT_ID"
echo ""
echo "OAuth Client Secret:"
echo "  $CLIENT_SECRET"
echo ""
echo "Token URL:"
echo "  $TOKEN_URL"
echo ""
echo "Scope (use only ONE):"
echo "  $OAUTH_SCOPE"
echo ""
echo "Logs Bucket:"
echo "  $LOGS_BUCKET"
echo ""
echo "=============================================="
