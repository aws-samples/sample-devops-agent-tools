#!/bin/bash
CLUSTER_ID="ams-mcp-test-km-2"
REGION="us-east-1"

echo "=========================================="
echo "Aurora Cluster Diagnostic Report"
echo "=========================================="
echo ""

echo "1. Checking if cluster exists..."
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --region $REGION \
  --query 'DBClusters[0].[DBClusterIdentifier,Status]' \
  --output text 2>/dev/null

if [ $? -ne 0 ]; then
  echo "❌ ERROR: Cluster '$CLUSTER_ID' not found in $REGION"
  echo ""
  echo "Available clusters:"
  aws rds describe-db-clusters \
    --region $REGION \
    --query 'DBClusters[*].DBClusterIdentifier' \
    --output text
  exit 1
fi

echo "✅ Cluster found"
echo ""

echo "2. Getting cluster endpoints..."
WRITER_ENDPOINT=$(aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --region $REGION \
  --query 'DBClusters[0].Endpoint' \
  --output text)

READER_ENDPOINT=$(aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --region $REGION \
  --query 'DBClusters[0].ReaderEndpoint' \
  --output text)

PORT=$(aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --region $REGION \
  --query 'DBClusters[0].Port' \
  --output text)

echo "   Writer Endpoint: $WRITER_ENDPOINT"
echo "   Reader Endpoint: $READER_ENDPOINT"
echo "   Port: $PORT"
echo ""

echo "3. Testing DNS resolution..."
if host $WRITER_ENDPOINT > /dev/null 2>&1; then
  echo "✅ Writer endpoint resolves to: $(host $WRITER_ENDPOINT | awk '{print $NF}')"
else
  echo "❌ ERROR: Cannot resolve writer endpoint"
fi

if host $READER_ENDPOINT > /dev/null 2>&1; then
  echo "✅ Reader endpoint resolves to: $(host $READER_ENDPOINT | awk '{print $NF}')"
else
  echo "❌ ERROR: Cannot resolve reader endpoint"
fi
echo ""

echo "4. Testing network connectivity..."
if nc -zv -w 5 $WRITER_ENDPOINT $PORT 2>&1 | grep -q succeeded; then
  echo "✅ Port $PORT is reachable on writer endpoint"
else
  echo "❌ ERROR: Cannot connect to port $PORT on writer endpoint"
  echo "   Check security group rules and network ACLs"
fi
echo ""

echo "5. Cluster configuration summary..."
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --region $REGION \
  --query 'DBClusters[0].{
    Engine: Engine,
    EngineVersion: EngineVersion,
    DatabaseName: DatabaseName,
    PubliclyAccessible: PubliclyAccessible,
    SecurityGroups: VpcSecurityGroups[*].VpcSecurityGroupId
  }' \
  --output table

echo ""
echo "=========================================="
echo "✅ CORRECT CONFIG FOR config.yaml:"
echo "=========================================="
echo "primary:"
echo "  hostname: \"$WRITER_ENDPOINT\""
echo "  port: $PORT"
echo ""
echo "replica:"
echo "  hostname: \"$READER_ENDPOINT\""
echo "  port: $PORT"
echo "=========================================="
