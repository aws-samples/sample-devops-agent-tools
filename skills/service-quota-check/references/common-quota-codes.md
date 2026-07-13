# Common Quota Codes Reference

This reference lists frequently checked quota codes for common AWS services. Use these
codes with `get-service-quota` and `request-service-quota-increase`.

## Amazon EC2

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances | L-1216C47A | 5 vCPU | vCPU |
| Running On-Demand G and VT instances | L-DB2E81BA | 0 vCPU | vCPU |
| Running On-Demand P instances | L-417A185B | 0 vCPU | vCPU |
| Running On-Demand Inf instances | L-B5D1601B | 0 vCPU | vCPU |
| Running Dedicated Standard (A, C, D, H, I, M, R, T, Z) Hosts | L-20F13EBD | 0 | None |
| EC2-VPC Elastic IPs | L-0263D0A3 | 5 | None |
| Public AMIs | L-0E3CBAB9 | 25 | None |

## Amazon VPC

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| VPCs per Region | L-F678F1CE | 5 | None |
| Subnets per VPC | L-407747CB | 200 | None |
| Internet gateways per Region | L-A4707A72 | 5 | None |
| NAT gateways per Availability Zone | L-FE5A380F | 5 | None |
| Network interfaces per Region | L-DF5E4CA3 | 5000 | None |
| Security groups per Region | L-E79EC296 | 2500 | None |
| Routes per route table | L-93826ACB | 50 | None |
| Route tables per VPC | L-589F43AA | 200 | None |

## Elastic Load Balancing

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Application Load Balancers per Region | L-53DA6B97 | 50 | None |
| Network Load Balancers per Region | L-69A177A2 | 50 | None |
| Target groups per Region | L-B6DF7632 | 3000 | None |
| Targets per Application Load Balancer | L-7E6692B2 | 1000 | None |
| Listeners per Application Load Balancer | L-B6DF7632 | 50 | None |

## Amazon RDS

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| DB instances | L-7B6409FD | 40 | None |
| DB clusters | L-952B80B8 | 40 | None |
| Read replicas per primary | L-5480080B | 5 | None |
| Manual DB instance snapshots | L-272F1212 | 100 | None |
| Total storage for all DB instances (GiB) | L-7ADDB58A | 100000 | GiB |

## AWS Lambda

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Concurrent executions | L-B99A9384 | 1000 | None |
| Function and layer storage | L-2ACBD22F | 75 GB | Gigabytes |
| Elastic network interfaces per VPC | L-9FEE3D26 | 250 | None |

## Amazon ECS

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Clusters per account | L-21C621EB | 10000 | None |
| Services per cluster | L-9A2EAEDE | 5000 | None |
| Tasks per service | L-EE04B13E | 5000 | None |
| Container instances per cluster | L-21C621EB | 5000 | None |

## AWS Fargate

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Fargate On-Demand resource count | L-790F3D0E | 500 | None |
| Fargate Spot resource count | L-36FBB829 | 500 | None |

## Amazon DynamoDB

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Maximum number of tables | L-F98FE922 | 2500 | None |
| Account-level read throughput limit (on-demand, per region) | L-B5A90E5F | 40000 | RCU |
| Account-level write throughput limit (on-demand, per region) | L-4CF20C20 | 40000 | WCU |

## AWS CloudFormation

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Stack count | L-0485CB21 | 2000 | None |
| Stack sets per administrator account | L-EC62D81A | 100 | None |
| Resources per stack | L-844E580A | 500 | None |

## Amazon S3

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Buckets | L-DC2B2D3D | 100 | None |

## Amazon SNS

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Topics per account | L-61103206 | 100000 | None |
| Subscriptions per topic | L-A4340BCD | 12500000 | None |

## Amazon SQS

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Queues per account | L-2BFD9882 | 1000000 | None |

## Auto Scaling

| Quota Name | Quota Code | Default | Unit |
|-----------|-----------|---------|------|
| Auto Scaling groups per region | L-CDE20ADC | 200 | None |
| Launch configurations per region | L-6B80B8FA | 200 | None |

---

## Notes

- Default values shown are the AWS defaults. Your account may have different applied
  values if previous increases were granted.
- Quota codes are stable identifiers that do not change, but new quotas may be added
  over time.
- Use `list-service-quotas` to get the most current list for any service.
- Some quotas listed here may be resource-level (not account-level) in newer API versions.
