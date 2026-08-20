# EKS Upgrade Capacity Planning

This reference covers capacity planning for EKS node group upgrades,
including surge node calculations and Capacity Reservation strategies.

## Surge Node Calculation

This follows the actual Amazon EKS managed node group update algorithm
(see [Understand each phase of node updates](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-update-behavior.html)),
not a simplified per-AZ percentage model. There are four phases:

1. **Setup** — a new launch template version is created and applied to the
   ASG. `updateConfig` (`maxUnavailable` or `maxUnavailablePercentage`, capped
   at 100 nodes) determines how many nodes can be replaced in parallel.
2. **Scale up** — the ASG's max and desired size are incremented **before**
   any old node is touched, so capacity never drops during the upgrade
   (default strategy). New nodes land in the same AZs as the nodes they
   replace, using EC2 Auto Scaling Availability Zone Rebalancing.
3. **Upgrade** — old nodes are cordoned once a replacement is `Ready`,
   drained (15-minute timeout, `PodEvictionFailure` without `--force`), then
   terminated after a 60-second post-eviction wait. This repeats in batches
   of `maxUnavailable` until every node runs the new launch template version.
4. **Scale down** — the ASG max/desired size is decremented back to the
   pre-upgrade value once the rollout completes (skipped if Cluster
   Autoscaler is actively scaling the group at that moment).

### Formula

The scale-up increment is **not** a per-AZ percentage of existing nodes — it
is the larger of two values, applied once to the whole ASG:

```
maxUnavailable_count = min(100, maxUnavailable OR ceil(desired_size * maxUnavailablePercentage / 100))
Total surge at peak  = max(2 * number_of_azs, maxUnavailable_count)
```

Because EKS guarantees at least one new node per AZ where old nodes exist
(and up to two per AZ to satisfy AZ Rebalancing), a node group spread across
many AZs can surge by more than `maxUnavailable` even when `maxUnavailable`
is small — plan capacity for `2 * numAZs`, not just `maxUnavailable`.

### Examples (3 AZs)

| Desired Size | updateConfig | maxUnavailable_count | Surge at Peak (max of 2×AZ, maxUnavailable) |
|--------------|-------------|----------------------|----------------------------------------------|
| 15 | maxUnavailable: 1 | 1 | 6 (2×3 AZ dominates) |
| 15 | maxUnavailable: 10 | 10 | 10 (maxUnavailable dominates) |
| 30 | maxUnavailablePercentage: 20% | 6 | 6 (maxUnavailable dominates) |
| 150 | maxUnavailablePercentage: 33% | 50 | 50 (maxUnavailable dominates) |

Use `--force` awareness when planning timelines: if `PodEvictionFailure`
occurs (aggressive PDBs, taint-tolerant deployments), the batch stalls at
the 15-minute drain timeout until an operator intervenes — factor this into
maintenance-window sizing, don't assume `force` is used automatically (it
requires an explicit operator-approved `update-nodegroup-version --force`).

## Capacity Reservation Strategies

For large clusters or instance types with limited availability,
use EC2 Capacity Reservations to guarantee surge capacity.

### On-Demand Capacity Reservations (ODCR)

- Immediate availability, billed whether used or not
- Best for: short upgrade windows where you want guaranteed capacity
- Create just before upgrade, cancel immediately after

### Flexible Duration Capacity Reservations (FDCR)

- Scheduled future capacity, minimum 24-hour duration
- Best for: planned upgrades with known schedules
- Create days in advance, auto-activate at scheduled time

### Targeting Strategies

| Strategy | How It Works | When to Use |
|----------|-------------|-------------|
| Open match | Any instance in the AZ consumes slots | Single workload in the AZ |
| Targeted + Resource Group | Only ASG instances consume slots | Multiple workloads in same AZ |

### Resource Group + ASG Targeting (Recommended)

```bash
# 1. Create resource group
aws resource-groups create-group \
  --name eks-upgrade-capacity \
  --configuration \
    '{"Type":"AWS::EC2::CapacityReservationPool"}' \
    '{"Type":"AWS::ResourceGroups::Generic","Parameters":[{"Name":"allowed-resource-types","Values":["AWS::EC2::CapacityReservation"]}]}'

# 2. Add CRs to group
aws resource-groups group-resources \
  --group eks-upgrade-capacity \
  --resource-arns arn:aws:ec2:<region>:<account>:capacity-reservation/<cr-id>

# 3. Configure ASG to target the group
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name <asg-name> \
  --capacity-reservation-specification \
    '{"CapacityReservationTarget":{"CapacityReservationResourceGroupArn":"arn:aws:resource-groups:<region>:<account>:group/eks-upgrade-capacity"}}'
```

### Important Notes

- FDCRs start as "targeted" — must switch to "open" after activation OR use resource group
- Cannot modify instance eligibility while instances are consuming the reservation
- If using "open" match, other workloads with the same instance type in the AZ may consume slots
- Calculate reservation size as: existing nodes + surge nodes (all get replaced during rolling update)

## When NOT to Use Capacity Reservations

- Instance types with broad availability (t3, m5, m6i in major regions)
- Small clusters (< 10 nodes) where InsufficientCapacity is unlikely
- Clusters using diversified instance types (Karpenter with multiple types)
- Spot-based node groups (CRs are for On-Demand only)

## Troubleshooting Capacity Issues During Upgrade

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| `InsufficientInstanceCapacity` during upgrade | AZ lacks capacity for instance type | Use FDCR or switch to open CR match |
| CR shows "Available: 0" but no instances running | Other workloads consumed open CR slots | Switch to targeted + resource group |
| ASG not consuming targeted CR | Launch template missing CR specification | Use resource group targeting on ASG instead |
| FDCR not activating | Still in "Scheduled" state | Wait until start time; cannot modify while scheduled |
