# AWS Pricing Reference

This file contains all AWS-specific Pricing API call patterns. Read this file the first time any operation is classified PAID by Layer 2, before estimating cost.

---

## Standard Pattern 

Always call the Pricing API from `us-east-1` regardless of workload region:

```bash
aws pricing get-products \
  --service-code <ServiceCode> \
  --filters '[{"Type":"TERM_MATCH","Field":"operation","Value":"<OperationName>"},
              {"Type":"TERM_MATCH","Field":"regionCode","Value":"<workload-region>"}]' \
  --region us-east-1
```

Use the **operation name from Layer 2** directly as the filter value. Use the **workload region** (from the resource ARN or `aws_region` param) as `regionCode` — never the agent space region.


---

## Cross-Region Data Transfer Rate

For any paid operation where `target_region ≠ agent_space_region`, fetch the live transfer rate. The rate is **source-region-based and destination-independent**:

```bash
aws pricing get-products \
  --service-code AWSDataTransfer \
  --filters '[{"Type":"TERM_MATCH","Field":"transferType","Value":"InterRegion Outbound"},
              {"Type":"TERM_MATCH","Field":"fromRegionCode","Value":"<source-region>"},
              {"Type":"TERM_MATCH","Field":"toLocationType","Value":"AWS Region"}]' \
  --max-results 1 \
  --region us-east-1
```

Read `pricePerUnit.USD` from the first result. Cache as `transfer_rate_cache[source_region]` — one lookup per source region per investigation.

---

## S3 Pricing Lookup

S3 has no `operation` field — use `usagetype` with the region prefix and tier:

```bash
aws pricing get-products \
  --service-code AmazonS3 \
  --filters '[{"Type":"TERM_MATCH","Field":"usagetype","Value":"<PREFIX>-Requests-<TIER>"}]' \
  --region us-east-1
```

Replace `<TIER>` with `Tier1` or `Tier2` based on the operation. For us-east-1 omit the prefix entirely (e.g. `Requests-Tier1`).

### S3 Tier Mapping

| Tier | Operations |
|---|---|
| `Tier1` | PUT, COPY, POST, LIST |
| `Tier2` | GET, SELECT, HEAD |

### Region Prefix Mapping

| Region | Prefix |
|---|---|
| us-east-1 | *(omit — bare `Requests-Tier1`)* |
| us-east-2 | USE2 |
| us-west-1 | USW1 |
| us-west-2 | USW2 |
| eu-west-1 | EU |
| eu-west-2 | EUW2 |
| eu-west-3 | EUW3 |
| eu-central-1 | EUC1 |
| eu-central-2 | EUC2 |
| eu-north-1 | EUN1 |
| eu-south-1 | EUS1 |
| ap-southeast-1 | APS1 |
| ap-southeast-2 | APS2 |
| ap-southeast-3 | APS4 |
| ap-southeast-4 | APS6 |
| ap-northeast-1 | APN1 |
| ap-northeast-2 | APN2 |
| ap-northeast-3 | APN3 |
| ap-south-1 | APS3 |
| ap-east-1 | APE1 |
| sa-east-1 | SAE1 |
| ca-central-1 | CAN1 |
| me-south-1 | MES1 |
| me-central-1 | MEC1 |
| af-south-1 | AFS1 |
| il-central-1 | ILC1 |

---

## Reference Links

[CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) · [X-Ray](https://aws.amazon.com/xray/pricing/) · [Athena](https://aws.amazon.com/athena/pricing/) · [DynamoDB](https://aws.amazon.com/dynamodb/pricing/on-demand/) · [S3](https://aws.amazon.com/s3/pricing/) · [Kinesis](https://aws.amazon.com/kinesis/data-streams/pricing/) · [SQS](https://aws.amazon.com/sqs/pricing/) · [Lambda](https://aws.amazon.com/lambda/pricing/) · [Resource Explorer](https://aws.amazon.com/resource-explorer/pricing/) · [Data Transfer](https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer)
