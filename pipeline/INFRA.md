# STEAM-v1 → Zarr conversion: AWS infrastructure request

*What to ask infra for, why, and what it will cost. Measured 2026-09-18; see
"How these numbers were measured" at the end.*

## The job in one paragraph

Convert 7 712 public bigwig files (241 Zoonomia species × 32 cell classes,
hosted by the Shendure lab at `shendure-web.gs.washington.edu`) into one
pre-binned, pre-normalised Zarr store in our S3 bucket, so the interactive Fig 6e
widget can read any locus in ~1 s from a static web page. Each track is read
over HTTPS in 20 Mb windows, averaged into 100 bp bins, mapped to the paper's
genome-wide Phred (GPS) scale, quantised to `uint8`, and written into a
`(241 species × 30.9 M bins)` array per cell class. Output: ~60 GB in
`s3://cn-scms-datastore/csev-steam-1/data/steam_v1_gps.zarr/`. The job is
resumable at chromosome granularity and idempotent, so spot interruption is fine.

## What is needed

| Item | Request | Why |
|---|---|---|
| **EC2 instance** | **`c6i.4xlarge`** (16 vCPU, 32 GB RAM, up to 12.5 Gbps), **us-east-1** (same region as the bucket) | The work is bound by parallel HTTPS range reads from the Shendure server, not CPU: ~20 CPU-s per track vs 3.4 min of network wait. 48–64 I/O-bound worker processes on 16 vCPUs saturate what a remote server will serve. Same region as the bucket → no transfer charge on upload. |
| **Purchase option** | Spot acceptable (~70 % cheaper); on-demand fine | The job checkpoints per chromosome and per species; an interruption costs at most the chromosome in flight (~10 min). |
| **Storage** | **200 GB gp3 EBS** root/data volume | Peak local use ≈ 60 GB final store + one chromosome's working cache (< 1 GB) + logs; 200 GB is 3× headroom. gp3 baseline IOPS is ample. |
| **AMI** | Amazon Linux 2023 or Ubuntu 24.04 (x86_64) | Python 3.11+, `pyBigWig` wheels available. |
| **IAM role** (instance profile) | See policy below: read/write to the `csev-steam-1/` prefix only, plus SSM Session Manager | Upload the store; no long-lived keys on the box. |
| **Network** | Outbound HTTPS (443) to `shendure-web.gs.washington.edu` and to S3; **no inbound** if SSM is used (else SSH 22 from the operator's IP) | |
| **Duration** | Provision for **3 days**; expected active run **10–20 h** | Leaves room for a benchmark run, a throttled server, and a re-run of a cell class. |
| **Tags / budget** | `Project=csev-steam`, `Owner=<you>`; alert at $100 | Expected total < $50. |

### Alternatives, if the default is not available

| Instance | vCPU / RAM | On-demand | When |
|---|---|---|---|
| `c6i.2xlarge` | 8 / 16 GB | $0.34/h | Minimum viable; run 32 workers. ~1.5× longer. |
| **`c6i.4xlarge`** | **16 / 32 GB** | **$0.68/h** | **Default.** 48–64 workers. |
| `c6i.8xlarge` | 32 / 64 GB | $1.36/h | Only if the benchmark shows the Shendure server still scales past 64 connections. |
| `c6in.4xlarge` | 16 / 32 GB, 50 Gbps | $0.91/h | Not needed — our NIC is not the bottleneck. |

Any current-generation x86 family (c7i, m6i, m7i) is equivalent; memory need is
< 8 GB even at 64 workers.

**EC2 network limits are not a factor.** Instances below `.8xlarge` have a
*baseline* bandwidth well under their "up to" burst figure (c6i.4xlarge:
6.25 Gbps baseline, 12.5 burst; c6i.xlarge: 1.56 baseline), but this job
moves 2.2 TB over 10–35 h — 17–60 MB/s, 0.14–0.5 Gbps, i.e. 2–8 % of the
c6i.4xlarge baseline and still under a c6i.xlarge's. Per-flow caps (≥ 5 Gbps)
are irrelevant to connections running at ~1.4 MB/s each, and inbound data from
the internet is neither metered nor charged. The only throttle that matters is
the Shendure server's, which `bench` measures. The 4xlarge is recommended for
worker headroom (64 processes), not bandwidth; a 2xlarge would finish in the
same time if the server is the limit.

## Time and cost estimate

Measured single-connection throughput: **0.066 s per Mb of genome** (20 Mb
window of chr1 in 1.3 s), i.e. 3.4 min per track, ~290 MB transferred per track.

| Parallel connections | If the server scales linearly | Likely (some throttling) |
|---|---|---|
| 16 | 27 h | 30–40 h |
| 32 | 14 h | 15–25 h |
| **48** | **9 h** | **10–20 h** |
| 64 | 7 h | 8–20 h |

**Measured on the instance (c6i.4xlarge, 2026-09-18):** the Shendure server
keeps serving ~10 Mb/s per connection all the way up:

| Workers | Aggregate | Per connection | Projected total |
|---|---|---|---|
| 16 | 113 Mb/s (11 MB/s) | 10.3 Mb/s | 59 h |
| 48 | 408 Mb/s (38 MB/s) | 10.0 Mb/s | 16 h |
| **96** | **780 Mb/s (73 MB/s)** | 9.9 Mb/s | **8.5 h** |

So run with `--workers 96` (≈ 12 GB RAM, ~5 cores busy): **all 32 cell classes
in ≈ 8–10 h, one cell class every ~16 min.** The 35 h laptop-based worst case
did not materialise.

| Cost item | Estimate |
|---|---|
| Compute, c6i.4xlarge on-demand, ~10 h | $7 |
| Compute, spot | ~$2–3 |
| EBS 200 GB gp3, 3 days | $2 |
| Data transfer in (Shendure → EC2, 2.2 TB) | $0 (inbound is free) |
| EC2 → S3 upload, same region, 60 GB | $0 |
| S3 storage, 60 GB, per month | $1.40 |
| S3 requests + egress serving the widget, per month | < $1 at expected traffic (chunks are ~150 KB; 1 000 page views ≈ 1 GB) |
| **Total for the conversion** | **≈ $10** |

## IAM policy for the instance role

Attach to a role with the EC2 trust policy; also attach the AWS managed
`AmazonSSMManagedInstanceCore` so the operator can open a shell with Session
Manager instead of SSH.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::cn-scms-datastore",
      "Condition": { "StringLike": { "s3:prefix": ["csev-steam-1/*", "csev-steam-1"] } }
    },
    {
      "Sid": "ReadWritePrefix",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": "arn:aws:s3:::cn-scms-datastore/csev-steam-1/*"
    }
  ]
}
```

`DeleteObject` is needed so a re-run of a cell class can replace its chunks;
scope stays inside the prefix.

### Attaching the role to the running instance (2026-09-18 status)

The instance exists — `i-01215d9f9b6020357`, c6i.4xlarge, us-east-1, account
`166088433508`, Ubuntu — and the software is installed and benchmarked, but it
has **no IAM role**, so `aws s3` on it reports *Unable to locate credentials*
and nothing can be uploaded. Whoever has IAM rights in that account runs the
following once (no restart needed; the role takes effect within a minute):

```bash
# from a machine with admin credentials for account 166088433508
cd pipeline
aws iam create-role --role-name steam-zarr-writer --assume-role-policy-document file://ec2-trust.json
aws iam put-role-policy --role-name steam-zarr-writer --policy-name csev-steam-1-rw --policy-document file://iam-policy.json
aws iam attach-role-policy --role-name steam-zarr-writer --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name steam-zarr-writer
aws iam add-role-to-instance-profile --instance-profile-name steam-zarr-writer --role-name steam-zarr-writer
sleep 10   # instance profiles take a few seconds to become usable
aws ec2 associate-iam-instance-profile --instance-id i-01215d9f9b6020357 \
    --iam-instance-profile Name=steam-zarr-writer --region us-east-1
```

Console equivalent: IAM → Roles → Create role (EC2) with the two policies,
then EC2 → the instance → Actions → Security → **Modify IAM role** → pick it.

Verify on the instance: `aws sts get-caller-identity` shows the role, and
`aws s3 ls s3://cn-scms-datastore/csev-steam-1/data/` lists the existing
store.

### How the instance reaches the bucket

No keys are copied anywhere. The role is attached to the instance as an
*instance profile*; EC2 then serves short-lived, auto-rotating credentials for
it through the instance metadata service, and the AWS CLI (which
`steam-zarr … --upload` shells out to as `aws s3 sync`) picks them up with no
`aws configure` step. Two things to confirm with infra:

- **Same account?** If `cn-scms-datastore` lives in a different AWS account
  from the instance, the role policy alone is not enough: the bucket also needs
  a bucket policy granting the role's ARN the same actions.
- **Public read of the results.** The widget reads the store anonymously from a
  web page, so the new objects under `csev-steam-1/data/` must be publicly
  readable exactly like the existing ones there (bucket policy / Block Public
  Access as already configured — with "bucket owner enforced" ownership, new
  objects inherit it automatically).

Fallback if a role cannot be granted: an IAM user with the same policy and an
access key, set with `aws configure` on the instance — works identically but is
a long-lived secret to delete afterwards.

## Text you can paste into the request

> I need a Linux EC2 instance in us-east-1 for a one-off, ~1-day data
> conversion job (reading ~2 TB of public genomics files over HTTPS and
> writing ~60 GB into our existing bucket `cn-scms-datastore` under
> `csev-steam-1/`).
>
> - Instance: c6i.4xlarge (16 vCPU / 32 GB), spot is fine, Amazon Linux 2023
>   or Ubuntu 24.04, 200 GB gp3 volume.
> - IAM instance role: S3 list/get/put/delete limited to
>   `arn:aws:s3:::cn-scms-datastore/csev-steam-1/*` (policy attached) plus
>   `AmazonSSMManagedInstanceCore` so I can use Session Manager — no SSH key or
>   inbound rule needed.
> - Security group: outbound 443 only.
> - The bucket is [in this account / in account X — please add a bucket
>   policy for the role]. New objects written under `csev-steam-1/data/` must
>   be publicly readable, like the existing ones there.
> - Please tag `Project=csev-steam` and set a $100 budget alert; expected
>   spend is about $20. I will terminate it when done (within 3 days).
>
> The job is resumable, so a spot interruption is not a problem.

## Operator checklist (once the instance exists)

1. Connect: `aws ssm start-session --target <instance-id>` (or SSH).
2. `git clone` this repo (or copy the `pipeline/` folder) and run
   `pipeline/setup.sh` — installs Python deps into a venv (~2 min).
3. `steam-zarr bench --workers 96` — throughput test against the Shendure
   server; it prints the projected hours at that worker count (measured: 8.5 h).
4. `nohup steam-zarr build --all --workers 96 --out /data/steam_v1_gps.zarr
   --upload s3://cn-scms-datastore/csev-steam-1/data/ > build.log 2>&1 &`
   — builds cell class by cell class, syncing each to S3 as it completes, so
   the widget can use finished cell classes while the rest run.
5. `steam-zarr status --out /data/steam_v1_gps.zarr` at any time shows
   chromosomes done per cell class and the projected finish.
6. When `status` reports 32/32 complete: verify with `steam-zarr verify`
   (spot-checks values against a fresh read of the source), then terminate
   the instance. The bucket keeps everything.

Full commands and options: `pipeline/README.md`.

## How these numbers were measured

- Reading path: `pyBigWig.values(chrom, start, end, numpy=True)` over 20 Mb
  windows, then `reshape(-1, 100).nanmean(axis=1)`. This gives results
  identical to the `bw.stats(nBins=…)` call the Streamlit app uses (max
  difference 5 × 10⁻⁶, identical missing pattern) at **8× the speed and 1/50
  the CPU** — the `stats` path is what made the earlier laptop build take 46
  minutes per chromosome.
- chr21 (46.7 Mb) of one track: 2.5 s wall, 0.3 s CPU, 4.4 MB transferred.
  chr1 20 Mb window: 1.3 s, 80 MB array, 125 MB peak RSS.
- Extrapolation: 3 088 Mb per track × 0.066 s/Mb = 3.4 min; × 7 712 tracks =
  437 connection-hours; 0.094 MB per Mb → 2.2 TB total transfer.
- Output size: 4.1× gzip on `uint8` GPS measured across chromosomes and
  species → 7.5 GB → ~1.8 GB per cell class, ~60 GB total (`data-analysis/`).
